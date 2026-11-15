from __future__ import annotations

import asyncio
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlencode

import httpx

_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
]
_WIKI_UA = "GEMMA-TUI/0.1 (local research assistant; uses Wikipedia API politely)"
_MAX_RESULTS = 6
_DDG_ROOT = "https://html.duckduckgo.com"
_DDG_LITE_ROOT = "https://lite.duckduckgo.com"

# Time-sensitive questions: SearXNG gets a recency window and the news category.
_FRESH_RE = re.compile(
    r"\b(latest|current|newest|breaking|news|today|tonight|recent|just|now|"
    r"this (week|month|year)|yesterday)\b",
    re.IGNORECASE,
)
# Page deep-read: top pages, fetched in parallel under one overall timeout.
_DEEP_READ_PAGES = 2
_DEEP_READ_TIMEOUT = 8.0
_EXCERPT_CHARS = 400
# Context sent to the model: ~250 tokens (≈4 chars/token). The local
# llama-server prefills at ~20 tok/s, so context size is the whole turn's
# latency budget; the cap stays well under the ~2000-token ceiling.
_CONTEXT_CHAR_BUDGET = 1000


def _extract_redirect_url(href: str) -> str:
    """Resolve a DuckDuckGo redirect URL to its real target."""
    if not href:
        return ""
    match = re.search(r"[?&]uddg=([^&]+)", href)
    if match:
        candidate = unquote(match.group(1))
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate
    if href.startswith("//"):
        return "https:" + href
    return href


class _DdgPgParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set(dict(attrs).get("class", "").split())
        href = dict(attrs).get("href") or ""
        if tag == "a":
            if "result__a" in classes:
                self._current = {"title": "", "snippet": "", "url": _extract_redirect_url(href)}
                self._capture = "title"
                self._buf = []
            elif "result__snippet" in classes and self._current is not None:
                self._capture = "snippet"
                self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture and self._current is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._capture is None or self._current is None:
            return
        self._current[self._capture] = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if self._capture == "title" and self._current.get("url"):
            # The result joins the list here; the snippet anchor may still
            # update it afterwards (same dict object).
            self.results.append(self._current)
        self._capture = None


class _DdgLiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_result = False
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set(attr.get("class", "").split())
        if tag == "tr" and "result" in classes:
            self._in_result = True
            self._current = {"title": "", "snippet": "", "url": ""}
        elif self._in_result and tag == "a" and "result-link" in classes:
            self._capture = "title"
            self._buf = []
            if self._current is not None:
                self._current["url"] = _extract_redirect_url(attr.get("href") or "")
        elif self._in_result and tag == "td" and "result-snippet" in classes:
            self._capture = "snippet"
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture and self._current is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._in_result:
            self._in_result = False
            if self._current is not None:
                result = self._current
                self._current = None
                if result.get("title"):
                    self.results.append(result)
            self._capture = None
        elif tag in ("a", "td") and self._capture and self._current is not None:
            self._current[self._capture] = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self._capture = None


class _SearxngHtmlParser(HTMLParser):
    """Parses the SearXNG web UI (HTML) — used when the JSON API is disabled."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = attr.get("class") or ""
        if tag == "article" and "result" in classes.split():
            self._current = {"title": "", "url": "", "snippet": "", "engine": "searxng", "date": ""}
        elif self._current is not None:
            if tag == "a" and attr.get("href", "").startswith("http"):
                url = attr["href"]
                if "/image_proxy" not in url and not self._current["url"]:
                    self._current["url"] = url
                if self._capture == "title":
                    return
            elif tag == "h3" and not self._current["title"]:
                self._capture = "title"
                self._buf = []
            elif tag == "p" and "content" in classes.split() and not self._current["snippet"]:
                self._capture = "snippet"
                self._buf = []
            elif tag == "time" and attr.get("datetime"):
                self._current["date"] = str(attr["datetime"])[:10]

    def handle_data(self, data: str) -> None:
        if self._capture and self._current is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            if self._current is not None and self._current.get("url") and self._current.get("title"):
                self.results.append(self._current)
            self._current = None
            self._capture = None
        elif tag in ("h3", "p") and self._capture and self._current is not None:
            self._current[self._capture] = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self._capture = None


def _dedupe(results: list[dict[str, str]], seen: set[str]) -> list[dict[str, str]]:
    clean: list[dict[str, str]] = []
    for result in results:
        key = result.get("url") or result.get("title") or ""
        if key and key not in seen:
            seen.add(key)
            clean.append(result)
    return clean


async def _fetch(client: httpx.AsyncClient, url: str, ua: str) -> str:
    response = await client.get(url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.8"})
    response.raise_for_status()
    return response.text


async def _search_duckduckgo(client: httpx.AsyncClient, query: str, ua: str) -> list[dict[str, str]]:
    url = _DDG_ROOT + "/html/?" + urlencode({"q": query, "kl": "us-en"})
    text = await _fetch(client, url, ua)
    if "result__a" not in text:
        return []
    parser = _DdgPgParser()
    parser.feed(text)
    return parser.results


async def _search_ddg_lite(client: httpx.AsyncClient, query: str, ua: str) -> list[dict[str, str]]:
    url = _DDG_LITE_ROOT + "/lite/?" + urlencode({"q": query})
    text = await _fetch(client, url, ua)
    if "result-link" not in text:
        return []
    parser = _DdgLiteParser()
    parser.feed(text)
    return parser.results


async def _wiki_get(
    client: httpx.AsyncClient, params: dict[str, str], retries: int = 3
) -> dict:
    """GET the Wikipedia API with backoff on 429 rate limits."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params=params,
                headers={"User-Agent": _WIKI_UA, "Accept-Language": "en-US,en;q=0.8"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code == 429 and attempt < retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            raise
    raise last_exc or RuntimeError("Wikipedia request failed")


async def _search_wikipedia(client: httpx.AsyncClient, query: str, ua: str) -> list[dict[str, str]]:
    data = await _wiki_get(
        client,
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": str(_MAX_RESULTS),
            "format": "json",
        },
    )
    hits = data.get("query", {}).get("search", [])
    results: list[dict[str, str]] = []
    for hit in hits:
        title = hit.get("title", "")
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
        results.append(
            {
                "title": title,
                "snippet": snippet,
                "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
            }
        )
    return results


async def _fetch_wiki_excerpt(client: httpx.AsyncClient, title: str, ua: str) -> str:
    """Fetch the lead paragraph of a Wikipedia article (authoritative opening text)."""
    data = await _wiki_get(
        client,
        {
            "action": "query",
            "prop": "extracts",
            "exintro": "1",
            "explaintext": "1",
            "redirects": "1",
            "titles": title,
            "format": "json",
        },
    )
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        extract = (page.get("extract") or "").strip().replace("\n", " ")
        return extract[:2000]
    return ""


_DEFAULT_SEARXNG_URL = "http://localhost:8888"
_PUBLIC_SEARXNG_INSTANCES = [
    "https://search.ononoki.org",
    "https://searx.be",
    "https://search.mdosch.de",
    "https://searx.perennialte.ch",
    "https://searxng.site",
]


def _searxng_params(
    query: str, time_range: str | None = None, categories: str = "general"
) -> dict[str, str]:
    """URL params for the SearXNG JSON API (exposed for tests)."""
    params = {
        "q": query,
        "format": "json",
        "categories": categories,
        "language": "en",
    }
    if time_range:
        params["time_range"] = time_range
    return params


async def _search_searxng(
    client: httpx.AsyncClient,
    query: str,
    base_url: str,
    ua: str,
    timeout: float = 6.0,
    time_range: str | None = None,
    categories: str = "general",
) -> list[dict[str, str]]:
    """Query a SearXNG instance JSON API."""
    url = base_url.rstrip("/") + "/search"
    params = _searxng_params(query, time_range, categories)
    response = await client.get(
        url,
        params=params,
        headers={"User-Agent": ua, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("results", [])
    results: list[dict[str, str]] = []
    for item in items:
        title = (item.get("title") or "").strip()
        url_link = (item.get("url") or "").strip()
        content = (item.get("content") or item.get("snippet") or "").strip()
        engine = (item.get("engine") or "").strip()
        if title and url_link:
            published = str(item.get("publishedDate") or "").strip()
            if published:
                # Normalize "2025-11-02T08:14:00+00:00" down to the date.
                published = published[:10]
            results.append({
                "title": title,
                "url": url_link,
                "snippet": content,
                "engine": engine,
                "date": published,
            })
    return results


async def _search_searxng_html(
    client: httpx.AsyncClient,
    query: str,
    base_url: str,
    ua: str,
    timeout: float = 6.0,
    time_range: str | None = None,
    categories: str = "general",
) -> list[dict[str, str]]:
    """Query the SearXNG web UI as HTML.

    Many instances (including local installs) keep ``format=json`` disabled
    and answer 403 — the HTML page still works and carries the same results.
    """
    url = base_url.rstrip("/") + "/search"
    params = _searxng_params(query, time_range, categories)
    params.pop("format", None)
    response = await client.get(
        url,
        params=params,
        headers={"User-Agent": ua, "Accept": "text/html"},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    parser = _SearxngHtmlParser()
    parser.feed(response.text)
    return parser.results


async def _searxng_attempt(
    client: httpx.AsyncClient,
    query: str,
    base_url: str,
    ua: str,
    timeout: float = 6.0,
    time_range: str | None = None,
    categories: str = "general",
) -> list[dict[str, str]]:
    """One SearXNG attempt: JSON API first, HTML UI when JSON is disabled."""
    try:
        return await _search_searxng(
            client, query, base_url, ua, timeout, time_range, categories
        )
    except Exception:
        return await _search_searxng_html(
            client, query, base_url, ua, timeout, time_range, categories
        )


async def check_searxng_health(base_url: str = _DEFAULT_SEARXNG_URL) -> tuple[bool, str]:
    """Check if a SearXNG instance is reachable and returns valid results."""
    clean_url = (base_url or _DEFAULT_SEARXNG_URL).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
            ua = _USER_AGENTS[0]
            results = await _searxng_attempt(client, "test", clean_url, ua, timeout=3.5)
            if results:
                engines = set(r.get("engine") for r in results if r.get("engine"))
                eng_str = ", ".join(sorted(engines)) if engines else "active"
                return True, f"Online ({len(results)} results, engines: {eng_str})"
            return True, "Online (empty test results)"
    except httpx.ConnectError:
        return False, f"Connection refused to {clean_url}. (Run ./scripts/run_searxng.sh to start)"
    except httpx.TimeoutException:
        return False, f"Timed out connecting to {clean_url}"
    except Exception as exc:
        return False, f"Unreachable ({exc.__class__.__name__}: {exc})"


async def _fetch_jina_reader(
    client: httpx.AsyncClient, target_url: str, ua: str, max_chars: int = _EXCERPT_CHARS
) -> str:
    """Fetch clean Markdown content of a webpage using Jina Reader (r.jina.ai).

    Returns ``(text, published_date)`` — the date comes from the reader's
    "Published Time" header when the page exposes one.
    """
    try:
        jina_url = f"https://r.jina.ai/{target_url}"
        resp = await client.get(
            jina_url,
            headers={"User-Agent": ua, "Accept": "text/markdown"},
            timeout=4.0,
        )
        if resp.status_code == 200:
            text = resp.text.strip()
            if text and len(text) > 40:
                # Drop the reader's metadata preamble ("Title: … / URL Source:
                # … / Published Time: … / Markdown Content:").
                published = ""
                match = re.search(r"^Published Time:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
                if match:
                    published = match.group(1)
                text = re.sub(
                    r"^(Title:.*)\n(.*\n)*?Markdown Content:\s*", "", text, count=1
                )
                if len(text) > 40:
                    return text[:max_chars].strip(), published
    except Exception:
        pass
    return "", ""


async def _enrich(
    results: list[dict[str, str]],
    client: httpx.AsyncClient,
    ua: str,
    deep_read: bool = True,
) -> list[dict[str, str]]:
    """Attach page-content excerpts to the top results.

    Non-wiki pages are fetched in parallel (top ``_DEEP_READ_PAGES``) under one
    overall ``_DEEP_READ_TIMEOUT`` so a slow site cannot stall the pipeline.
    """
    enriched = [dict(result) for result in results]

    if deep_read:
        targets = [
            (index, result)
            for index, result in enumerate(enriched)
            if result.get("url", "").startswith("http") and not result.get("excerpt")
        ][:_DEEP_READ_PAGES]

        async def read(index: int, result: dict[str, str]) -> None:
            url = result.get("url", "")
            published = ""
            if "en.wikipedia.org/wiki/" in url:
                title = url.split("/wiki/", 1)[1]
                try:
                    excerpt = await _fetch_wiki_excerpt(client, title, ua)
                except Exception:
                    excerpt = ""
            else:
                excerpt, published = await _fetch_jina_reader(client, url, ua)
            if excerpt:
                enriched[index] = {**enriched[index], "excerpt": excerpt[:_EXCERPT_CHARS]}
                # Surface the page's own publish date when the engine had none.
                if published and not enriched[index].get("date"):
                    enriched[index] = {**enriched[index], "date": published}

        if targets:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(read(index, result) for index, result in targets)),
                    timeout=_DEEP_READ_TIMEOUT,
                )
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - keep what we got
                pass

    return enriched


def _is_fresh_query(query: str) -> bool:
    """True when the query asks about the latest/current state of the world."""
    return bool(_FRESH_RE.search(query))


def _rank_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    """Prefer recent, official sources; push Wikipedia down for fresh questions."""
    def key(result: dict[str, str]) -> tuple[int, int, int]:
        url = result.get("url", "")
        is_wiki = "wikipedia.org" in url
        date = (result.get("date") or "")[:4]
        try:
            year = -int(date)
        except ValueError:
            year = 0
        has_date = 0 if (result.get("date") or "") else 1
        return (1 if is_wiki else 0, year, has_date)

    return sorted(results, key=key)


async def web_search(
    query: str,
    top_n: int = _MAX_RESULTS,
    searxng_url: str | None = None,
    deep_read: bool = True,
) -> list[dict[str, str]]:
    """Search the web using SearXNG with automatic fallbacks and deep content extraction."""
    from config.settings import Settings

    if not searxng_url:
        try:
            searxng_url = Settings.load().searxng_url
        except Exception:
            searxng_url = _DEFAULT_SEARXNG_URL

    seen: set[str] = set()
    combined: list[dict[str, str]] = []

    # Time-sensitive queries get a recency window and the news category; the
    # windows widen on each retry so a thin news index cannot starve the answer.
    fresh = _is_fresh_query(query)
    if fresh:
        attempts = [
            ("month", "news,general"),
            ("year", "news,general"),
        ]
    else:
        attempts = [(None, "general")]

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        ua = _USER_AGENTS[0]

        # 1. Try configured / local SearXNG, widening the window until results
        for time_range, categories in attempts:
            if combined:
                break
            if searxng_url:
                try:
                    results = await _searxng_attempt(
                        client, query, searxng_url, ua, timeout=4.0,
                        time_range=time_range, categories=categories,
                    )
                    if results:
                        combined.extend(_dedupe(results, seen))
                except Exception:
                    pass

        # 2. Local SearXNG came up empty (degraded/limited instance): fall
        # back to DuckDuckGo first — fast and fresh — then Wikipedia, then
        # public SearXNG nodes as the last resort.
        if not combined:
            for provider in (_search_duckduckgo, _search_ddg_lite, _search_wikipedia):
                try:
                    results = await provider(client, query, ua)
                    if results:
                        combined.extend(_dedupe(results, seen))
                        break
                except Exception:
                    continue

        # 3. Last resort: public SearXNG nodes (same recency window)
        if not combined:
            for pub_url in _PUBLIC_SEARXNG_INSTANCES:
                try:
                    results = await _searxng_attempt(
                        client, query, pub_url, ua, timeout=3.0,
                        time_range=attempts[0][0], categories=attempts[0][1],
                    )
                    if results:
                        combined.extend(_dedupe(results, seen))
                        break
                except Exception:
                    continue

        if combined:
            combined = _rank_results(combined)
            combined = await _enrich(combined[:top_n], client, ua, deep_read=deep_read)
            # The deep-read may have discovered publish dates — re-rank so the
            # freshest dated page leads the list.
            combined = _rank_results(combined)

    return combined[:top_n]


def format_context(results: list[dict[str, str]]) -> str:
    if not results:
        return "Web search returned no results."
    today = datetime.now().strftime("%B %d, %Y")
    lines = [f"### Web search results (fetched {today})", ""]

    # Keep the whole context under ~2000 tokens so generation stays fast.
    budget = _CONTEXT_CHAR_BUDGET
    for index, result in enumerate(results, 1):
        title = (result.get("title") or "").strip()
        date = (result.get("date") or "").strip()
        snippet = (result.get("snippet") or "").strip()
        excerpt = (result.get("excerpt") or "").strip()
        url = (result.get("url") or "").strip()

        block_lines = [f"{index}. {title}"]
        if date:
            block_lines.append(f"   Published: {date}")
        if snippet:
            block_lines.append(f"   {snippet[:400]}")
        if excerpt:
            used = sum(len(line) + 1 for line in lines) + sum(
                len(line) + 1 for line in block_lines
            )
            room = budget - used
            if room > 100:
                block_lines.append(f"   Content: {excerpt[:max(room, 100)]}")
        block = "\n".join(block_lines) + "\n"
        if len(block) > budget:
            break
        budget -= len(block)
        lines.append(block)

    # Compact instructions — every token here costs ~1/20s of prefill on the
    # local model, so this stays one line.
    lines.append(
        f"Answer using only these results. Today is {today}. For time-sensitive "
        "facts prefer the most recent result and state when the information is "
        "from. If they do not answer the question, say 'not found in results'. "
        "Open with the fact itself (never 'Based on' or any mention of search "
        "or sources), keep it to 1-3 sentences, and add no citations."
    )
    return "\n".join(lines)


async def search_context(
    query: str,
    top_n: int = _MAX_RESULTS,
    searxng_url: str | None = None,
    deep_read: bool = True,
) -> str:
    """Return formatted search context for a query, or '' if unavailable."""
    try:
        results = await web_search(query, top_n, searxng_url=searxng_url, deep_read=deep_read)
        if not results:
            return ""
        return format_context(results)
    except Exception:
        return ""
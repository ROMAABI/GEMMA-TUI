from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urlencode

import httpx

_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
]
_WIKI_UA = "GEMMA-TUI/0.1 (local research assistant; uses Wikipedia API politely)"
_MAX_RESULTS = 5
_DDG_ROOT = "https://html.duckduckgo.com"
_DDG_LITE_ROOT = "https://lite.duckduckgo.com"


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


async def _search_searxng(
    client: httpx.AsyncClient,
    query: str,
    base_url: str,
    ua: str,
    timeout: float = 6.0,
) -> list[dict[str, str]]:
    """Query a SearXNG instance JSON API."""
    url = base_url.rstrip("/") + "/search"
    params = {
        "q": query,
        "format": "json",
        "categories": "general",
        "language": "en",
    }
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
            results.append({
                "title": title,
                "url": url_link,
                "snippet": content,
                "engine": engine,
            })
    return results


async def check_searxng_health(base_url: str = _DEFAULT_SEARXNG_URL) -> tuple[bool, str]:
    """Check if a SearXNG instance is reachable and returns valid results."""
    clean_url = (base_url or _DEFAULT_SEARXNG_URL).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
            ua = _USER_AGENTS[0]
            results = await _search_searxng(client, "test", clean_url, ua, timeout=3.5)
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
    client: httpx.AsyncClient, target_url: str, ua: str, max_chars: int = 1500
) -> str:
    """Fetch clean Markdown content of a webpage using Jina Reader (r.jina.ai)."""
    if "wikipedia.org" in target_url:
        return ""
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
                return text[:max_chars].strip()
    except Exception:
        pass
    return ""


async def _enrich(
    results: list[dict[str, str]],
    client: httpx.AsyncClient,
    ua: str,
    deep_read: bool = True,
) -> list[dict[str, str]]:
    """Attach lead-paragraph or page content excerpts to top results."""
    enriched: list[dict[str, str]] = []
    deep_fetched = 0
    for result in results:
        url = result.get("url", "")
        if "en.wikipedia.org/wiki/" in url:
            title = url.split("/wiki/", 1)[1]
            try:
                excerpt = await _fetch_wiki_excerpt(client, title, ua)
            except Exception:
                excerpt = ""
            if excerpt:
                result = {**result, "excerpt": excerpt}
        elif deep_read and deep_fetched < 2 and url.startswith("http"):
            try:
                reader_text = await _fetch_jina_reader(client, url, ua)
                if reader_text:
                    result = {**result, "excerpt": reader_text}
                    deep_fetched += 1
            except Exception:
                pass
        enriched.append(result)
    return enriched


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

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        ua = _USER_AGENTS[0]

        # 1. Try configured / local SearXNG
        if searxng_url:
            try:
                results = await _search_searxng(client, query, searxng_url, ua, timeout=5.0)
                if results:
                    combined.extend(_dedupe(results, seen))
            except Exception:
                pass

        # 2. If no results yet, try public SearXNG nodes
        if not combined:
            for pub_url in _PUBLIC_SEARXNG_INSTANCES:
                try:
                    results = await _search_searxng(client, query, pub_url, ua, timeout=3.0)
                    if results:
                        combined.extend(_dedupe(results, seen))
                        break
                except Exception:
                    continue

        # 3. If SearXNG produced no results, fallback to Wikipedia + DuckDuckGo
        if not combined:
            for provider in (_search_wikipedia, _search_duckduckgo, _search_ddg_lite):
                try:
                    results = await provider(client, query, ua)
                    if results:
                        combined.extend(_dedupe(results, seen))
                except Exception:
                    continue

        if combined:
            combined = await _enrich(combined[:top_n], client, ua, deep_read=deep_read)

    return combined[:top_n]


def format_context(results: list[dict[str, str]]) -> str:
    if not results:
        return "Web search returned no results."
    lines = ["### Web search results (for reference)", ""]
    for index, result in enumerate(results, 1):
        engine_str = f" [{result['engine']}]" if result.get("engine") else ""
        lines.append(f"{index}. **{result.get('title', '')}**{engine_str}")
        if result.get("snippet"):
            lines.append(f"   {result['snippet']}")
        if result.get("excerpt"):
            lines.append(f"   [Content] {result['excerpt']}")
        if result.get("url"):
            lines.append(f"   Source: {result['url']}")
        lines.append("")
    lines.append(
        "Answer the user's question accurately using the search results above when helpful. "
        "Cite relevant facts directly. Do NOT mention internal search mechanisms or that you used web search."
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
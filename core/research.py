from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote_plus, unquote, urlencode, urlparse

import httpx

from utils import runlog

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
_DEEP_READ_PAGES = 3
_DEEP_READ_TIMEOUT = 10.0
_EXCERPT_CHARS = 400
# Context sent to the model: ~250 tokens (≈4 chars/token). The local
# llama-server prefills at ~20 tok/s, so context size is the whole turn's
# latency budget; the cap stays well under the ~2000-token ceiling.
_CONTEXT_CHAR_BUDGET = 1800
# Search reliability: each SearXNG request gets its own 12s budget, one retry,
# then a different engine set / instance is tried.
_SEARCH_TIMEOUT = 12.0
_SEARCH_RETRIES = 1
_SHOPPING_DOMAINS = (
    "amazon.in", "flipkart.com", "gsmarena.com", "91mobiles.com", "smartprix.com",
)
_BLOCKED_DOMAINS = ("wikipedia.org",)
_SHOPPING_RE = re.compile(
    r"\b(price|prices|cost|buy|buying|shop|shopping|deal|deals|discount|where to|"
    r"where can i|available|availability|cheap|cheapest|best|₹|rs\.?|inr)\b",
    re.IGNORECASE,
)
_ENTITY_STOPWORDS = set(
    """a an and are as at be by can do does for from how i in is it me my of on or
    our the this that to u what when where which who why with you your know know's
    current latest newest recent best price prices cost buy buying where shop
    shopping deal deals discount available availability cheap cheapest india
    inr rs amazon flipkart gsmarena 91mobiles smartprix today tonight now week
    month year specs specification review reviews features launch released
    release country region and than then next about news breaking story
    options option locations location buying stores store online user assistant""".split()
)
_MONTHS = set(
    """january february march april may june july august september october
    november december""".split()
)


def _normal_text(text: str) -> str:
    return " ".join(re.findall(r"\w+", (text or "").lower()))


def intent_of(query: str) -> str:
    """ shopping routes to retailer/review sources; everything else is general. """
    return "shopping" if _SHOPPING_RE.search(query) else "general"


def query_entity(query: str) -> str:
    """Best-effort main entity ("Poco X7 Pro", "iphone 17") of a search query."""
    words: list[str] = []
    for token in re.findall(r"\w+", (query or "").lower()):
        if token in _ENTITY_STOPWORDS or token in _MONTHS or re.fullmatch(r"20\d{2}", token):
            continue
        if not re.search(r"[a-z]", token) and not re.fullmatch(r"\d{1,2}", token):
            continue
        if token not in words:
            words.append(token)
    return " ".join(words[:3])


def retailer_pages(query: str, entity: str) -> list[dict[str, str]]:
    """Direct Amazon/Flipkart search pages for availability questions."""
    if not entity or not re.search(
        r"\b(amazon|flipkart|availability|available|e-?commerce|buy)\b", query, re.IGNORECASE
    ):
        return []
    term = quote_plus(entity)
    return [
        {
            "title": f"{entity} - Amazon India",
            "url": f"https://www.amazon.in/s?k={term}",
            "snippet": f"Amazon India search results for {entity}, price and stock",
            "engine": "direct",
            "date": "",
        },
        {
            "title": f"{entity} - Flipkart",
            "url": f"https://www.flipkart.com/search?q={term}",
            "snippet": f"Flipkart search results for {entity}, price and stock",
            "engine": "direct",
            "date": "",
        },
    ]


def relevant_results(
    results: list[dict[str, str]], query: str, intent: str | None = None
) -> list[dict[str, str]]:
    """Keep only results about the query's main entity; never feed junk to the model."""
    intent = intent or intent_of(query)
    entity = query_entity(query)
    haystack = _normal_text
    kept: list[dict[str, str]] = []
    for result in results:
        url = result.get("url", "")
        domain = url.split("/")[2].lower() if url.startswith("http") else ""
        if intent == "shopping" and any(bad in domain for bad in _BLOCKED_DOMAINS):
            continue
        text = haystack(
            f"{result.get('title','')} {result.get('snippet','')} {result.get('excerpt','')}"
        )
        if entity and entity not in text and not all(word in text.split() for word in entity.split()):
            continue
        kept.append(result)

    def rank(result: dict[str, str]) -> tuple[int, int]:
        domain = result.get("url", "").split("/")[2].lower()
        preferred = 0 if (intent == "shopping" and any(d in domain for d in _SHOPPING_DOMAINS)) else 1
        wiki = 1 if "wikipedia.org" in domain else 0
        return (preferred, wiki)

    return sorted(kept, key=rank)


def _rewrite_prompt(message: str, history: list[str], region: str, intent: str) -> str:
    today = datetime.now().strftime("%B %Y")
    context = "\n".join(f"- {item}" for item in history[-3:]) or "- (none)"
    extra = (
        "Include the region and the current month/year."
        if intent == "shopping" or _is_fresh_query(message)
        else "Keep it short; include the region only when useful."
    )
    return (
        "<start_of_turn>system\n"
        "Rewrite the user's message into a standalone keyword web-search query. "
        "Resolve follow-ups using the chat history. Output only the query — no "
        "explanation, no quotes, no question mark. Use Title Case keywords, at "
        "most 8 words: the product/person, the attribute, the region, and month "
        "year for time-sensitive questions. No filler like 'buy from' or "
        "'buying options'. If the message says 'this', 'it', 'that', 'same' or "
        "names no product/person, take the entity from the most recent user "
        "line in Recent chat (for example 'Poco X7 Pro'). "
        f"The user is in {region}. Today is {today}. {extra}<end_of_turn>\n"
        f"<start_of_turn>user\nRecent chat:\n{context}\n"
        f"Message: {message}<end_of_turn>\n"
        "<start_of_turn>model\n<|channel>Ok.<channel|>"
    )


def _rule_query(message: str, history: list[str], region: str, intent: str) -> str:
    entity = query_entity(message)
    history_entity = query_entity(history[-1]) if history else ""
    generic = bool(re.search(r"\b(this|it|that|same|there)\b", message, re.IGNORECASE))
    if history_entity and (generic or not any(re.search(r"\d", token) for token in entity.split())):
        entity = history_entity
    words = entity.split() or [w for w in re.findall(r"\w+", message.lower()) if w not in _ENTITY_STOPWORDS][:4]
    query = " ".join(words).title()
    if intent == "shopping":
        query = f"{query} price buy Amazon Flipkart {region} {datetime.now():%B %Y}"
    elif re.search(r"\b(know|what is|who is)\b", message, re.IGNORECASE):
        query = f"{query} specs price {region}"
    else:
        query = f"{query} {region}"
    return query.strip()


async def rewrite_query(
    message: str,
    history: list[str],
    client: object,
    region: str = "India",
) -> str:
    """One short non-streaming call to make a follow-up standalone; never blocks the chat."""
    started = time.monotonic()
    intent = intent_of(message)
    fallback = _rule_query(message, history, region, intent)
    rewritten = fallback
    try:
        raw = await client.complete(  # type: ignore[attr-defined]
            _rewrite_prompt(message, history, region, intent), n_predict=24, temperature=0.2
        )
        clean = re.sub(r"\s+", " ", (raw or "").strip()).strip("\"'`*_#.").strip()
        clean = clean.split("<end_of_turn>")[0].strip()[:120]
        if len(clean) >= 3:
            rewritten = clean
    except Exception:  # noqa: BLE001 - the rule-based fallback is always available
        pass
    runlog.log(
        {
            "type": "search_rewrite",
            "message": message[:200],
            "query": rewritten,
            "intent": intent,
            "region": region,
            "ms": round((time.monotonic() - started) * 1000, 1),
        }
    )
    return rewritten


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
    timeout: float = _SEARCH_TIMEOUT,
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
    timeout: float = _SEARCH_TIMEOUT,
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
    timeout: float = _SEARCH_TIMEOUT,
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


_BLOCK_PAGE_RE = re.compile(
    r"why have i been blocked|attention required|security service|captcha|"
    r"access denied|enable javascript|just a moment|request unsuccessful",
    re.IGNORECASE,
)


def _usable_text(text: str) -> bool:
    return bool(text and len(text) > 80 and not _BLOCK_PAGE_RE.search(text[:1500]))


def _direct_page_text(raw_html: str) -> str:
    """Strip markup and keep a short window around the most useful price."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw_html, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    matches = list(re.finditer(r"₹\s?\d[\d,]*", text))
    if matches:
        sellers = ("flipkart", "amazon", "reliance digital", "croma", "myntra")

        def score(match: re.Match[str]) -> int:
            before = text[max(0, match.start() - 260):match.start()].lower()
            points = 0
            if "price in india" in before or "prices in india" in before:
                points += 3
            if re.search(r"\d+\s?gb", before):
                points += 2
            if any(seller in before for seller in sellers):
                points += 2
            if "most viewed" in before or "under rs" in before:
                points -= 3
            return points

        match = max(matches, key=score)
        return text[max(0, match.start() - 220):match.start() + 280].strip()
    return text[:800]


async def _fetch_direct_page(
    client: httpx.AsyncClient, target_url: str, ua: str
) -> str:
    try:
        response = await client.get(
            target_url,
            headers={"User-Agent": ua, "Accept-Language": "en-IN,en;q=0.8"},
            timeout=_SEARCH_TIMEOUT,
        )
        response.raise_for_status()
        return _direct_page_text(response.text)
    except Exception:  # noqa: BLE001 - best effort
        return ""


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
                if len(text) > 40 and _usable_text(text):
                    return text[:max_chars].strip(), published
    except Exception:
        pass
    # Jina often returns a Cloudflare block page; fetch the page directly so
    # the price still comes from actual page text rather than a snippet.
    direct = await _fetch_direct_page(client, target_url, ua)
    if _usable_text(direct):
        return direct[:max_chars], ""
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


_AUTHORITATIVE_DOMAINS = (
    ".gov.in", ".nic.in", ".gov.", "britannica.com", "reuters.com",
    "apnews.com", "bbc.com", "thehindu.com", "indianexpress.com",
    "hindustantimes.com", "ndtv.com",
)
_LOW_TRUST_DOMAINS = (
    "facebook.com", "instagram.com", "x.com", "twitter.com", "quora.com",
    "reddit.com", "pinterest.com", "tiktok.com", "youtube.com", "youtu.be",
)


def _rank_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    """Prefer official/reference sources; push social and fan pages to the end."""
    def key(result: dict[str, str]) -> tuple[int, int, int, int]:
        url = result.get("url", "")
        domain = urlparse(url).netloc.lower()
        trusted = any(domain.endswith(item) or item in domain for item in _AUTHORITATIVE_DOMAINS)
        low_trust = any(item in domain for item in _LOW_TRUST_DOMAINS)
        date = (result.get("date") or "")[:4]
        try:
            year = -int(date)
        except ValueError:
            year = 0
        has_date = 0 if (result.get("date") or "") else 1
        return (
            2 if low_trust else (0 if trusted else 1),
            1 if "wikipedia.org" in domain else 0,
            year,
            has_date,
        )

    return sorted(results, key=key)


async def web_search(
    query: str,
    top_n: int = _MAX_RESULTS,
    searxng_url: str | None = None,
    deep_read: bool = True,
    intent: str | None = None,
) -> list[dict[str, str]]:
    """Search with 12s timeouts, one retry, and a main-entity relevance gate."""
    from config.settings import Settings

    started = time.monotonic()
    intent = intent or intent_of(query)
    if not searxng_url:
        try:
            searxng_url = Settings.load().searxng_url
        except Exception:
            searxng_url = _DEFAULT_SEARXNG_URL

    fresh = _is_fresh_query(query) or intent == "shopping"
    categories = "general,shopping" if intent == "shopping" else ("news,general" if fresh else "general")
    primary_range = "month" if fresh else None
    wider_range = "year" if fresh else None
    entity = query_entity(query)
    broader = " ".join(
        part for part in (entity, "price buy" if intent == "shopping" else "review specs")
        if part
    )
    instances = [url for url in [searxng_url, *_PUBLIC_SEARXNG_INSTANCES[:2]] if url]
    seen: set[str] = set()
    attempts_log: list[str] = []

    async with httpx.AsyncClient(
        timeout=_SEARCH_TIMEOUT + 2, follow_redirects=True
    ) as client:
        ua = _USER_AGENTS[0]

        async def try_search(term: str, time_range: str | None, cats: str) -> list[dict[str, str]]:
            for base_url in instances:
                for attempt in range(_SEARCH_RETRIES + 1):
                    try:
                        results = await _searxng_attempt(
                            client, term, base_url, ua, timeout=_SEARCH_TIMEOUT,
                            time_range=time_range, categories=cats,
                        )
                        if results:
                            attempts_log.append(
                                f"{base_url} {cats}/{time_range or '-'} try{attempt + 1}: {len(results)}"
                            )
                            return _dedupe(results, seen)
                    except Exception:  # noqa: BLE001 - try the next instance
                        continue
            return []

        # First pass: rewritten query, shopping category and recency when needed.
        retailers = _dedupe(retailer_pages(query, entity), set())
        raw = await try_search(query, primary_range, categories)
        # For availability questions add the retailer search pages themselves;
        # the deep-read phase fetches their text and we can link them directly.
        raw.extend([dict(page) for page in retailers])
        filtered = relevant_results(raw, query, intent)

        # Second pass: one broader/refined query when the entity gate found too few.
        if len(filtered) < 2:
            more = await try_search(broader, wider_range, "general")
            # try_search already deduped and extended `seen`.
            filtered = relevant_results(more, query, intent)
            filtered.extend([dict(page) for page in retailers])
            filtered = relevant_results(filtered, query, intent)

        # Last resort before giving up: DuckDuckGo HTML (never Wikipedia results).
        if len(filtered) < 2:
            for provider in (_search_duckduckgo, _search_ddg_lite):
                try:
                    results = await provider(client, broader, ua)
                except Exception:
                    continue
                if results:
                    attempts_log.append(f"{provider.__name__}: {len(results)}")
                    filtered = relevant_results(_dedupe(results, seen), query, intent)
                    if len(filtered) >= 2:
                        break

        filtered = filtered[:top_n]
        if filtered:
            filtered = await _enrich(filtered, client, ua, deep_read=deep_read)
            filtered = _rank_results(relevant_results(filtered, query, intent))[:top_n]

    runlog.log(
        {
            "type": "search_results",
            "query": query,
            "entity": entity,
            "intent": intent,
            "results": [
                {"title": item.get("title", ""), "url": item.get("url", ""),
                 "date": item.get("date", ""), "excerpt": bool(item.get("excerpt"))}
                for item in filtered
            ],
            "attempts": attempts_log,
            "ms": round((time.monotonic() - started) * 1000, 1),
        }
    )
    return filtered


_PROMO_LINE_RE = re.compile(
    r"^\s*(buy:|subscribe|log ?in|sign up|advertisement|download the app|"
    r"explore premium|unlock these|you are logged in)",
    re.IGNORECASE,
)


def _clean_snippet(text: str) -> str:
    """Drop search-result chrome that the model tends to copy into answers."""
    return "\n".join(
        line for line in (text or "").splitlines() if not _PROMO_LINE_RE.match(line)
    ).strip()


def format_context(results: list[dict[str, str]]) -> str:
    if not results:
        return "Web search returned no results."
    today = datetime.now().strftime("%B %d, %Y")
    lines = [
        f"### Web search results (fetched {today})",
        "Answer using these sources only. Prefer official government sites and "
        "established reference/news sources; ignore social media, fan pages, "
        "Quora, ads and promotional snippets. Never output 'Buy:' lines, ads "
        "or links unless the user asked where to buy something. If sources "
        "disagree, say so and name the disagreement. Be brief and name the "
        "source you relied on.",
        "",
    ]

    # Keep the whole context under ~2000 tokens so generation stays fast.
    budget = _CONTEXT_CHAR_BUDGET
    for index, result in enumerate(results, 1):
        title = (result.get("title") or "").strip()
        date = (result.get("date") or "").strip()
        snippet = _clean_snippet(result.get("snippet") or "")
        excerpt = _clean_snippet(result.get("excerpt") or "")
        url = (result.get("url") or "").strip()

        block_lines = [f"{index}. {title}"]
        domain = urlparse(url).netloc
        if domain:
            block_lines.append(f"   Domain: {domain}")
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
        f"Answer only from these results. Today is {today}; prefer the most "
        "recent date and state it. Use 'Content:' (fetched page text), not "
        "snippets, for prices. Start with the answer — never 'Based on', 'provided "
        "context', or any search mention. Price format: 'Product (variant) costs "
        "₹N on Seller, seen <date>. Buy: d1, d2, d3.' If ₹ appears in Content, "
        "report it; never say 'price not found'. End with 'Buy: d1, d2, d3' "
        "using Domain lines. No generic advice, no citations."
    )
    return "\n".join(lines)


async def search_context(
    query: str,
    top_n: int = _MAX_RESULTS,
    searxng_url: str | None = None,
    deep_read: bool = True,
    intent: str | None = None,
) -> str:
    """Return formatted search context for a query, or '' if unavailable."""
    try:
        results = await web_search(
            query, top_n, searxng_url=searxng_url, deep_read=deep_read, intent=intent
        )
        if not results:
            return ""
        return format_context(results)
    except Exception:
        return ""
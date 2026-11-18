from __future__ import annotations

import pytest

import core.research as research


class FakeLlm:
    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    async def complete(self, prompt: str, **kwargs: object) -> str:
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        if self.reply is None:
            raise RuntimeError("model unavailable")
        return self.reply


@pytest.mark.asyncio
async def test_rewrite_query_uses_one_short_call_and_cleans_output() -> None:
    llm = FakeLlm('"Poco X7 Pro price India buy Amazon Flipkart"\n')
    query = await research.rewrite_query(
        "what is the current price of poco x7 pro and where to i buy this",
        [],
        llm,
        "India",
    )
    assert query == "Poco X7 Pro price India buy Amazon Flipkart"
    assert len(llm.prompts) == 1
    assert "India" in llm.prompts[0]
    assert "<|channel>Ok.<channel|>" in llm.prompts[0]
    assert llm.kwargs[0]["n_predict"] == 24


@pytest.mark.asyncio
async def test_rewrite_query_falls_back_with_history() -> None:
    query = await research.rewrite_query(
        "then u know the poco x8 pro",
        ["User: what is the current price of poco x7 pro"],
        FakeLlm(),
        "India",
    )
    assert "Poco X8 Pro" in query
    assert "specs price India" in query


def test_intent_and_entity_extraction() -> None:
    assert research.intent_of("current price of poco x7 pro") == "shopping"
    assert research.intent_of("where can i buy iphone 17") == "shopping"
    assert research.intent_of("do you know the poco x8 pro") == "general"
    assert research.query_entity("Poco X7 Pro price India buy Amazon Flipkart") == "poco x7 pro"
    assert research.query_entity("iphone 17 price in india") == "iphone 17"
    assert research.query_entity("then u know the poco x8 pro") == "poco x8 pro"


def test_relevance_gate_filters_irrelevant_and_wikipedia() -> None:
    results = [
        {"title": "Xiaomi", "url": "https://en.wikipedia.org/wiki/Xiaomi", "snippet": "Xiaomi company"},
        {"title": "Realme 12", "url": "https://example.com/realme", "snippet": "another phone"},
        {"title": "Poco X7 Pro 5G price", "url": "https://www.flipkart.com/poco-x7", "snippet": "Poco X7 Pro starts at ₹22,999"},
        {"title": "Poco X7 Pro review", "url": "https://www.gsmarena.com/poco_x7", "snippet": "Poco X7 Pro specs"},
    ]
    kept = research.relevant_results(
        results, "Poco X7 Pro price India buy Amazon Flipkart", "shopping"
    )
    assert len(kept) == 2
    assert "wikipedia.org" not in kept[0]["url"] + kept[1]["url"]
    assert all("poco x7 pro" in (r["title"] + r["snippet"]).lower() for r in kept)


@pytest.mark.asyncio
async def test_web_search_retries_once_then_broadens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float | None, str, float]] = []

    async def fake_search(client, query, base_url, ua, timeout=12.0, time_range=None, categories="general"):
        calls.append((query, time_range, categories, timeout))
        if "India" in query:
            # One retry on the same instance, then irrelevant results.
            return [
                {"title": "Xiaomi", "url": "https://en.wikipedia.org/wiki/Xiaomi", "snippet": "company"},
            ]
        return [
            {"title": "Poco X7 Pro price", "url": "https://www.flipkart.com/poco-x7", "snippet": "Poco X7 Pro ₹22,999"},
            {"title": "Poco X7 Pro specs", "url": "https://www.gsmarena.com/poco-x7", "snippet": "Poco X7 Pro review"},
        ]

    async def no_enrich(results, client, ua, deep_read=True):
        return results

    monkeypatch.setattr(research, "_searxng_attempt", fake_search)
    monkeypatch.setattr(research, "_enrich", no_enrich)

    results = await research.web_search(
        "Poco X7 Pro price India", searxng_url="http://local", deep_read=False,
        intent="shopping",
    )
    assert len(results) == 2
    assert any("shopping" in call[2] for call in calls)
    assert all(call[3] == research._SEARCH_TIMEOUT for call in calls)
    assert any("poco x7 pro" in call[0] and "India" not in call[0] for call in calls)


@pytest.mark.asyncio
async def test_web_search_returns_empty_when_still_irrelevant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_search(*args, **kwargs):
        return [{"title": "Xiaomi", "url": "https://en.wikipedia.org/wiki/Xiaomi", "snippet": "company"}]

    monkeypatch.setattr(research, "_searxng_attempt", fake_search)
    monkeypatch.setattr(research, "_search_duckduckgo", fake_search)
    monkeypatch.setattr(research, "_search_ddg_lite", fake_search)
    results = await research.web_search(
        "Poco X7 Pro price India", searxng_url="http://local", deep_read=False
    )
    assert results == []

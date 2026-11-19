from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.lifecycle import get_server_context_limit
from core import compaction
from core.agent import SEARCH_ANSWER_MAX_TOKENS, AgentOrchestrator
from core.permissions import default_rules
from core.research import rewrite_query
from core.tooling import build_default_registry
from core.tooling.filesystem import list_dir
from core.tooling.types import ToolContext
from core.tooling.web import web_search_tool


def test_web_search_tool_is_registered_with_clear_description() -> None:
    registry = build_default_registry()
    spec = registry.get("web_search")
    assert spec is not None
    assert "price" in spec.description.lower()
    assert "availability" in spec.description.lower()
    fetch = registry.get("webfetch")
    assert fetch is not None and "captcha" in fetch.description.lower()
    assert any(rule.action == "web_search" for rule in default_rules("auto"))
    assert any(rule.action == "web_search" and rule.effect == "deny" for rule in default_rules("readonly"))


@pytest.mark.asyncio
async def test_web_search_tool_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query, **kwargs):
        return [{"title": "Poco X7 Pro price", "url": "https://flipkart.com/x7", "snippet": "₹23,999", "date": "2026-11-19"}]

    monkeypatch.setattr("core.research.web_search", fake_search)
    result = await web_search_tool(ToolContext(root=Path(".")), {"query": "Poco X7 Pro price India"})
    assert result.ok
    assert "flipkart.com/x7" in result.output
    assert "₹23,999" in result.output


def test_list_dir_is_non_recursive_and_capped(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    for index in range(60):
        (tmp_path / f"file{index}.txt").write_text("x")
    (tmp_path / "nested" / "hidden.txt").write_text("x")
    result = list_dir(ToolContext(root=tmp_path), {"path": "."})
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) <= 50
    assert sum(line.startswith("nested/") for line in lines) <= 1
    assert "hidden.txt" not in result.output


@pytest.mark.asyncio
async def test_followup_rewrite_resolves_this_from_history() -> None:
    class NoLlm:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("offline")

    query = await rewrite_query(
        "can u check the availability in amazon or any other major ecommerce site",
        ["User: what is the current price of poco x7 pro and where to i buy this"],
        NoLlm(),
        "India",
    )
    assert "Poco X7 Pro" in query
    assert "Amazon" in query and "Flipkart" in query


@pytest.mark.asyncio
async def test_length_finish_reason_triggers_one_continuation() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, messages, tools, **kwargs):
            self.calls += 1
            text = " GB + 256 GB costs ₹23,999 on Flipkart." if self.calls == 1 else ""
            yield SimpleNamespace(type="text", text=text)

    provider = FakeProvider()
    agent = object.__new__(AgentOrchestrator)
    agent.provider = provider
    agent.settings = SimpleNamespace(temperature=0.7)
    continuation = await agent._continue_answer([{"role": "user", "content": "price?"}], "₹23,999 for the 8 GB + 256")
    assert continuation == "GB + 256 GB costs ₹23,999 on Flipkart."
    assert SEARCH_ANSWER_MAX_TOKENS == 400


def test_context_limit_uses_server_props(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"default_generation_settings": {"n_ctx": 8192}}

    monkeypatch.setattr("app.lifecycle.httpx.get", lambda *a, **k: FakeResponse())
    assert get_server_context_limit("http://127.0.0.1:8080", 32768) == 8192


def test_compaction_drops_old_tool_outputs_first() -> None:
    dropped = compaction._drop_old_tool_outputs(
        [
            {"role": "user", "content": "question"},
            {"role": "tool", "name": "webfetch", "content": "x" * 5000},
        ]
    )
    assert dropped[0]["content"] == "question"
    assert len(dropped[1]["content"]) < 100

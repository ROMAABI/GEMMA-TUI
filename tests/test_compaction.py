from __future__ import annotations

import asyncio

import pytest

from core.compaction import Compactor, estimate_tokens


class FakeClient:
    def __init__(self, summary: str = "## Objective\n- test") -> None:
        self.summary = summary
        self.calls: list[str] = []

    async def complete(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        return self.summary


def make_messages(count: int, size: int = 400) -> list[dict]:
    messages = [{"role": "system", "content": "system prompt"}]
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"message {index} " + ("x" * size)})
    return messages


def test_estimate_tokens() -> None:
    assert estimate_tokens([{"role": "user", "content": "a" * 400}]) >= 100


def test_compaction_reduces_and_keeps_recent() -> None:
    client = FakeClient()
    compactor = Compactor(client, context_limit=4_000, threshold=0.8, keep_tokens=800)
    messages = make_messages(20)
    assert compactor.should_compact(messages)
    result = asyncio.run(compactor.compact(messages))
    assert result is not None
    assert result.removed > 0
    assert result.after_tokens < result.before_tokens
    assert any("<conversation-checkpoint>" in (m.get("content") or "") for m in result.messages)
    assert result.messages[-1]["content"].startswith("message 19")


def test_compaction_skips_when_nothing_old() -> None:
    client = FakeClient()
    compactor = Compactor(client, context_limit=100_000, threshold=0.8, keep_tokens=100_000)
    result = asyncio.run(compactor.compact(make_messages(2)))
    assert result is None


def test_compaction_drops_orphan_tool_results() -> None:
    client = FakeClient()
    compactor = Compactor(client, context_limit=4_000, threshold=0.8, keep_tokens=200)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old " + "x" * 2000},
        {"role": "assistant", "content": "old " + "x" * 2000},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "result"},
    ]
    result = asyncio.run(compactor.compact(messages))
    assert result is not None
    # Any tool message that survived must follow an assistant message that
    # requested it; orphaned results are invalid context.
    for index, message in enumerate(result.messages):
        if message.get("role") == "tool":
            assert index > 0
            previous = result.messages[index - 1]
            assert previous.get("role") == "assistant"
            assert previous.get("tool_calls")


def test_fallback_summary_when_model_fails() -> None:
    class BrokenClient:
        async def complete(self, prompt: str, **kwargs) -> str:
            raise RuntimeError("boom")

    compactor = Compactor(BrokenClient(), context_limit=4_000, threshold=0.8, keep_tokens=100)
    messages = make_messages(10)
    result = asyncio.run(compactor.compact(messages))
    assert result is not None
    assert "Objective" in result.summary

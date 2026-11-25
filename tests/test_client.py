from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from config.settings import Settings
from core.client import LlamaClient, _is_gemma_model, _parse_gemma_prompt, collapse_system_messages
from core.titles import _title_prompt


def test_parse_gemma_prompt_extracts_messages_and_no_thinking() -> None:
    prompt = _title_prompt("who is abimanyu", "I am Gemma.")
    messages, no_thinking = _parse_gemma_prompt(prompt)
    assert no_thinking is True
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "who is abimanyu" in messages[1]["content"]


def test_collapse_system_messages() -> None:
    messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "hello"},
        {"role": "system", "content": "search context"},
    ]
    assert collapse_system_messages(messages) == [
        {"role": "system", "content": "base\n\nsearch context"},
        {"role": "user", "content": "hello"},
    ]


def test_is_gemma_model() -> None:
    assert _is_gemma_model("gemma-4-E4B-it-Q4_K_M") is True
    assert _is_gemma_model("Qwen3.5-9B-Q4_K_M") is False


@pytest.mark.asyncio
async def test_non_gemma_complete_uses_chat_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "Who Is Abimanyu"}}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("core.client.httpx.AsyncClient", FakeAsyncClient)
    client = LlamaClient(Settings(model="Qwen3.5-9B-Q4_K_M"))
    out = await client.complete(_title_prompt("who is abimanyu", "reply"), n_predict=16)

    assert out == "Who Is Abimanyu"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert all("<|channel>" not in m["content"] for m in captured["json"]["messages"])


@pytest.mark.asyncio
async def test_non_gemma_stream_chat_uses_chat_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeStreamResponse:
        def raise_for_status(self) -> None:
            pass

        async def aiter_lines(self):
            yield f"data: {json.dumps({'choices': [{'delta': {'content': 'Hello'}}]})}"
            yield f"data: {json.dumps({'choices': [{'delta': {'content': ' world'}}]})}"
            yield "data: [DONE]"

    class FakeStreamCtx:
        async def __aenter__(self) -> FakeStreamResponse:
            return FakeStreamResponse()

        async def __aexit__(self, *args: Any) -> None:
            pass

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def stream(self, method: str, url: str, json: dict[str, Any]) -> FakeStreamCtx:
            captured["url"] = url
            captured["json"] = json
            return FakeStreamCtx()

    monkeypatch.setattr("core.client.httpx.AsyncClient", FakeAsyncClient)
    client = LlamaClient(Settings(model="Qwen3.5-9B-Q4_K_M"))
    chunks: list[str] = []
    async for chunk in client.stream_chat(
        [{"role": "user", "content": "who is abimanyu"}], prefill=True
    ):
        chunks.append(chunk)

    assert "".join(chunks) == "Hello world"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": False}

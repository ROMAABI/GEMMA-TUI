from __future__ import annotations

import asyncio
import json

import pytest

import core.providers as providers
from core.providers import (
    NativeToolProvider,
    NativeToolsUnsupported,
    ProviderEvent,
    ProviderRouter,
    ToolCall,
    _parse_json_arguments,
)
from config.settings import Settings


def test_parse_json_arguments_repairs() -> None:
    assert _parse_json_arguments('{"path": "a.py"}') == ({"path": "a.py"}, None)
    assert _parse_json_arguments('{"path": "a.py",}')[0] == {"path": "a.py"}
    assert _parse_json_arguments("{'path': 'a.py'}")[0] == {"path": "a.py"}
    args, error = _parse_json_arguments("not json")
    assert args == {} and error


def test_parse_json_arguments_extracts_object() -> None:
    args, error = _parse_json_arguments('prefix {"path": "a.py"} suffix')
    assert args == {"path": "a.py"} and error is None


class FakeResponse:
    def __init__(self, lines: list[str], status_code: int = 200, text: str = "") -> None:
        self.lines = lines
        self.status_code = status_code
        self.text = text

    async def aread(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeStream:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(self, *args) -> None:
        return None


class FakeClient:
    def __init__(self, response: FakeResponse, **kwargs) -> None:
        self.response = response
        self.payload = None

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args) -> None:
        return None

    def stream(self, method, url, json=None, headers=None):
        self.payload = json
        return FakeStream(self.response)


def run_stream(provider: NativeToolProvider, monkeypatch, response: FakeResponse) -> list[ProviderEvent]:
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: FakeClient(response))

    async def collect():
        return [event async for event in provider.stream([{"role": "user", "content": "hi"}], [])]

    return asyncio.run(collect())


def test_native_stream_text_tools_usage(monkeypatch) -> None:
    settings = Settings(model="test")
    provider = NativeToolProvider(settings)
    chunks = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read","arguments":"{\\"path\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"a.py\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        'data: {"usage":{"prompt_tokens":10,"completion_tokens":5}}',
        "data: [DONE]",
    ]
    events = run_stream(provider, monkeypatch, FakeResponse(chunks))
    text = "".join(event.text for event in events if event.type == "text")
    assert text == "Hello"
    calls = [event for event in events if event.type == "tool_calls"][0].tool_calls
    assert calls[0].name == "read"
    assert calls[0].arguments == {"path": "a.py"}
    usage = [event for event in events if event.type == "usage"][0].usage
    assert usage["input_tokens"] == 10


def test_native_tools_unsupported(monkeypatch) -> None:
    settings = Settings(model="test")
    provider = NativeToolProvider(settings)
    response = FakeResponse([], status_code=400, text="tools are not supported")
    with pytest.raises(NativeToolsUnsupported):
        run_stream(provider, monkeypatch, response)


def test_router_falls_back_to_prompt() -> None:
    settings = Settings(model="test", provider="auto")
    router = ProviderRouter(settings)

    class Native:
        async def stream(self, *args, **kwargs):
            raise NativeToolsUnsupported("no tools")
            yield  # pragma: no cover

    class Prompt:
        async def stream(self, *args, **kwargs):
            yield ProviderEvent("text", text="fallback")
            yield ProviderEvent("done")

    router.native = Native()
    router.prompt = Prompt()

    async def collect():
        return [event async for event in router.stream([], [])]

    events = asyncio.run(collect())
    assert events[0].text == "fallback"
    assert router.last_provider == "prompt"


def test_router_uses_native_when_available() -> None:
    settings = Settings(model="test", provider="auto")
    router = ProviderRouter(settings)

    class Native:
        async def stream(self, *args, **kwargs):
            yield ProviderEvent("text", text="native")
            yield ProviderEvent("done")

    router.native = Native()

    async def collect():
        return [event async for event in router.stream([], [])]

    events = asyncio.run(collect())
    assert events[0].text == "native"
    assert router.last_provider == "native"


class FakePostClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.url = ""
        self.payload = None
        self.headers = None

    async def __aenter__(self) -> "FakePostClient":
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def post(self, url, json=None, headers=None):
        self.url = url
        self.payload = json
        self.headers = headers

        class Response:
            def __init__(self, code: int) -> None:
                self.status_code = code

        return Response(self.status_code)


def test_native_warmup_sends_one_token_request(monkeypatch) -> None:
    settings = Settings(model="test", base_url="http://127.0.0.1:8080")
    provider = NativeToolProvider(settings)
    holder: dict[str, FakePostClient] = {}

    def factory(**kwargs):
        holder["client"] = FakePostClient()
        return holder["client"]

    monkeypatch.setattr(providers.httpx, "AsyncClient", factory)
    tools = [{"type": "function", "function": {"name": "read"}}]
    ok = asyncio.run(
        provider.warmup(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            tools,
        )
    )

    client = holder["client"]
    assert ok is True
    assert client.url.endswith("/v1/chat/completions")
    assert client.payload["max_tokens"] == 1
    assert client.payload["stream"] is False
    assert client.payload["tools"] == tools


def test_native_warmup_reports_failure_without_raising(monkeypatch) -> None:
    settings = Settings(model="test")
    provider = NativeToolProvider(settings)
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **kwargs: FakePostClient(status_code=500)
    )
    assert asyncio.run(provider.warmup([{"role": "user", "content": "hi"}], [])) is False


def test_router_warmup_falls_back_to_prompt_provider(monkeypatch) -> None:
    settings = Settings(model="test", provider="auto")
    router = ProviderRouter(settings)
    calls: list[str] = []

    async def native_warmup(messages, tools):
        calls.append("native")
        return False

    async def prompt_warmup(messages, tools):
        calls.append("prompt")
        return True

    monkeypatch.setattr(router.native, "warmup", native_warmup)
    monkeypatch.setattr(router.prompt, "warmup", prompt_warmup)

    assert asyncio.run(router.warmup([{"role": "user", "content": "hi"}], [])) is True
    assert calls == ["native", "prompt"]

"""Provider abstraction: native OpenAI tool calling with a prompt/XML fallback."""

from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from config.settings import Settings
from core.client import LlamaClient, REPETITION_PARAMS, collapse_system_messages
from core.tools import parse_tool_calls, strip_tool_calls
from utils import runlog


class NativeToolsUnsupported(Exception):
    """The server/model rejected the OpenAI tools payload."""


class ContextOverflowError(Exception):
    """The provider rejected the request because the context is too long."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    error: str | None = None


@dataclass
class ProviderEvent:
    type: str  # text | text_replace | reasoning | tool_calls | usage | done
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int | float] = field(default_factory=dict)
    finish_reason: str = ""


def _parse_json_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    text = (raw or "").strip()
    if not text:
        return {}, None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, None
        return {}, f"Tool arguments must be a JSON object, got {type(parsed).__name__}"
    except Exception:
        pass

    # Common model repairs: trailing commas, single quotes, Python literals.
    candidate = re.sub(r",\s*([}\]])", r"\1", text)
    for attempt in (candidate, candidate.replace("'", '"')):
        try:
            parsed = json.loads(attempt)
            if isinstance(parsed, dict):
                return parsed, None
        except Exception:
            continue
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, dict):
            return parsed, None
    except Exception:
        pass

    # Last resort: extract the first balanced object.
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            char = text[idx]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    snippet = text[start : idx + 1]
                    try:
                        parsed = json.loads(re.sub(r",\s*([}\]])", r"\1", snippet))
                        if isinstance(parsed, dict):
                            return parsed, None
                    except Exception:
                        break

    return {}, f"Could not parse tool arguments as JSON: {raw[:300]}"


def _is_tools_unsupported(response: httpx.Response) -> bool:
    body = response.text.lower()
    if response.status_code in (400, 404, 422, 500):
        needles = (
            "tools",
            "tool_choice",
            "function",
            "jinja",
            "chat template",
            "unsupported",
            "not supported",
        )
        return any(needle in body for needle in needles)
    return False


def _is_context_overflow(response: httpx.Response) -> bool:
    body = response.text.lower()
    needles = (
        "context length",
        "context window",
        "too many tokens",
        "maximum context",
        "exceeds the available context",
        "prompt is too long",
        "n_ctx",
    )
    return response.status_code in (400, 413, 422, 500) and any(n in body for n in needles)


class NativeToolProvider:
    """OpenAI-compatible ``/v1/chat/completions`` provider with streaming tools."""

    name = "native"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = getattr(self.settings, "api_key", "") or ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        stream: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        repeat_penalty: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": collapse_system_messages(messages),
            "stream": stream,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or getattr(self.settings, "agent_max_tokens", 4096),
            # Reuse the server's prompt cache across steps/turns. llama.cpp
            # ignores unknown OAI fields, so this is safe everywhere.
            "cache_prompt": True,
            **REPETITION_PARAMS,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        if repeat_penalty is not None:
            payload["repeat_penalty"] = repeat_penalty
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # Qwen3.5 thinks for minutes on CPU before emitting anything. The chat
        # template flag disables that thinking channel while still producing a
        # normal answer (and tool calls) in the content channel.
        if "qwen3" in (self.settings.model or "").lower():
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    async def warmup(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> bool:
        """Prime the prompt cache with the exact agent prefix."""
        payload = self._payload(
            messages, tools, stream=False, temperature=0.0, max_tokens=1
        )
        timeout = httpx.Timeout(connect=10.0, read=900.0, write=30.0, pool=10.0)
        url = f"{self.settings.base_url}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url, json=payload, headers=self._headers()
                )
                return response.status_code < 400
        except Exception:  # noqa: BLE001 - warmup is best effort
            return False

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        repeat_penalty: float | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        payload = self._payload(
            messages, tools, stream=True, temperature=temperature, max_tokens=max_tokens,
            top_p=top_p, repeat_penalty=repeat_penalty,
        )
        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        url = f"{self.settings.base_url}/v1/chat/completions"
        accumulated: dict[int, dict[str, str]] = {}
        text_buffer: list[str] = []
        finish_reason = ""
        usage: dict[str, int | float] = {}
        self._last_timings: dict[str, Any] = {}
        started = time.monotonic()

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=self._headers()) as response:
                if response.status_code >= 400:
                    await response.aread()
                    if _is_context_overflow(response):
                        raise ContextOverflowError(response.text[:1000])
                    if _is_tools_unsupported(response):
                        raise NativeToolsUnsupported(response.text[:1000])
                    response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        raw = line[6:].strip()
                    elif line.startswith("data:"):
                        raw = line[5:].strip()
                    else:
                        continue
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(data.get("usage"), dict):
                        usage = self._merge_usage(usage, data["usage"])
                    if isinstance(data.get("timings"), dict):
                        self._last_timings = dict(data["timings"])
                        usage = self._merge_usage(usage, self._timings_to_usage(data["timings"]))

                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    message = choice.get("message") or {}
                    content = delta.get("content", message.get("content"))
                    reasoning = (
                        delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or message.get("reasoning_content")
                    )
                    if reasoning:
                        yield ProviderEvent("reasoning", text=str(reasoning))
                    if content:
                        text_buffer.append(str(content))
                        yield ProviderEvent("text", text=str(content))

                    calls = delta.get("tool_calls") or message.get("tool_calls") or []
                    if isinstance(calls, list):
                        for call in calls:
                            self._accumulate_call(accumulated, call)

                    legacy = delta.get("function_call") or message.get("function_call")
                    if isinstance(legacy, dict):
                        self._accumulate_call(
                            accumulated,
                            {
                                "index": 0,
                                "id": "call_0",
                                "function": legacy,
                            },
                        )

                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])

        # Latency audit: prompt eval (prompt_ms / cache_n shows whether the
        # server reused the cached prefix) and generation time per step.
        timings = self._last_timings
        runlog.log(
            {
                "type": "provider_step",
                "provider": self.name,
                "stream": True,
                "ttft_ms": round((time.monotonic() - started) * 1000, 1),
                "prompt_n": timings.get("prompt_n"),
                "cache_n": timings.get("cache_n"),
                "prompt_ms": timings.get("prompt_ms"),
                "predicted_n": timings.get("predicted_n"),
                "predicted_ms": timings.get("predicted_ms"),
                "finish_reason": finish_reason,
                "tool_calls": bool(accumulated),
            }
        )

        tool_calls = self._finalize_calls(accumulated)
        if not tool_calls and text_buffer:
            # Some chat templates emit XML tool calls as text even on the native
            # route. Recover them so the agent still makes progress, and strip
            # the markup from the prose so the UI doesn't render raw tool-call
            # XML (with stray token fragments) as the assistant's answer.
            raw_text = "".join(text_buffer)
            parsed = parse_tool_calls(raw_text)
            tool_calls = [
                ToolCall(id=f"call_{idx}", name=name, arguments=args)
                for idx, (name, args) in enumerate(parsed)
            ]
            if tool_calls:
                preamble = strip_tool_calls(raw_text)
                if preamble != raw_text.strip():
                    yield ProviderEvent("text_replace", text=preamble)
        if usage:
            yield ProviderEvent("usage", usage=usage)
        if tool_calls:
            yield ProviderEvent("tool_calls", tool_calls=tool_calls, finish_reason=finish_reason)
        yield ProviderEvent("done", finish_reason=finish_reason)

    @staticmethod
    def _merge_usage(current: dict[str, int | float], incoming: dict[str, Any]) -> dict[str, int | float]:
        merged = dict(current)
        mapping = {
            "prompt_tokens": "input_tokens",
            "completion_tokens": "output_tokens",
            "total_tokens": "total_tokens",
            "gen_ms": "gen_ms",
        }
        for source, target in mapping.items():
            value = incoming.get(source)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                merged[target] = value
        return merged

    @staticmethod
    def _timings_to_usage(timings: dict[str, Any]) -> dict[str, int | float]:
        usage: dict[str, int | float] = {}
        if isinstance(timings.get("prompt_n"), int):
            cached = timings.get("cache_n")
            usage["prompt_tokens"] = timings["prompt_n"] + (
                cached if isinstance(cached, int) else 0
            )
            usage["new_prompt_tokens"] = timings["prompt_n"]
            usage["cache_tokens"] = cached if isinstance(cached, int) else 0
        if isinstance(timings.get("predicted_n"), int):
            usage["completion_tokens"] = timings["predicted_n"]
        if isinstance(timings.get("predicted_ms"), (int, float)):
            # Real generation time (no prefill), so the UI can compute tok/s
            # from the same source as the server's timing log.
            usage["gen_ms"] = timings["predicted_ms"]
        return usage

    @staticmethod
    def _accumulate_call(accumulated: dict[int, dict[str, str]], call: dict[str, Any]) -> None:
        try:
            index = int(call.get("index", len(accumulated)))
        except (TypeError, ValueError):
            index = len(accumulated)
        slot = accumulated.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if call.get("id"):
            slot["id"] = str(call["id"])
        function = call.get("function") or {}
        if function.get("name"):
            slot["name"] = str(function["name"])
        if function.get("arguments") is not None:
            slot["arguments"] += str(function["arguments"])

    @staticmethod
    def _finalize_calls(accumulated: dict[int, dict[str, str]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for index in sorted(accumulated):
            slot = accumulated[index]
            name = slot.get("name", "").strip()
            if not name:
                continue
            arguments, error = _parse_json_arguments(slot.get("arguments", ""))
            calls.append(
                ToolCall(
                    id=slot.get("id") or f"call_{index}",
                    name=name,
                    arguments=arguments,
                    raw_arguments=slot.get("arguments", ""),
                    error=error,
                )
            )
        return calls


class PromptToolProvider:
    """Legacy Gemma ``/completion`` provider using XML tool calls."""

    name = "prompt"

    def __init__(self, settings: Settings, client: LlamaClient | None = None) -> None:
        self.settings = settings
        self.client = client or LlamaClient(settings)

    async def warmup(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> bool:
        converted = self._convert_messages(messages)
        return await self.client.warmup(converted)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        converted = self._convert_messages(messages)
        buffer = ""
        try:
            # Web-search context makes the model think at length before
            # answering; prefill a closed thinking channel there so replies
            # stream right away.
            prefill = any(
                "### Web search results" in str(message.get("content") or "")
                for message in messages
            )
            async for chunk in self.client.stream_chat(
                converted, temperature=temperature, prefill=prefill
            ):
                buffer += chunk
                yield ProviderEvent("text", text=chunk)
        except Exception as exc:  # noqa: BLE001
            if _looks_like_overflow(str(exc)):
                raise ContextOverflowError(str(exc)) from exc
            raise

        calls = parse_tool_calls(buffer)
        tool_calls = [
            ToolCall(id=f"call_{idx}", name=name, arguments=args)
            for idx, (name, args) in enumerate(calls)
        ]
        if tool_calls:
            yield ProviderEvent("tool_calls", tool_calls=tool_calls, finish_reason="tool_calls")
        yield ProviderEvent("done", finish_reason="tool_calls" if tool_calls else "stop")

    @staticmethod
    def _convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        converted: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content") or ""
            if role == "tool":
                name = message.get("name", "tool")
                call_id = message.get("tool_call_id", "")
                converted.append(
                    {
                        "role": "user",
                        "content": (
                            f"<tool_response name=\"{name}\" id=\"{call_id}\">\n"
                            f"{content}\n</tool_response>"
                        ),
                    }
                )
            elif role == "assistant":
                text = str(content)
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    text += (
                        "\n<tool_call>\n"
                        + json.dumps({"name": function.get("name"), "arguments": args})
                        + "\n</tool_call>"
                    )
                converted.append({"role": "assistant", "content": text})
            else:
                converted.append({"role": role, "content": str(content)})
        return converted


def _looks_like_overflow(text: str) -> bool:
    lowered = text.lower()
    return any(
        needle in lowered
        for needle in (
            "context length",
            "context window",
            "too many tokens",
            "n_ctx",
            "prompt is too long",
        )
    )


class ProviderRouter:
    """Chooses native tools when possible and remembers when they are not."""

    def __init__(
        self,
        settings: Settings,
        client: LlamaClient | None = None,
        *,
        mode: str | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or LlamaClient(settings)
        self.mode = (mode or getattr(settings, "provider", "auto") or "auto").lower()
        self.native = NativeToolProvider(settings)
        self.prompt = PromptToolProvider(settings, self.client)
        self._native_supported = self.mode != "prompt"
        self.last_provider = "prompt" if self.mode == "prompt" else "native"

    async def warmup(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> bool:
        """Prime the prompt cache so the first real step is fast."""
        if self.mode == "prompt":
            return await self.prompt.warmup(messages, tools)
        if not self._native_supported:
            return await self.prompt.warmup(messages, tools)
        try:
            warmed = await self.native.warmup(messages, tools)
        except Exception:  # noqa: BLE001
            return False
        if warmed:
            return True
        # A server without native tool support can still cache the fallback
        # prompt, which is what the next request will actually send.
        self._native_supported = False
        return await self.prompt.warmup(messages, tools)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **_kwargs: Any,
    ) -> AsyncIterator[ProviderEvent]:
        if self.mode == "prompt":
            self.last_provider = "prompt"
            async for event in self.prompt.stream(
                messages, tools, temperature=temperature, max_tokens=max_tokens
            ):
                yield event
            return

        if self._native_supported:
            emitted = False
            try:
                async for event in self.native.stream(
                    messages, tools, temperature=temperature, max_tokens=max_tokens
                ):
                    if event.type in ("text", "reasoning") and event.text:
                        emitted = True
                    yield event
                self.last_provider = "native"
                return
            except NativeToolsUnsupported:
                if emitted:
                    raise
                self._native_supported = False
                if self.mode == "native":
                    raise
            except ContextOverflowError:
                raise

        self.last_provider = "prompt"
        async for event in self.prompt.stream(
            messages, tools, temperature=temperature, max_tokens=max_tokens
        ):
            yield event

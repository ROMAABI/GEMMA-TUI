from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from config.settings import Settings
from utils import runlog

_THOUGHT_PATTERN = re.compile(r"<\|channel>.*?<channel\|>", re.S)
_CHANNEL_OPEN = "<|channel>"
_CHANNEL_CLOSE = "<channel|>"
_MAX_PREAMBLE = 16384
# Prefilling a closed thinking channel makes the model answer immediately
# instead of burning 40s+ on its thinking process — used for web-search
# answers where latency matters and the context already carries the facts.
_ANSWER_PREFILL = "<|channel>Ok.<channel|>"


def _build_prompt(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            parts.append(f"<start_of_turn>system\n{content}<end_of_turn>\n")
        elif role == "user":
            parts.append(f"<start_of_turn>user\n{content}<end_of_turn>\n")
        elif role == "assistant":
            parts.append(f"<start_of_turn>model\n{content}<end_of_turn>\n")
    parts.append("<start_of_turn>model\n")
    return "".join(parts)


def _is_gemma_model(model: str) -> bool:
    return "gemma" in (model or "").lower()


def collapse_system_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge every system message into the first one.

    Qwen3's chat template rejects a system message that is not the first
    message in the array. Web-search context and agent checkpoints are added
    as extra system messages upstream, so fold them into the leading prompt
    before sending the request.
    """
    systems = [message for message in messages if message.get("role") == "system"]
    if len(systems) < 2:
        return messages
    merged = "\n\n".join(str(message.get("content") or "") for message in systems).strip()
    result: list[dict[str, str]] = []
    inserted = False
    for message in messages:
        if message.get("role") == "system":
            if not inserted:
                result.append({"role": "system", "content": merged})
                inserted = True
            continue
        result.append(message)
    return result


def _is_thinking_model(model: str) -> bool:
    """Qwen3/Qwen3.5 hybrid models think unless the template disables it.

    llama.cpp's `reasoning_budget: 0` makes those models emit only the
    thinking channel, which the UI never displays. The chat template flag is
    the supported way to get a normal, fast answer instead.
    """
    return "qwen3" in (model or "").lower()


_TURN_PATTERN = re.compile(
    r"<start_of_turn>(system|user|model)\n(.*?)<end_of_turn>", re.S
)


def _parse_gemma_prompt(prompt: str) -> tuple[list[dict[str, str]], bool]:
    """Extract OpenAI-style messages and no-thinking flag from a raw Gemma prompt."""
    messages: list[dict[str, str]] = []
    for role, content in _TURN_PATTERN.findall(prompt):
        mapped = "assistant" if role == "model" else role
        messages.append({"role": mapped, "content": content})
    no_thinking = _ANSWER_PREFILL in prompt.split("<end_of_turn>")[-1]
    return messages, no_thinking


def _clean_output(text: str) -> str:
    return _THOUGHT_PATTERN.sub("", text).strip()


class LlamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def complete(
        self,
        prompt: str,
        *,
        n_predict: int = 64,
        temperature: float | None = None,
    ) -> str:
        """Run a single non-streaming completion and return the text."""
        temp = self.settings.temperature if temperature is None else temperature
        if not _is_gemma_model(self.settings.model):
            messages, no_thinking = _parse_gemma_prompt(prompt)
            if messages:
                payload: dict[str, Any] = {
                    "messages": collapse_system_messages(messages),
                    "max_tokens": n_predict,
                    "temperature": temp,
                    "stream": False,
                    "cache_prompt": False,
                }
                if no_thinking and _is_thinking_model(self.settings.model):
                    payload["chat_template_kwargs"] = {"enable_thinking": False}
                async with httpx.AsyncClient(timeout=None) as client:
                    response = await client.post(
                        f"{self.settings.base_url}/v1/chat/completions", json=payload
                    )
                    response.raise_for_status()
                    data = response.json()
                choice = (data.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content") or ""
                return _clean_output(content).strip()

        payload = {
            "prompt": prompt,
            "n_predict": n_predict,
            "temperature": temp,
            "stream": False,
            "cache_prompt": False,
            "stop": ["<end_of_turn>"],
        }
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(f"{self.settings.base_url}/completion", json=payload)
            response.raise_for_status()
            data = response.json()
        return _clean_output(data.get("content", "")).strip()

    async def warmup(self, messages: list[dict[str, str]]) -> bool:
        """Prime the server prompt cache without generating a real answer.

        The first request of a session pays for processing the whole system
        prompt and tool catalog. Sending the same prefix once with
        ``n_predict=1`` makes every later request reuse it.
        """
        if not _is_gemma_model(self.settings.model):
            url = f"{self.settings.base_url}/v1/chat/completions"
            payload: dict[str, Any] = {
                "messages": messages,
                "max_tokens": 1,
                "temperature": 0.0,
                "stream": False,
                "cache_prompt": True,
            }
        else:
            url = f"{self.settings.base_url}/completion"
            payload = {
                "prompt": _build_prompt(messages),
                "n_predict": 1,
                "temperature": 0.0,
                "stream": False,
                "cache_prompt": True,
                "stop": ["<end_of_turn>"],
            }
        timeout = httpx.Timeout(connect=10.0, read=900.0, write=30.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
                return response.status_code < 400
        except Exception:  # noqa: BLE001 - warmup is best effort
            return False

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        n_predict: int = 512,
        prefill: bool = False,
    ) -> AsyncIterator[str]:
        temp = self.settings.temperature if temperature is None else temperature
        if not _is_gemma_model(self.settings.model):
            async for chunk in self._stream_openai_chat(
                messages, temperature=temp, n_predict=n_predict, prefill=prefill
            ):
                yield chunk
            return

        prompt = _build_prompt(messages)
        if prefill:
            prompt += _ANSWER_PREFILL
        payload = {
            "prompt": prompt,
            "n_predict": n_predict,
            "temperature": temp,
            "stream": True,
            "cache_prompt": True,
            "stop": ["<end_of_turn>"],
        }

        in_preamble = True
        buf = ""
        last_timings: dict[str, Any] = {}
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.settings.base_url}/completion",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    data = json.loads(raw)
                    if isinstance(data.get("timings"), dict):
                        last_timings = data["timings"]
                    chunk = data.get("content")
                    if not chunk:
                        continue

                    if in_preamble:
                        buf += chunk
                        if _CHANNEL_CLOSE in buf:
                            in_preamble = False
                            rest = (
                                buf[
                                    buf.rindex(_CHANNEL_CLOSE)
                                    + len(_CHANNEL_CLOSE) :
                                ].strip()
                            )
                            buf = ""
                            if rest:
                                yield rest
                        elif _CHANNEL_OPEN in buf:
                            if len(buf) > _MAX_PREAMBLE:
                                in_preamble = False
                                yield buf
                                buf = ""
                        elif not _CHANNEL_OPEN.startswith(buf):
                            in_preamble = False
                            yield buf
                            buf = ""
                        continue

                    yield chunk

        # Latency audit for chat turns (prompt eval + generation, cache hits).
        runlog.log(
            {
                "type": "chat_completion",
                "ttft_ms": round((time.monotonic() - started) * 1000, 1),
                "prompt_n": last_timings.get("prompt_n"),
                "cache_n": last_timings.get("cache_n"),
                "prompt_ms": last_timings.get("prompt_ms"),
                "predicted_n": last_timings.get("predicted_n"),
                "predicted_ms": last_timings.get("predicted_ms"),
            }
        )

    async def _stream_openai_chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        n_predict: int,
        prefill: bool,
    ) -> AsyncIterator[str]:
        """Stream chat via /v1/chat/completions for non-Gemma models."""
        payload: dict[str, Any] = {
            "messages": collapse_system_messages(messages),
            "max_tokens": n_predict,
            "temperature": temperature,
            "stream": True,
            "cache_prompt": True,
        }
        if _is_thinking_model(self.settings.model):
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        last_timings: dict[str, Any] = {}
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.settings.base_url}/v1/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    data = json.loads(raw)
                    if isinstance(data.get("timings"), dict):
                        last_timings = data["timings"]
                    choice = (data.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    chunk = delta.get("content")
                    if chunk:
                        yield chunk

        runlog.log(
            {
                "type": "chat_completion",
                "ttft_ms": round((time.monotonic() - started) * 1000, 1),
                "prompt_n": last_timings.get("prompt_n"),
                "cache_n": last_timings.get("cache_n"),
                "prompt_ms": last_timings.get("prompt_ms"),
                "predicted_n": last_timings.get("predicted_n"),
                "predicted_ms": last_timings.get("predicted_ms"),
            }
        )

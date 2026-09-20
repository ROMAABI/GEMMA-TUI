from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx

from config.settings import Settings

_THOUGHT_PATTERN = re.compile(r"<\|channel>.*?<channel\|>", re.S)
_CHANNEL_OPEN = "<|channel>"
_CHANNEL_CLOSE = "<channel|>"
_MAX_PREAMBLE = 16384


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
        payload = {
            "prompt": prompt,
            "n_predict": n_predict,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "stream": False,
            "cache_prompt": False,
            "stop": ["<end_of_turn>"],
        }
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(f"{self.settings.base_url}/completion", json=payload)
            response.raise_for_status()
            data = response.json()
        return _clean_output(data.get("content", "")).strip()

    async def stream_chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        prompt = _build_prompt(messages)
        payload = {
            "prompt": prompt,
            "n_predict": 2048,
            "temperature": self.settings.temperature,
            "stream": True,
            "cache_prompt": True,
            "stop": ["<end_of_turn>"],
        }

        in_preamble = True
        buf = ""
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

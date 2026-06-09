from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx

from config.settings import Settings

_THOUGHT_PATTERN = re.compile(r"<\|channel>thought\n<channel\|>")


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

    async def stream_chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        prompt = _build_prompt(messages)
        payload = {
            "prompt": prompt,
            "n_predict": 2048,
            "temperature": self.settings.temperature,
            "stream": True,
            "cache_prompt": True,
        }

        preamble = True
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

                    if preamble:
                        buf += chunk
                        # Wait until we've passed the thought preamble
                        if "<channel|>" in buf:
                            preamble = False
                            # Yield everything after the last tag
                            idx = buf.rindex("<channel|>") + len("<channel|>")
                            rest = buf[idx:].strip()
                            if rest:
                                yield rest
                            buf = ""
                        continue

                    yield chunk

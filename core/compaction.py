"""Context compaction: replace old history with a structured checkpoint."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.client import LlamaClient

SUMMARY_TEMPLATE = """## Objective
- [one or two sentences describing what the user is trying to accomplish]

## Requirements
- [constraints, preferences, and scope stated by the user, or "(none)"]

## Decisions
- [decisions already made and why, or "(none)"]

## Work State
### Completed
- [completed work, or "(none)"]
### Active
- [work in progress, or "(none)"]
### Blocked
- [blockers, or "(none)"]

## Next Move
1. [ordered next actions, or "(none)"]

## Relevant Files
- `[path]`: [why it matters]

## Important Context
- [facts needed to continue, or "(none)"]"""

SUMMARY_INSTRUCTIONS = """You are compacting a coding-agent conversation into a checkpoint for another agent.
Summarize only what the user and assistant said and did. Preserve exact file paths, symbols,
commands, error strings, and identifiers. Keep bullets terse. Do not continue the task or call
tools. Return only the structured Markdown below, with no preamble.

""" + SUMMARY_TEMPLATE


@dataclass
class CompactionResult:
    messages: list[dict[str, Any]]
    summary: str
    removed: int
    before_tokens: int
    after_tokens: int
    reason: str = "auto"


def estimate_tokens(messages: list[dict[str, Any]], extra_chars: int = 0) -> int:
    total = extra_chars
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(str(content))
        for call in message.get("tool_calls") or []:
            total += len(json.dumps(call))
        total += 16
    return max(1, total // 4)


def _render_transcript(messages: list[dict[str, Any]], limit: int = 60_000) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "unknown")
        content = message.get("content") or ""
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = call.get("function") or {}
                calls.append(f"{function.get('name')}({function.get('arguments', '')})")
            content = f"{content}\n[tool calls: {', '.join(calls)}]".strip()
        if role == "tool":
            content = f"[{message.get('name', 'tool')}] {content}"
        parts.append(f"<{role}>\n{content}\n</{role}>")
    transcript = "\n\n".join(parts)
    if len(transcript) > limit:
        transcript = "... [earlier history omitted] ...\n\n" + transcript[-limit:]
    return transcript


def _fallback_summary(messages: list[dict[str, Any]]) -> str:
    user_requests = [
        str(m.get("content", "")).strip()
        for m in messages
        if m.get("role") == "user" and m.get("content")
    ]
    files: list[str] = []
    commands: list[str] = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                args = json.loads(function.get("arguments") or "{}")
            except Exception:
                args = {}
            path = args.get("path")
            if path and path not in files:
                files.append(path)
            command = args.get("command")
            if command and command not in commands:
                commands.append(command)
    lines = ["## Objective", f"- {user_requests[-1] if user_requests else '(unknown)'}", ""]
    if user_requests:
        lines.append("## Requirements")
        lines.extend(f"- {request}" for request in user_requests[-5:])
        lines.append("")
    if files:
        lines.append("## Relevant Files")
        lines.extend(f"- `{path}`" for path in files[:15])
        lines.append("")
    if commands:
        lines.append("## Important Context")
        lines.extend(f"- Ran: `{command}`" for command in commands[-8:])
        lines.append("")
    lines.append("## Next Move")
    lines.append("1. Continue from the most recent tool result.")
    return "\n".join(lines)


def _sanitize_recent(recent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop orphaned tool results and incomplete tool-call groups at the cut."""
    # A tool message must follow the assistant message that requested it.
    while recent and recent[0].get("role") == "tool":
        recent.pop(0)
    # If the last assistant message has tool calls without all results, drop it.
    if recent and recent[-1].get("role") == "assistant" and recent[-1].get("tool_calls"):
        expected = {call.get("id") for call in recent[-1]["tool_calls"]}
        recent.pop()
        # Any tool results that belonged to it are now orphaned.
        while recent and recent[0].get("role") == "tool":
            recent.pop(0)
    return recent


class Compactor:
    def __init__(
        self,
        client: LlamaClient,
        *,
        context_limit: int = 32_768,
        threshold: float = 0.8,
        keep_tokens: int = 4_000,
        reserve_tokens: int = 2_000,
    ) -> None:
        self.client = client
        self.context_limit = max(2_000, context_limit)
        self.threshold = min(max(threshold, 0.3), 0.98)
        self.keep_tokens = max(500, keep_tokens)
        self.reserve_tokens = max(0, reserve_tokens)

    def ceiling(self) -> int:
        return max(1_000, int(self.context_limit * self.threshold) - self.reserve_tokens)

    def should_compact(self, messages: list[dict[str, Any]], extra_chars: int = 0) -> bool:
        return estimate_tokens(messages, extra_chars) > self.ceiling()

    def _split(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        system: list[dict[str, Any]] = []
        body: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "system" and not body:
                system.append(message)
            else:
                body.append(message)
        if not body:
            return system, [], []

        # Keep a recent window that fits keep_tokens.
        recent: list[dict[str, Any]] = []
        used = 0
        for message in reversed(body):
            cost = estimate_tokens([message])
            if recent and used + cost > self.keep_tokens:
                break
            recent.insert(0, message)
            used += cost
        older = body[: len(body) - len(recent)]
        return system, older, _sanitize_recent(recent)

    async def compact(
        self,
        messages: list[dict[str, Any]],
        *,
        reason: str = "auto",
        instructions: str = "",
    ) -> CompactionResult | None:
        system, older, recent = self._split(messages)
        if not older:
            return None

        before_tokens = estimate_tokens(messages)
        transcript = _render_transcript(older)
        prompt = (
            "<start_of_turn>system\n"
            + SUMMARY_INSTRUCTIONS
            + "\n<end_of_turn>\n<start_of_turn>user\n"
            + transcript
            + "\n<end_of_turn>\n<start_of_turn>model\n"
        )
        if instructions:
            prompt = prompt.replace(
                "Return only the structured Markdown below, with no preamble.",
                f"Additional user instructions: {instructions}\n"
                "Return only the structured Markdown below, with no preamble.",
            )

        summary = ""
        try:
            summary = await self.client.complete(prompt, n_predict=1_600, temperature=0.2)
        except Exception:
            summary = ""
        if not summary.strip():
            summary = _fallback_summary(older)

        checkpoint = (
            "<conversation-checkpoint>\n"
            "The earlier conversation was compacted. Use this checkpoint to continue.\n\n"
            + summary.strip()
            + "\n</conversation-checkpoint>"
        )
        compacted = [*system, {"role": "system", "content": checkpoint}, *recent]
        after_tokens = estimate_tokens(compacted)
        return CompactionResult(
            messages=compacted,
            summary=summary.strip(),
            removed=len(older),
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            reason=reason,
        )

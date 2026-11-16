"""Hardened agent loop: one provider call per step, structured tool calls."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from core.client import LlamaClient
from core.compaction import Compactor
from core.permissions import PermissionDenied, PermissionManager, PermissionRule, parse_rule
from core.providers import (
    ContextOverflowError,
    NativeToolsUnsupported,
    ProviderRouter,
    ToolCall,
)
from core.tooling import ToolContext, ToolRegistry, ToolResult, build_default_registry
from core.tooling.types import ToolSpec
from core.tools import WorkspaceTools, build_agent_system_prompt
from utils import runlog


@dataclass
class AgentEvent:
    type: str
    content: Any


_BLOCKING_FAILURE_MARKERS = (
    "permission denied",
    "access denied",
    "operation not permitted",
    "escapes workspace",
    "timed out",
    "timeout",
    "unknown tool",
    "not allowed",
    "denied",
    "could not start",
    "failed to",
)

_FAILURE_ACK = re.compile(
    r"\b(?:fail(?:ed|ure)?|error|could\s+not|couldn't|unable|denied|"
    r"permission|not\s+possible|did\s+not|didn't|outside\s+the\s+workspace|"
    r"not\s+completed|incomplete)\b",
    re.IGNORECASE,
)

_SUCCESS_CLAIM = re.compile(
    r"\b(?:task|everything|all(?:\s+of\s+it)?|it)\s+(?:is|was|has\s+been)?\s*"
    r"(?:complete|completed|done|finished|successful(?:ly)?)\b"
    r"|\b(?:completed|finished|done|success(?:ful(?:ly)?)?)\b",
    re.IGNORECASE,
)

_NEGATION_BEFORE = re.compile(
    r"(?:\bnot|\bnever|\bno|\bcannot|can't|couldn't|didn't|doesn't|isn't|"
    r"wasn't|\bunable)\s*$",
    re.IGNORECASE,
)

_MUTATING_TOOLS = {"write", "edit", "patch"}
_VERIFYING_TOOLS = {
    "bash",
    "read",
    "grep",
    "glob",
    "list_dir",
    "git_diff",
    "git_status",
    "lsp_diagnostics",
}

# Shell verbs are used to tell "this command changed something" apart from
# "this command only looked at something". Only the leading verb matters.
_SHELL_VERB_ALIASES = {"python3": "python", "pip": "python", "pip3": "python"}
_MUTATING_SHELL_VERBS = {
    "rm", "rmdir", "mkdir", "mv", "cp", "touch", "chmod", "chown", "ln",
    "tee", "tar", "unzip", "zip",
}
# Sub-commands that change state even though the leading verb does not
# ("git commit", "pip install"). A false positive only makes the loop guard
# more lenient, so this set is deliberately generous.
_MUTATING_SUBCOMMANDS = {
    "add", "commit", "install", "uninstall", "update", "upgrade", "checkout",
    "merge", "rebase", "reset", "apply", "stash", "push", "pull", "init",
    "clone", "format", "save", "restore", "clean",
}


class AgentOrchestrator:
    """Executes a multi-step coding agent loop with native or prompt tools."""

    def __init__(
        self,
        client: LlamaClient | None = None,
        tools: WorkspaceTools | None = None,
        max_steps: int = 10,
        confirm_commands: bool = False,
        *,
        settings: Any = None,
        registry: ToolRegistry | None = None,
        permission: PermissionManager | None = None,
        approval: Any = None,
        workspace_dir: str | None = None,
        system_prompt: str | None = None,
        store: Any = None,
        session_id: str | None = None,
        provider_mode: str | None = None,
        parallel_tools: int | None = None,
        tool_timeout: int | None = None,
        compactor: Compactor | None = None,
        skills: Any = None,
        extra_context: str = "",
    ) -> None:
        if client is None:
            if settings is None:
                from config.settings import Settings

                settings = Settings()
            client = LlamaClient(settings)
        self.client = client
        self.settings = settings or getattr(client, "settings", None)
        self.store = store
        self.session_id = session_id

        if workspace_dir:
            root = workspace_dir
        elif tools is not None:
            root = tools.root
        elif self.settings is not None:
            root = self.settings.workspace_dir
        else:
            root = "."
        self.workspace = str(root)
        self.tools = tools or WorkspaceTools(root_dir=root)

        self.registry = registry or build_default_registry()
        self.skills = skills
        if skills is not None:
            for spec in getattr(skills, "tool_specs", lambda: [])():
                if self.registry.get(spec.name) is None:
                    self.registry.register(spec)

        self.max_steps = max(1, int(max_steps or getattr(self.settings, "agent_max_steps", 10)))
        self.parallel_tools = max(
            1,
            int(
                parallel_tools
                if parallel_tools is not None
                else getattr(self.settings, "agent_parallel_tools", 4)
            ),
        )
        self.tool_timeout = max(
            5,
            int(
                tool_timeout
                if tool_timeout is not None
                else getattr(self.settings, "agent_tool_timeout", 180)
            ),
        )

        if permission is not None:
            self.permission = permission
        else:
            mode = (
                getattr(self.settings, "permission_mode", None)
                or ("ask" if confirm_commands else "auto")
            )
            rules: list[PermissionRule] = []
            for raw in getattr(self.settings, "permission_rules", []) or []:
                try:
                    rules.append(parse_rule(raw))
                except ValueError:
                    continue
            self.permission = PermissionManager(
                mode=mode,
                rules=rules,
                approval=approval,
                session_id=session_id,
            )
        if approval is not None:
            self.permission.approval = approval

        self.provider = ProviderRouter(
            self.settings,
            client,
            mode=provider_mode or getattr(self.settings, "provider", "auto"),
        )

        if compactor is not None:
            self.compactor = compactor
        elif self.settings is not None:
            self.compactor = Compactor(
                client,
                context_limit=getattr(self.settings, "context_limit", 32_768),
                threshold=getattr(self.settings, "compact_threshold", 0.8),
                keep_tokens=getattr(self.settings, "compact_keep_tokens", 4_000),
            )
        else:
            self.compactor = None

        skills_summary = ""
        if skills is not None:
            try:
                skills_summary = skills.summary()
            except Exception:
                skills_summary = ""
        self.system_prompt = system_prompt or build_agent_system_prompt(
            self.workspace,
            self.registry.prompt_catalog(),
            skills_summary,
        )
        if extra_context.strip():
            # Web research context rides into the agent's system prompt.
            self.system_prompt = f"{self.system_prompt}\n\n{extra_context.strip()}"
        self._permission_lock = asyncio.Lock()

    # ── Conversation preparation ────────────────────────────────────────

    def _prepare(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        checkpoints: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content")
            if content is None:
                content = ""
            if role == "system":
                if "<conversation-checkpoint>" in str(content):
                    checkpoints.append({"role": "system", "content": str(content)})
                continue
            if role not in ("user", "assistant", "tool"):
                continue
            cleaned: dict[str, Any] = {"role": role, "content": str(content)}
            if role == "assistant" and message.get("tool_calls"):
                cleaned["tool_calls"] = message["tool_calls"]
            if role == "tool":
                cleaned["tool_call_id"] = message.get("tool_call_id") or ""
                cleaned["name"] = message.get("name") or "tool"
            prepared.append(cleaned)
        return [{"role": "system", "content": self.system_prompt}, *checkpoints, *prepared]

    # ── Streaming one provider step ─────────────────────────────────────

    async def _stream_step(
        self,
        conversation: list[dict[str, Any]],
        state: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        tools = self.registry.openai_tools()
        async for event in self.provider.stream(
            conversation,
            tools,
            temperature=getattr(self.settings, "temperature", 0.7),
            max_tokens=getattr(self.settings, "agent_max_tokens", 4096),
        ):
            if event.type == "text":
                state["text"].append(event.text)
                yield AgentEvent("text", event.text)
            elif event.type == "reasoning":
                state["reasoning"].append(event.text)
                yield AgentEvent("reasoning", event.text)
            elif event.type == "tool_calls":
                state["tool_calls"] = event.tool_calls
            elif event.type == "usage":
                state["usage"].update(event.usage)
                yield AgentEvent("usage", event.usage)
            elif event.type == "done":
                state["finish_reason"] = event.finish_reason

    # ── Prompt cache warmup ─────────────────────────────────────────────

    async def warmup(self) -> bool:
        """Prime the model's prompt cache with this agent's exact prefix.

        On CPU-only servers processing the system prompt and tool schemas can
        take a minute. Doing it once ahead of time keeps the first real step
        fast, and the cached prefix is reused by every later step.
        """
        warmup = getattr(self.provider, "warmup", None)
        if warmup is None:
            return False
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": "Warm up the prompt cache."},
        ]
        try:
            return bool(await warmup(messages, self.registry.openai_tools()))
        except Exception:  # noqa: BLE001 - warmup must never break a run
            return False

    # ── Main loop ───────────────────────────────────────────────────────

    async def run(
        self,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        conversation = self._prepare(messages)
        step = 0
        empty_retries = 0
        completion_retries = 0
        verification_retries = 0
        unresolved: list[dict[str, Any]] = []
        last_mutation_step = 0
        last_verification_step = 0
        last_verification: dict[str, Any] | None = None
        last_call_signature = ""
        repeat_count = 0
        loop_stopped = False
        stop_reason = ""
        signature_history: list[str] = []
        nudged_signatures: set[str] = set()
        mutation_seen = False
        idle_steps = 0
        idle_nudged = False
        # Latency audit for this turn.
        turn_started = time.monotonic()
        first_token_at: float | None = None
        tool_ms = 0.0
        tool_count = 0
        finish_label = "complete"

        # Latency audit helper: one record per turn, whatever the exit path.
        def log_turn(outcome: str) -> None:
            total_ms = round((time.monotonic() - turn_started) * 1000, 1)
            ttft_ms = (
                round((first_token_at - turn_started) * 1000, 1) if first_token_at else None
            )
            runlog.log(
                {
                    "type": "turn",
                    "outcome": outcome,
                    "steps": step,
                    "total_ms": total_ms,
                    "ttft_ms": ttft_ms,
                    "tool_ms": round(tool_ms, 1),
                    "tool_calls": tool_count,
                    "stop_reason": stop_reason,
                }
            )

        while step < self.max_steps:
            step += 1
            yield AgentEvent("step_start", {"step": step, "max_steps": self.max_steps})

            # Compact before the call when the window is getting full.
            if self.compactor is not None:
                try:
                    extra = len(json.dumps(self.registry.openai_tools()))
                    if self.compactor.should_compact(conversation, extra_chars=extra):
                        result = await self.compactor.compact(conversation, reason="auto")
                        if result is not None:
                            conversation = result.messages
                            self._persist_checkpoint(result.summary, result.reason)
                            yield AgentEvent(
                                "compaction",
                                {
                                    "reason": result.reason,
                                    "removed": result.removed,
                                    "before_tokens": result.before_tokens,
                                    "after_tokens": result.after_tokens,
                                },
                            )
                except Exception as exc:  # noqa: BLE001 - compaction must not kill the run
                    yield AgentEvent("compaction_error", str(exc))

            state: dict[str, Any] = {
                "text": [],
                "reasoning": [],
                "tool_calls": [],
                "usage": {},
                "finish_reason": "",
            }

            overflow_retry = False
            while True:
                try:
                    async for event in self._stream_step(conversation, state):
                        if event.type in ("text", "reasoning") and first_token_at is None:
                            first_token_at = time.monotonic()
                        yield event
                    break
                except ContextOverflowError:
                    if overflow_retry or self.compactor is None:
                        yield AgentEvent(
                            "error",
                            "Context is too large and could not be compacted. "
                            "Start a new session or raise the context limit.",
                        )
                        log_turn("context_overflow")
                        return
                    overflow_retry = True
                    result = await self.compactor.compact(conversation, reason="overflow")
                    if result is None:
                        yield AgentEvent("error", "Context overflow: nothing left to compact.")
                        return
                    conversation = result.messages
                    self._persist_checkpoint(result.summary, "overflow")
                    yield AgentEvent(
                        "compaction",
                        {
                            "reason": "overflow",
                            "removed": result.removed,
                            "before_tokens": result.before_tokens,
                            "after_tokens": result.after_tokens,
                        },
                    )
                    state = {
                        "text": [],
                        "reasoning": [],
                        "tool_calls": [],
                        "usage": {},
                        "finish_reason": "",
                    }
                except NativeToolsUnsupported as exc:
                    yield AgentEvent(
                        "error",
                        f"Native tool calling is not supported by this server/model: {exc}",
                    )
                    log_turn("tools_unsupported")
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    yield AgentEvent("error", f"Model stream error: {type(exc).__name__}: {exc}")
                    log_turn("stream_error")
                    return

            text = "".join(state["text"])
            reasoning = "".join(state["reasoning"])
            tool_calls: list[ToolCall] = state["tool_calls"] or []
            if reasoning:
                yield AgentEvent("reasoning_done", reasoning)
            yield AgentEvent(
                "provider",
                {"name": self.provider.last_provider, "finish_reason": state["finish_reason"]},
            )

            if not tool_calls:
                if not text.strip() and empty_retries < 1:
                    empty_retries += 1
                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous response was empty. Continue the task. "
                                "Call a tool or provide a final answer."
                            ),
                        }
                    )
                    continue

                needs_correction = bool(unresolved) and (
                    not text.strip()
                    or self._claims_success(text)
                    or not self._acknowledges_failure(text)
                )
                if (
                    needs_correction
                    and completion_retries < 1
                    and step < self.max_steps
                ):
                    completion_retries += 1
                    conversation.append(
                        {"role": "user", "content": self._completion_nudge(unresolved)}
                    )
                    yield AgentEvent(
                        "completion_warning",
                        {"reason": "unresolved_failures", "failures": unresolved},
                    )
                    continue

                stale_verification = (
                    last_mutation_step > last_verification_step > 0
                    and last_verification is not None
                    and last_verification.get("tool") == "bash"
                )
                needs_verification = stale_verification and (
                    self._claims_success(text)
                    or not self._acknowledges_failure(text)
                )
                if (
                    needs_verification
                    and verification_retries < 1
                    and step < self.max_steps
                ):
                    verification_retries += 1
                    conversation.append(
                        {
                            "role": "user",
                            "content": self._verification_nudge(last_verification),
                        }
                    )
                    yield AgentEvent(
                        "completion_warning",
                        {
                            "reason": "stale_verification",
                            "last_mutation_step": last_mutation_step,
                            "last_verification_step": last_verification_step,
                        },
                    )
                    continue

                if not text.strip():
                    text = self._incomplete_summary(unresolved)
                elif unresolved and (
                    not self._acknowledges_failure(text)
                    or self._claims_success(text)
                ):
                    text = self._annotate_incomplete(text, unresolved)
                    yield AgentEvent(
                        "completion_warning",
                        {"reason": "annotated_completion", "failures": unresolved},
                    )
                elif stale_verification and (
                    self._claims_success(text)
                    or not self._acknowledges_failure(text)
                ):
                    text = self._annotate_stale(text)
                    yield AgentEvent(
                        "completion_warning", {"reason": "annotated_stale_verification"}
                    )

                conversation.append({"role": "assistant", "content": text})
                self._persist_message("assistant", text)
                yield AgentEvent("assistant_message", {"role": "assistant", "content": text})
                yield AgentEvent("complete", text)
                log_turn("complete")
                return

            # Normalize IDs so every call is addressable.
            for index, call in enumerate(tool_calls):
                if not call.id:
                    call.id = f"call_{step}_{index}"

            assistant_message = self._assistant_tool_message(text, tool_calls)
            conversation.append(assistant_message)
            self._persist_message(
                "assistant",
                text,
                metadata={"tool_calls": assistant_message["tool_calls"]},
            )
            yield AgentEvent("assistant_message", assistant_message)

            signature = "|".join(
                f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"
                for call in tool_calls
            )
            # Look at a short window, not just consecutive steps: models often
            # alternate between two calls (ls, test, ls, test) and never repeat
            # the same one twice in a row.
            signature_history.append(signature)
            if len(signature_history) > 6:
                signature_history.pop(0)
            repeat_count = signature_history.count(signature)
            last_call_signature = signature

            results: dict[int, ToolResult] = {}
            try:
                async for event in self._execute_calls(tool_calls, step, results):
                    yield event
            except BaseException:
                # The assistant tool-call message is already persisted. Without
                # matching tool messages the next provider call is invalid, so
                # close the loop before propagating cancellation/errors.
                self._persist_interrupted_tool_results(tool_calls, results)
                raise
            ordered: list[tuple[ToolCall, ToolResult]] = []
            step_mutated = False
            for index, call in enumerate(tool_calls):
                result = results.get(index) or ToolResult.failure(
                    "Tool did not return a result (cancelled or timed out)."
                )
                exec_ms = result.metadata.get("exec_ms")
                if isinstance(exec_ms, (int, float)):
                    tool_ms += float(exec_ms)
                    tool_count += 1
                self._track_failure(unresolved, call, result)
                if result.ok:
                    if self._is_mutating_call(call, result):
                        step_mutated = True
                    if call.name in _MUTATING_TOOLS:
                        # A no-op write did not change the verified state.
                        if call.name != "write" or result.metadata.get("changed", True):
                            last_mutation_step = step
                    elif call.name in _VERIFYING_TOOLS:
                        last_verification_step = step
                        last_verification = {
                            "tool": call.name,
                            "args": call.arguments,
                            "output": result.output,
                            "step": step,
                        }
                ordered.append((call, result))
                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": result.output,
                }
                conversation.append(tool_message)
                self._persist_message(
                    "tool",
                    result.output,
                    tool_call_id=call.id,
                    name=call.name,
                    metadata={"ok": result.ok, "tool_name": call.name},
                )
                yield AgentEvent("tool_message", tool_message)

            if step_mutated:
                mutation_seen = True
                idle_steps = 0
                idle_nudged = False
            elif mutation_seen:
                idle_steps += 1

            if repeat_count >= 3 and signature not in nudged_signatures:
                nudged_signatures.add(signature)
                warning = (
                    "You have called the same tool(s) with identical arguments "
                    f"{repeat_count} times in the last few steps. The result will not "
                    "change. Use a different approach or give your final answer now."
                )
                conversation.append({"role": "user", "content": warning})
                yield AgentEvent(
                    "loop_warning",
                    {"repeat_count": repeat_count, "signature": signature},
                )
            if repeat_count >= 4 or (
                len(signature_history) >= 6 and len(set(signature_history)) <= 2
            ):
                loop_stopped = True
                stop_reason = (
                    "Stopped because the same few tool calls were repeated without "
                    "making progress."
                )
                break

            # After a successful change, a model that only reads for several
            # steps in a row is usually re-checking work that is already done.
            if mutation_seen and idle_steps >= 3 and not idle_nudged and not unresolved:
                idle_nudged = True
                conversation.append(
                    {"role": "user", "content": self._idle_nudge(idle_steps)}
                )
                yield AgentEvent(
                    "loop_warning",
                    {"reason": "redundant_verification", "idle_steps": idle_steps},
                )
            if mutation_seen and idle_steps >= 5 and not unresolved:
                loop_stopped = True
                stop_reason = (
                    "Stopped because the requested change was already applied and "
                    f"the last {idle_steps} steps only re-read the result."
                )
                break

        # Step limit reached: ask for a final summary without tools.
        final = await self._finalize(conversation)
        if final:
            self._persist_message("assistant", final)
            yield AgentEvent("complete", final)
        elif loop_stopped:
            yield AgentEvent(
                "complete",
                f"{stop_reason} Review the tool output above and give the agent a "
                "more specific instruction.",
            )
        else:
            yield AgentEvent(
                "complete",
                f"Reached the {self.max_steps}-step limit. Review the tool output above for progress.",
            )
        log_turn("step_limit" if not loop_stopped else "loop_stopped")

    # ── Tool execution ──────────────────────────────────────────────────

    @staticmethod
    def _assistant_tool_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in calls
            ],
        }

    @staticmethod
    def _call_key(call: ToolCall) -> tuple[str, str]:
        """Stable identity for resolving a failure with a later success."""
        args = call.arguments or {}
        if call.name == "bash":
            command = args.get("command") or args.get("cmd") or ""
            return ("bash", str(command).strip())
        for key in ("path", "file_path", "file", "workdir", "cwd", "pattern"):
            value = args.get(key)
            if value:
                return ("fs", str(value).strip().lstrip("./") or ".")
        try:
            return (call.name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        except Exception:
            return (call.name, str(args))

    @staticmethod
    def _bash_shape(command: str) -> tuple[str, set[str]]:
        """Return ``(verb, targets)`` for a shell command.

        ``rm snake`` and ``rm -r snake`` share the verb ``rm`` and the target
        ``snake``, which lets a corrected command clear the earlier failure.
        """
        tokens = [tok for tok in re.split(r"[\s'\"|;&()]+", command or "") if tok]
        verb = ""
        targets: set[str] = set()
        for token in tokens:
            if token.startswith("-"):
                continue
            if not verb:
                verb = _SHELL_VERB_ALIASES.get(token, token)
                continue
            cleaned = token.strip("'\"`").rstrip("/")
            if cleaned and not cleaned.isdigit():
                targets.add(cleaned.lstrip("./") or ".")
        return verb, targets

    @classmethod
    def _resolves_bash_failure(
        cls, item: dict[str, Any], verb: str, targets: set[str]
    ) -> bool:
        """Did this successful command fix the earlier failed one?"""
        if item.get("tool") != "bash" or not verb or not targets:
            return False
        args = item.get("args") or {}
        failed_verb, failed_targets = cls._bash_shape(str(args.get("command") or ""))
        if failed_verb != verb:
            return False
        return bool(failed_targets & targets)

    @classmethod
    def _is_mutating_call(cls, call: ToolCall, result: ToolResult) -> bool:
        """Did this call change the workspace?"""
        if not result.ok:
            return False
        if call.name in _MUTATING_TOOLS:
            return call.name != "write" or result.metadata.get("changed", True)
        if call.name == "bash":
            verb, targets = cls._bash_shape(str(call.arguments.get("command") or ""))
            if verb in _MUTATING_SHELL_VERBS:
                return True
            return bool(targets & _MUTATING_SUBCOMMANDS)
        return False

    @staticmethod
    def _is_blocking_failure(call: ToolCall, result: ToolResult) -> bool:
        if result.ok:
            return False
        text = f"{result.output}\n{result.error or ''}".lower()
        if any(marker in text for marker in _BLOCKING_FAILURE_MARKERS):
            return True
        exit_code = (result.metadata or {}).get("exit_code")
        if exit_code not in (None, 0):
            return True
        # Mutating/terminal tools that reject their arguments did not perform
        # the requested operation, so their failure is blocking.
        return call.name in {"write", "edit", "patch", "bash"}

    @classmethod
    def _track_failure(
        cls,
        unresolved: list[dict[str, Any]],
        call: ToolCall,
        result: ToolResult,
    ) -> None:
        key = cls._call_key(call)
        if result.ok:
            unresolved[:] = [item for item in unresolved if item["key"] != key]
            # A corrected command is a different string ("rm x" vs "rm -r x"),
            # so also clear earlier failures of the same operation on the same
            # target. Otherwise a fixed mistake still looks unresolved.
            if call.name == "bash":
                verb, targets = cls._bash_shape(
                    str(call.arguments.get("command") or "")
                )
                if verb and targets:
                    unresolved[:] = [
                        item
                        for item in unresolved
                        if not cls._resolves_bash_failure(item, verb, targets)
                    ]
            return
        if not cls._is_blocking_failure(call, result):
            return
        unresolved[:] = [item for item in unresolved if item["key"] != key]
        unresolved.append(
            {
                "key": key,
                "tool": call.name,
                "args": call.arguments,
                "output": result.output,
            }
        )

    @staticmethod
    def _failure_lines(unresolved: list[dict[str, Any]], limit: int = 5) -> list[str]:
        lines: list[str] = []
        for item in unresolved[-limit:]:
            detail = " ".join(str(item.get("output") or "").split())
            if len(detail) > 240:
                detail = detail[:237] + "..."
            lines.append(f"- {item['tool']}: {detail}")
        return lines

    @classmethod
    def _completion_nudge(cls, unresolved: list[dict[str, Any]]) -> str:
        return (
            "Some tool calls failed and were not resolved:\n"
            + "\n".join(cls._failure_lines(unresolved))
            + "\n\nDo not claim the task is complete. Either fix the failure with a "
            "different approach, or tell the user clearly that the task could not be "
            "completed and explain why. If access was denied by the workspace or OS "
            "permissions, say what the user must change."
        )

    @classmethod
    def _incomplete_summary(cls, unresolved: list[dict[str, Any]]) -> str:
        if not unresolved:
            return (
                "The agent stopped without a final answer. Review the tool output "
                "above; the task may not be complete."
            )
        return (
            "I could not confirm that the task completed. These tool calls failed:\n"
            + "\n".join(cls._failure_lines(unresolved))
            + "\n\nThe task may be incomplete."
        )

    @classmethod
    def _annotate_incomplete(cls, text: str, unresolved: list[dict[str, Any]]) -> str:
        note = (
            "⚠️ Completion not verified. These tool calls failed and were not "
            "resolved:\n"
            + "\n".join(cls._failure_lines(unresolved))
        )
        return f"{note}\n\n{text}".strip()

    @classmethod
    def _verification_nudge(cls, verification: dict[str, Any] | None) -> str:
        lines = [
            "You changed files after your last successful verification."
        ]
        if verification:
            args = verification.get("args") or {}
            detail = args.get("command") if verification.get("tool") == "bash" else None
            if detail:
                lines.append(f"Your last successful check was: `{detail}`")
            output = " ".join(str(verification.get("output") or "").split())
            if output:
                if len(output) > 300:
                    output = output[:297] + "..."
                lines.append(f"It produced: {output}")
        lines.append(
            "Re-run that check now and report the actual result before claiming "
            "the task is complete. If it fails, fix it or tell the user that the "
            "task is not complete."
        )
        return "\n\n".join(lines)

    @staticmethod
    def _annotate_stale(text: str) -> str:
        return (
            "⚠️ Completion not verified: files were changed after the last "
            "successful check. Re-run the relevant test before trusting this "
            "result.\n\n"
            f"{text}"
        ).strip()

    @staticmethod
    def _idle_nudge(idle_steps: int) -> str:
        return (
            f"The requested change was already applied and the last {idle_steps} "
            "steps only re-read the result without changing anything. If the tool "
            "output already shows the goal was reached, stop calling tools and "
            "give your final answer now. Only keep going if something is still "
            "actually broken."
        )

    @staticmethod
    def _acknowledges_failure(text: str) -> bool:
        return bool(_FAILURE_ACK.search(text))

    @staticmethod
    def _claims_success(text: str) -> bool:
        for match in _SUCCESS_CLAIM.finditer(text):
            # Only an immediately preceding negation ("not completed",
            # "couldn't finish") cancels the claim. A distant "no" in another
            # sentence must not hide a real completion claim.
            prefix = text[max(0, match.start() - 24) : match.start()]
            if not _NEGATION_BEFORE.search(prefix):
                return True
        return False

    def _permission_resources(self, spec: ToolSpec, args: dict[str, Any]) -> list[str]:
        if spec.permission == "bash":
            command = args.get("command") or args.get("cmd") or ""
            return [str(command)]
        if spec.permission in ("read", "edit"):
            paths: list[str] = []
            for key in ("path", "file_path", "file", "workdir", "cwd"):
                value = args.get(key)
                if value:
                    paths.append(str(value).lstrip("./") or ".")
            patch_text = args.get("patch_text") or args.get("patch")
            if isinstance(patch_text, str):
                for match in re.finditer(
                    r"\*\*\* (?:Add|Update|Delete) File:\s*(.+)", patch_text
                ):
                    paths.append(match.group(1).strip().lstrip("./"))
                for match in re.finditer(r"\*\*\* Move to:\s*(.+)", patch_text):
                    paths.append(match.group(1).strip().lstrip("./"))
            return paths or ["*"]
        return ["*"]

    async def _execute_one(
        self,
        index: int,
        call: ToolCall,
        step: int,
        semaphore: asyncio.Semaphore,
    ) -> tuple[int, ToolCall, ToolResult]:
        async with semaphore:
            spec = self.registry.get(call.name)
            if call.error:
                return index, call, ToolResult.failure(call.error)
            if spec is None:
                available = ", ".join(sorted(self.registry.names()))
                return index, call, ToolResult.failure(
                    f"Unknown tool '{call.name}'. Available tools: {available}"
                )

            resources = self._permission_resources(spec, call.arguments)
            try:
                async with self._permission_lock:
                    await self.permission.authorize(
                        spec.permission,
                        resources,
                        tool=call.name,
                        args=call.arguments,
                    )
            except PermissionDenied as exc:
                return index, call, ToolResult.failure(str(exc))
            except Exception as exc:  # noqa: BLE001
                return index, call, ToolResult.failure(f"Permission check failed: {exc}")

            context = ToolContext(
                root=self.workspace,
                session_id=self.session_id,
                settings=self.settings,
                permission=self.permission,
            )
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self.registry.execute(call.name, call.arguments, context),
                    timeout=self.tool_timeout,
                )
            except asyncio.TimeoutError:
                result = ToolResult.failure(
                    f"Tool '{call.name}' timed out after {self.tool_timeout}s"
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                result = ToolResult.failure(f"Tool '{call.name}' failed: {exc}")
            # Latency + exact-output audit: this is byte-for-byte what the
            # model receives as the tool message, so the UI can be checked
            # against the same digest.
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            runlog.log(
                {
                    "type": "tool",
                    "name": call.name,
                    "step": step,
                    "ok": result.ok,
                    "ms": elapsed_ms,
                    **runlog.output_for_log(result.output),
                }
            )
            result.metadata.setdefault("exec_ms", elapsed_ms)
            return index, call, result

    async def _execute_calls(
        self,
        calls: list[ToolCall],
        step: int,
        results: dict[int, ToolResult],
    ) -> AsyncIterator[AgentEvent]:
        for call in calls:
            yield AgentEvent(
                "tool_start",
                {
                    "id": call.id,
                    "name": call.name,
                    "args": call.arguments,
                    "step": step,
                },
            )

        semaphore = asyncio.Semaphore(self.parallel_tools)
        tasks = [
            asyncio.create_task(self._execute_one(index, call, step, semaphore))
            for index, call in enumerate(calls)
        ]
        try:
            for completed in asyncio.as_completed(tasks):
                index, call, result = await completed
                results[index] = result
                yield AgentEvent(
                    "tool_finish",
                    {
                        "id": call.id,
                        "name": call.name,
                        "args": call.arguments,
                        "result": result.output,
                        "ok": result.ok,
                        "step": step,
                    },
                )
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    # ── Finalization and persistence ────────────────────────────────────

    def _persist_interrupted_tool_results(
        self,
        calls: list[ToolCall],
        results: dict[int, ToolResult],
    ) -> None:
        """Persist a tool message for every call after an interrupted step.

        Completed calls keep their real output; unfinished calls get a failure
        message so the session never contains an assistant tool call without a
        matching tool response.
        """
        if self.store is None or not self.session_id:
            return
        for index, call in enumerate(calls):
            result = results.get(index)
            if result is None:
                output = f"Tool '{call.name}' did not complete (cancelled)."
                ok = False
            else:
                output = result.output
                ok = result.ok
            try:
                self._persist_message(
                    "tool",
                    output,
                    tool_call_id=call.id,
                    name=call.name,
                    metadata={
                        "ok": ok,
                        "tool_name": call.name,
                        "interrupted": result is None,
                    },
                )
            except Exception:
                pass

    async def _finalize(self, conversation: list[dict[str, Any]]) -> str:
        nudge = {
            "role": "user",
            "content": (
                "You have reached the step limit. Do not call any more tools. "
                "Summarize what you completed, what you verified, and what remains."
            ),
        }
        text_parts: list[str] = []
        try:
            async for event in self.provider.stream(
                [*conversation, nudge],
                [],
                temperature=getattr(self.settings, "temperature", 0.7),
                max_tokens=1_200,
            ):
                if event.type == "text":
                    text_parts.append(event.text)
        except Exception:
            return ""
        return "".join(text_parts).strip()

    def _persist_message(
        self,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> None:
        if self.store is None or not self.session_id:
            return
        try:
            self.store.add_message(
                self.session_id,
                role,
                content,
                metadata=metadata,
                tool_call_id=tool_call_id,
                name=name,
            )
        except Exception:
            pass

    def _persist_checkpoint(self, summary: str, reason: str) -> None:
        if self.store is None or not self.session_id:
            return
        try:
            self.store.add_message(
                self.session_id,
                "system",
                "<conversation-checkpoint>\n" + summary + "\n</conversation-checkpoint>",
                metadata={"kind": "checkpoint", "reason": reason},
            )
            set_summary = getattr(self.store, "set_summary", None)
            if callable(set_summary):
                set_summary(self.session_id, summary)
        except Exception:
            pass

    async def compact_now(self) -> str | None:
        """Force a compaction of the current session and return the summary."""
        if self.compactor is None or self.store is None or not self.session_id:
            return None
        messages = self.store.context_messages(self.session_id)
        result = await self.compactor.compact(self._prepare(messages), reason="manual")
        if result is None:
            return None
        self._persist_checkpoint(result.summary, "manual")
        return result.summary

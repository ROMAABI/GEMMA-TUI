"""Shell execution tool with process-group cancellation and bounded output."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from core.tooling.types import (
    ToolContext,
    ToolResult,
    ToolSpec,
    integer_prop,
    object_schema,
    string_prop,
)

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
MAX_STREAM_CHARS = 20_000


def _clip(text: str, limit: int = MAX_STREAM_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return (
        text[:head]
        + f"\n... [truncated {len(text) - head - tail} characters] ...\n"
        + text[-tail:]
    )


async def run_bash(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    command = args.get("command") or args.get("cmd") or ""
    if not isinstance(command, str) or not command.strip():
        return ToolResult.failure("bash requires a non-empty 'command' argument")

    timeout = args.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = max(1, min(int(timeout), MAX_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    workdir = args.get("workdir") or args.get("cwd") or "."
    try:
        cwd = context.resolve(workdir)
    except ValueError as exc:
        return ToolResult.failure(str(exc))
    if not cwd.exists():
        return ToolResult.failure(f"Working directory does not exist: {workdir}")
    if not cwd.is_dir():
        return ToolResult.failure(f"Working directory is not a directory: {workdir}")

    env = os.environ.copy()
    env.setdefault("GIT_PAGER", "cat")
    env.setdefault("PAGER", "cat")
    env.setdefault("TERM", "dumb")
    env.setdefault("NO_COLOR", "1")

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"Could not start command: {exc}")

    timed_out = False
    try:
        stdout_data, stderr_data = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=3)
        except Exception:
            stdout_data, stderr_data = b"", b""
    except asyncio.CancelledError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        raise

    stdout = stdout_data.decode("utf-8", errors="replace").strip()
    stderr = stderr_data.decode("utf-8", errors="replace").strip()
    exit_code = proc.returncode

    parts: list[str] = []
    if timed_out:
        parts.append(f"Command timed out after {timeout}s and was terminated.")
    parts.append(f"Exit code: {exit_code}")
    if stdout:
        parts.append(f"STDOUT:\n{_clip(stdout)}")
    if stderr:
        parts.append(f"STDERR:\n{_clip(stderr)}")
    if not stdout and not stderr:
        parts.append("(no output)")
    lowered = stderr.lower()
    if exit_code != 0 and (
        "permission denied" in lowered or "operation not permitted" in lowered
    ):
        parts.append(
            "NOTE: The operating system denied this command. Do not retry the same "
            "command; report the failure and tell the user what access or path must change."
        )

    output = "\n\n".join(parts)
    return ToolResult(
        ok=not timed_out and exit_code == 0,
        output=output,
        metadata={
            "exit_code": exit_code,
            "timed_out": timed_out,
            "command": command,
            "cwd": context.relative(cwd),
        },
    )


def shell_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="bash",
            description=(
                "Execute a shell command in the workspace. Use for builds, tests, git, package "
                "managers, and inspecting the environment. Commands run non-interactively; "
                "never start long-lived interactive programs."
            ),
            parameters=object_schema(
                {
                    "command": string_prop("Shell command to execute"),
                    "timeout": integer_prop(
                        f"Timeout in seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT})",
                        minimum=1,
                        maximum=MAX_TIMEOUT,
                    ),
                    "workdir": string_prop("Workspace-relative working directory (default '.')"),
                    "description": string_prop("Short human-readable description of the command"),
                },
                ["command"],
            ),
            handler=run_bash,
            permission="bash",
            aliases=("run_command", "shell", "execute"),
            max_output_chars=45_000,
        )
    ]

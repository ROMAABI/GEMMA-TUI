"""Git tools built on argv-safe subprocess calls."""

from __future__ import annotations

import asyncio
from typing import Any

from core.tooling.types import (
    ToolContext,
    ToolResult,
    ToolSpec,
    boolean_prop,
    integer_prop,
    object_schema,
    string_prop,
)


async def _git(
    context: ToolContext,
    *args: str,
    timeout: int = 30,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(context.root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"git {' '.join(args)} timed out"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


async def _ensure_repo(context: ToolContext) -> str | None:
    code, out, err = await _git(context, "rev-parse", "--is-inside-work-tree")
    if code != 0 or out.strip() != "true":
        detail = err.strip() or "not a git repository"
        return f"Not a git repository. Run 'git init' first.\n{detail}"
    return None


async def git_status(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    problem = await _ensure_repo(context)
    if problem:
        return ToolResult.failure(problem)
    code, out, err = await _git(context, "status", "--short", "--branch")
    if code != 0:
        return ToolResult.failure(err or "git status failed")
    changes = [line for line in out.splitlines() if line and not line.startswith("##")]
    if not changes:
        return ToolResult.success("Working tree clean.", status=out)
    return ToolResult.success(out, status=out)


async def git_diff(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    problem = await _ensure_repo(context)
    if problem:
        return ToolResult.failure(problem)
    command = ["diff", "--no-color"]
    if args.get("staged"):
        command.append("--staged")
    if args.get("stat"):
        command.append("--stat")
    path = args.get("path")
    if path:
        try:
            target = context.resolve(path, must_exist=True)
        except (ValueError, FileNotFoundError) as exc:
            return ToolResult.failure(str(exc))
        command.extend(["--", context.relative(target)])
    code, out, err = await _git(context, *command)
    if code != 0:
        return ToolResult.failure(err or "git diff failed")
    return ToolResult.success(out or "No differences.", diff=out)


async def git_log(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    problem = await _ensure_repo(context)
    if problem:
        return ToolResult.failure(problem)
    limit = args.get("limit", 10)
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 10
    code, out, err = await _git(
        context, "log", f"-{limit}", "--oneline", "--decorate", "--no-color"
    )
    if code != 0:
        return ToolResult.failure(err or "git log failed")
    return ToolResult.success(out or "No commits.", log=out)


async def git_commit(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    problem = await _ensure_repo(context)
    if problem:
        return ToolResult.failure(problem)
    message = args.get("message") or ""
    if not isinstance(message, str) or not message.strip():
        return ToolResult.failure("git_commit requires a 'message' argument")
    if args.get("add_all", True):
        code, out, err = await _git(context, "add", "-A")
        if code != 0:
            return ToolResult.failure(err or "git add failed")
    code, out, err = await _git(context, "commit", "-m", message)
    if code != 0:
        return ToolResult.failure(err or out or "git commit failed")
    code2, head, _ = await _git(context, "rev-parse", "--short", "HEAD")
    summary = f"Committed {head.strip() if code2 == 0 else ''}: {message.strip()}"
    if out:
        summary += f"\n{out}"
    return ToolResult.success(summary, commit=head.strip() if code2 == 0 else None)


def git_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="git_status",
            description="Show the git working tree status (short format).",
            parameters=object_schema({}),
            handler=git_status,
            permission="bash",
        ),
        ToolSpec(
            name="git_diff",
            description="Show git diffs. Use staged=true for staged changes and stat=true for a summary.",
            parameters=object_schema(
                {
                    "staged": boolean_prop("Show staged changes"),
                    "stat": boolean_prop("Show a diffstat instead of the full diff"),
                    "path": string_prop("Limit to a workspace-relative path"),
                }
            ),
            handler=git_diff,
            permission="bash",
            max_output_chars=40_000,
        ),
        ToolSpec(
            name="git_log",
            description="Show recent commits in one-line format.",
            parameters=object_schema(
                {"limit": integer_prop("Number of commits (default 10)", minimum=1, maximum=100)}
            ),
            handler=git_log,
            permission="bash",
        ),
        ToolSpec(
            name="git_commit",
            description="Stage all changes (unless add_all=false) and create a commit with the given message.",
            parameters=object_schema(
                {
                    "message": string_prop("Commit message"),
                    "add_all": boolean_prop("Run 'git add -A' first (default true)"),
                },
                ["message"],
            ),
            handler=git_commit,
            permission="edit",
        ),
    ]

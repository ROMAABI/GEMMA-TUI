"""Filesystem tools: read, write, edit, glob, grep, and list."""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path
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

IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".gemini",
    ".idea",
    ".vscode",
}

MAX_READ_LINES = 2000
MAX_READ_BYTES = 120_000
MAX_GREP_MATCHES = 100
MAX_GLOB_RESULTS = 300

_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)


def _is_probably_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(8192)
    except Exception:
        return False
    return b"\x00" in chunk


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _line_numbered(lines: list[str], start: int) -> list[str]:
    return [f"{start + idx}: {line}" for idx, line in enumerate(lines)]


def read_file(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    raw_path = args.get("path") or args.get("file_path") or args.get("file") or ""
    if not raw_path:
        return ToolResult.failure("read requires a 'path' argument")
    try:
        target = context.resolve(raw_path, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        return ToolResult.failure(str(exc))
    if target.is_dir():
        return ToolResult.failure(f"'{raw_path}' is a directory; use list_dir or glob")

    if _is_probably_binary(target):
        return ToolResult.failure(
            f"'{raw_path}' appears to be a binary file ({target.stat().st_size} bytes)"
        )

    try:
        content = _read_text(target)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"Could not read '{raw_path}': {exc}")

    lines = content.splitlines()
    total = len(lines)
    if total == 0:
        return ToolResult.success(f"[File: {raw_path} | empty]", path=raw_path, lines=0)

    start = args.get("start_line", args.get("offset", 1))
    end = args.get("end_line", args.get("limit_end"))
    try:
        start = max(1, int(start or 1))
    except (TypeError, ValueError):
        start = 1
    if end is None:
        end = min(total, start + MAX_READ_LINES - 1)
    else:
        try:
            end = max(start, int(end))
        except (TypeError, ValueError):
            end = min(total, start + MAX_READ_LINES - 1)

    if start > total:
        return ToolResult.failure(
            f"start_line {start} exceeds file length ({total} lines)",
            output=f"Error: start_line {start} exceeds file length ({total} lines)",
        )

    selected = lines[start - 1 : end]
    rendered = "\n".join(_line_numbered(selected, start))
    header = f"[File: {raw_path} | lines {start}-{min(end, total)} of {total}]"
    output = f"{header}\n{rendered}"

    if len(output.encode("utf-8")) > MAX_READ_BYTES:
        # Shrink the window until it fits, keeping the requested start.
        keep = max(1, len(selected) // 2)
        selected = selected[:keep]
        rendered = "\n".join(_line_numbered(selected, start))
        output = (
            f"{header}\n{rendered}\n\n"
            f"... [truncated; file continues to line {total}. "
            f"Re-run with start_line={start + keep}]"
        )

    if end < total:
        output += f"\n... [{total - end} more lines; use start_line={end + 1}]"

    return ToolResult.success(output, path=raw_path, lines=total, start=start, end=min(end, total))


def write_file(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    raw_path = args.get("path") or args.get("file_path") or ""
    content = args.get("content")
    if not raw_path:
        return ToolResult.failure("write requires a 'path' argument")
    if content is None:
        return ToolResult.failure("write requires a 'content' argument")
    if not isinstance(content, str):
        content = str(content)

    try:
        target = context.resolve(raw_path)
    except ValueError as exc:
        return ToolResult.failure(str(exc))
    if target.is_dir():
        return ToolResult.failure(f"'{raw_path}' is a directory")

    existed = target.exists()
    old = ""
    if existed:
        try:
            old = _read_text(target)
        except Exception:
            old = ""
    if existed and old == content:
        return ToolResult.success(
            f"No changes: '{raw_path}' already has this content.", path=raw_path, changed=False
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"Could not write '{raw_path}': {exc}")

    line_count = len(content.splitlines())
    action = "Updated" if existed else "Created"
    return ToolResult.success(
        f"{action} '{raw_path}' ({len(content)} bytes, {line_count} lines).",
        path=raw_path,
        changed=True,
        created=not existed,
    )


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").translate(_QUOTE_MAP)


def _find_edit_region(content: str, old: str) -> tuple[int, int] | None:
    """Find a unique edit region using progressively looser matching."""
    if not old:
        return None

    # 1. Exact match.
    count = content.count(old)
    if count == 1:
        start = content.index(old)
        return start, start + len(old)
    if count > 1:
        return None

    # 2. Normalised Unicode / line endings.
    normalised_content = _normalise(content)
    normalised_old = _normalise(old)
    count = normalised_content.count(normalised_old)
    if count == 1:
        start = normalised_content.index(normalised_old)
        return start, start + len(normalised_old)

    # 3. Trailing whitespace on each line.
    content_lines = content.splitlines(keepends=True)
    old_lines = old.splitlines(keepends=True)
    if old_lines:
        stripped_old = [line.rstrip() for line in old_lines]
        matches: list[int] = []
        for idx in range(0, len(content_lines) - len(old_lines) + 1):
            window = [line.rstrip() for line in content_lines[idx : idx + len(old_lines)]]
            if window == stripped_old:
                matches.append(idx)
        if len(matches) == 1:
            idx = matches[0]
            start = sum(len(line) for line in content_lines[:idx])
            end = start + sum(len(line) for line in content_lines[idx : idx + len(old_lines)])
            return start, end

    # 4. Fuzzy line match (whitespace-insensitive).
    stripped_content = [line.strip() for line in content.splitlines()]
    stripped_old = [line.strip() for line in old.splitlines()]
    if stripped_old:
        matches = [
            idx
            for idx in range(0, len(stripped_content) - len(stripped_old) + 1)
            if stripped_content[idx : idx + len(stripped_old)] == stripped_old
        ]
        if len(matches) == 1:
            lines = content.splitlines(keepends=True)
            idx = matches[0]
            start = sum(len(line) for line in lines[:idx])
            end = start + sum(len(line) for line in lines[idx : idx + len(stripped_old)])
            return start, end

    return None


def edit_file(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    raw_path = args.get("path") or args.get("file_path") or ""
    old = args.get("old_string", args.get("old_str", args.get("old", "")))
    new = args.get("new_string", args.get("new_str", args.get("new", "")))
    replace_all = bool(args.get("replace_all", False))

    if not raw_path:
        return ToolResult.failure("edit requires a 'path' argument")
    if old is None or old == "":
        return ToolResult.failure("edit requires a non-empty 'old_string' argument")
    if new is None:
        new = ""

    try:
        target = context.resolve(raw_path, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        return ToolResult.failure(str(exc))
    if target.is_dir():
        return ToolResult.failure(f"'{raw_path}' is a directory")

    try:
        content = _read_text(target)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"Could not read '{raw_path}': {exc}")

    if replace_all:
        count = content.count(old)
        if count == 0:
            return ToolResult.failure(f"old_string not found in '{raw_path}'")
        updated = content.replace(old, new)
        target.write_text(updated, encoding="utf-8")
        return ToolResult.success(
            f"Replaced {count} occurrence(s) in '{raw_path}'.", path=raw_path, replacements=count
        )

    region = _find_edit_region(content, old)
    if region is None:
        occurrences = content.count(old)
        if occurrences > 1:
            return ToolResult.failure(
                f"old_string occurs {occurrences} times in '{raw_path}'. "
                "Provide more surrounding context or set replace_all=true."
            )
        # Give the model a useful hint.
        closest = difflib.get_close_matches(old, content.splitlines(), n=1, cutoff=0.5)
        hint = f" Closest line: {closest[0]!r}" if closest else ""
        return ToolResult.failure(
            f"old_string not found in '{raw_path}'. Check exact whitespace/indentation.{hint}"
        )

    start, end = region
    # A line-based fallback may have consumed the line terminator. Preserve it
    # when the replacement is a single logical line and the caller did not ask
    # to remove it.
    if (
        end > start
        and content[end - 1] == "\n"
        and not old.endswith("\n")
        and not new.endswith("\n")
    ):
        new = new + "\n"
    updated = content[:start] + new + content[end:]
    try:
        target.write_text(updated, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"Could not write '{raw_path}': {exc}")

    return ToolResult.success(
        f"Updated '{raw_path}' (replaced {end - start} chars with {len(new)} chars).",
        path=raw_path,
        replacements=1,
    )


def list_dir(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    raw_path = args.get("path") or "."
    # Non-recursive, small listing: never walk the home directory tree.
    depth = 1
    max_entries = 50

    try:
        target = context.resolve(raw_path, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        return ToolResult.failure(str(exc))
    if not target.is_dir():
        return ToolResult.failure(f"'{raw_path}' is not a directory")

    items: list[str] = []
    base_parts = len(target.parts)
    for root, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
        current_depth = len(Path(root).parts) - base_parts
        if current_depth >= depth:
            dirs.clear()
        rel_root = Path(root).relative_to(context.root).as_posix()
        prefix = "" if rel_root == "." else rel_root + "/"
        for directory in dirs:
            items.append(f"{prefix}{directory}/")
        for filename in sorted(files):
            if not filename.startswith("."):
                items.append(f"{prefix}{filename}")
        if len(items) >= max_entries:
            items.append("... [truncated]")
            break

    if not items:
        return ToolResult.success(f"Directory '{raw_path}' is empty.", path=raw_path, count=0)
    return ToolResult.success("\n".join(items[:max_entries]), path=raw_path, count=len(items))


def glob_files(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    pattern = args.get("pattern") or args.get("glob") or "*"
    raw_path = args.get("path") or "."
    try:
        target = context.resolve(raw_path, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        return ToolResult.failure(str(exc))
    if not target.is_dir():
        return ToolResult.failure(f"'{raw_path}' is not a directory")

    try:
        matches = sorted(target.glob(pattern), key=lambda p: p.as_posix())
        if not matches and "/" not in pattern and "**" not in pattern:
            matches = sorted(target.rglob(pattern), key=lambda p: p.as_posix())
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"Invalid glob pattern '{pattern}': {exc}")

    rendered: list[str] = []
    for match in matches:
        try:
            resolved = match.resolve()
        except OSError:
            continue
        if resolved != context.root and not resolved.is_relative_to(context.root):
            continue
        if any(part in IGNORED_DIRS or part.startswith(".") for part in match.relative_to(target).parts):
            continue
        if match.is_file():
            rendered.append(context.relative(match))
        if len(rendered) >= MAX_GLOB_RESULTS:
            rendered.append("... [truncated]")
            break

    if not rendered:
        return ToolResult.success(f"No files matched '{pattern}' in '{raw_path}'.", count=0)
    return ToolResult.success("\n".join(rendered), count=len(rendered))


def _python_grep(
    target: Path,
    root: Path,
    pattern: re.Pattern[str],
    include: str | None,
    max_matches: int,
) -> list[str]:
    matches: list[str] = []
    for dirpath, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        for filename in files:
            if filename.startswith("."):
                continue
            if include and not fnmatch.fnmatch(filename, include):
                continue
            file_path = Path(dirpath) / filename
            try:
                resolved = file_path.resolve()
            except OSError:
                continue
            if resolved != root and not resolved.is_relative_to(root):
                continue
            if _is_probably_binary(file_path):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = file_path.relative_to(root).as_posix()
            for idx, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(f"{rel}:{idx}: {line.rstrip()}")
                    if len(matches) >= max_matches:
                        return matches
    return matches


def grep_search(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = args.get("pattern", args.get("query", ""))
    if not query:
        return ToolResult.failure("grep requires a 'pattern' argument")
    raw_path = args.get("path") or "."
    include = args.get("include")
    case_sensitive = bool(args.get("case_sensitive", False))
    fixed = bool(args.get("fixed_strings", args.get("literal", False)))
    max_matches = args.get("max_matches", MAX_GREP_MATCHES)
    try:
        max_matches = max(1, min(int(max_matches), 500))
    except (TypeError, ValueError):
        max_matches = MAX_GREP_MATCHES

    try:
        target = context.resolve(raw_path, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        return ToolResult.failure(str(exc))

    rg = ["rg", "-n", "--no-heading", "--color", "never", "--max-count", str(max_matches)]
    if not case_sensitive:
        rg.append("-i")
    if fixed:
        rg.append("-F")
    if include:
        rg.extend(["--glob", include])
    for ignored in IGNORED_DIRS:
        rg.extend(["--glob", f"!**/{ignored}/**"])
    rg.extend(["-e", query, str(target)])

    try:
        proc = subprocess.run(rg, capture_output=True, text=True, timeout=15)
        if proc.returncode in (0, 1):
            lines = [line for line in proc.stdout.splitlines() if line.strip()]
            if not lines:
                return ToolResult.success(f"No matches for '{query}'.", count=0)
            rendered = [line.replace(str(context.root) + "/", "") for line in lines[:max_matches]]
            return ToolResult.success("\n".join(rendered), count=len(rendered))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(query) if fixed else query, flags)
    except re.error as exc:
        return ToolResult.failure(f"Invalid regex '{query}': {exc}")

    try:
        matches = _python_grep(target, context.root, pattern, include, max_matches)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"grep failed: {exc}")

    if not matches:
        return ToolResult.success(f"No matches for '{query}'.", count=0)
    return ToolResult.success("\n".join(matches), count=len(matches))


def filesystem_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="read",
            description=(
                "Read a text file from the workspace with line numbers. Use start_line/end_line "
                "to page through large files. Always read a file before editing it."
            ),
            parameters=object_schema(
                {
                    "path": string_prop("Workspace-relative path to the file"),
                    "start_line": integer_prop("First line to read (1-based)", minimum=1),
                    "end_line": integer_prop("Last line to read (inclusive)", minimum=1),
                },
                ["path"],
            ),
            handler=read_file,
            permission="read",
            aliases=("read_file",),
        ),
        ToolSpec(
            name="write",
            description=(
                "Create or overwrite a file in the workspace. Parent directories are created. "
                "Prefer edit for small changes to existing files."
            ),
            parameters=object_schema(
                {
                    "path": string_prop("Workspace-relative path to write"),
                    "content": string_prop("Full file content"),
                },
                ["path", "content"],
            ),
            handler=write_file,
            permission="edit",
            aliases=("write_file",),
        ),
        ToolSpec(
            name="edit",
            description=(
                "Replace an exact string in a file. old_string must be unique unless replace_all "
                "is true. Include enough surrounding context to make the match unique."
            ),
            parameters=object_schema(
                {
                    "path": string_prop("Workspace-relative path to edit"),
                    "old_string": string_prop("Exact text to replace"),
                    "new_string": string_prop("Replacement text"),
                    "replace_all": boolean_prop("Replace every occurrence (default false)"),
                },
                ["path", "old_string", "new_string"],
            ),
            handler=edit_file,
            permission="edit",
            aliases=("edit_file",),
        ),
        ToolSpec(
            name="list_dir",
            description="List up to 50 entries of a workspace directory (non-recursive).",
            parameters=object_schema(
                {
                    "path": string_prop("Workspace-relative directory (default '.')"),
                }
            ),
            handler=list_dir,
            permission="read",
            aliases=("list", "ls"),
        ),
        ToolSpec(
            name="glob",
            description=(
                "Find files by glob pattern (for example '**/*.py' or 'src/**/*.ts'). "
                "Returns workspace-relative paths."
            ),
            parameters=object_schema(
                {
                    "pattern": string_prop("Glob pattern, supports **"),
                    "path": string_prop("Directory to search from (default '.')"),
                },
                ["pattern"],
            ),
            handler=glob_files,
            permission="read",
            aliases=("glob_search", "find_files"),
        ),
        ToolSpec(
            name="grep",
            description=(
                "Search file contents with a regular expression. Returns path:line:text. "
                "Use include to restrict files, e.g. '*.py'."
            ),
            parameters=object_schema(
                {
                    "pattern": string_prop("Regular expression (or literal text)"),
                    "path": string_prop("Directory or file to search (default '.')"),
                    "include": string_prop("Glob filter for filenames, e.g. '*.py'"),
                    "case_sensitive": boolean_prop("Match case (default false)"),
                    "fixed_strings": boolean_prop("Treat pattern as literal text"),
                    "max_matches": integer_prop("Maximum matches (default 100)", minimum=1, maximum=500),
                },
                ["pattern"],
            ),
            handler=grep_search,
            permission="read",
            aliases=("grep_search",),
        ),
    ]

"""Multi-file patch tool compatible with the common Codex/OpenCode patch format.

Supported operations::

    *** Begin Patch
    *** Add File: path/to/new.py
    +line
    *** Update File: path/to/existing.py
    @@ optional context
     unchanged
    -removed
    +added
    *** Delete File: path/to/old.py
    *** End Patch

The whole patch is prepared before anything is written, so a malformed hunk
does not leave the workspace half-modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.tooling.types import (
    ToolContext,
    ToolResult,
    ToolSpec,
    object_schema,
    string_prop,
)


@dataclass
class PatchHunk:
    kind: str  # add | update | delete
    path: str
    move_to: str | None = None
    lines: list[str] = field(default_factory=list)  # raw patch lines for update


def parse_patch(text: str) -> tuple[list[PatchHunk], str | None]:
    lines = text.splitlines()
    hunks: list[PatchHunk] = []
    current: PatchHunk | None = None
    started = False
    ended = False

    for raw in lines:
        line = raw.rstrip("\n")
        if line.strip() == "*** Begin Patch":
            started = True
            continue
        if line.strip() == "*** End Patch":
            ended = True
            if current is not None:
                hunks.append(current)
                current = None
            break
        if line.startswith("*** Add File:"):
            if current is not None:
                hunks.append(current)
            current = PatchHunk(kind="add", path=line.split(":", 1)[1].strip())
            continue
        if line.startswith("*** Delete File:"):
            if current is not None:
                hunks.append(current)
            current = PatchHunk(kind="delete", path=line.split(":", 1)[1].strip())
            continue
        if line.startswith("*** Update File:"):
            if current is not None:
                hunks.append(current)
            current = PatchHunk(kind="update", path=line.split(":", 1)[1].strip())
            continue
        if line.startswith("*** Move to:") and current is not None:
            current.move_to = line.split(":", 1)[1].strip()
            continue
        if line.startswith("*** "):
            return [], f"Unknown patch directive: {line!r}"
        if current is None:
            if line.strip():
                return [], f"Content outside a file hunk: {line!r}"
            continue
        current.lines.append(line)

    if current is not None:
        hunks.append(current)
    if not started:
        return [], "Patch must start with '*** Begin Patch'"
    if not ended:
        return [], "Patch must end with '*** End Patch'"
    if not hunks:
        return [], "Patch contains no file operations"
    return hunks, None


def _split_chunks(lines: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("@@"):
            if current:
                chunks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        chunks.append(current)
    return chunks


def _apply_chunk(original: list[str], chunk: list[str], cursor: int) -> tuple[list[str], int, str | None]:
    old_lines: list[str] = []
    new_lines: list[str] = []
    for line in chunk:
        if not line:
            old_lines.append("")
            new_lines.append("")
        elif line.startswith("+"):
            new_lines.append(line[1:])
        elif line.startswith("-"):
            old_lines.append(line[1:])
        elif line.startswith(" "):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line.startswith("\\"):
            # "\ No newline at end of file" marker
            continue
        else:
            return original, cursor, f"Invalid patch line: {line!r}"

    if not old_lines:
        # Pure insertion: append at the cursor (or end).
        position = min(cursor, len(original))
        updated = original[:position] + new_lines + original[position:]
        return updated, position + len(new_lines), None

    match_index = None
    # Exact subsequence search from the cursor.
    for idx in range(cursor, len(original) - len(old_lines) + 1):
        if original[idx : idx + len(old_lines)] == old_lines:
            match_index = idx
            break
    if match_index is None:
        # Fallback: whitespace-insensitive search.
        stripped_old = [line.strip() for line in old_lines]
        for idx in range(cursor, len(original) - len(old_lines) + 1):
            if [line.strip() for line in original[idx : idx + len(old_lines)]] == stripped_old:
                match_index = idx
                break
    if match_index is None:
        preview = "\n".join(old_lines[:6])
        return original, cursor, f"Context not found in file. Expected:\n{preview}"

    updated = (
        original[:match_index]
        + new_lines
        + original[match_index + len(old_lines) :]
    )
    return updated, match_index + len(new_lines), None


def apply_patch(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    patch_text = args.get("patch_text", args.get("patch", args.get("input", "")))
    if not isinstance(patch_text, str) or not patch_text.strip():
        return ToolResult.failure("patch requires a non-empty 'patch_text' argument")

    hunks, error = parse_patch(patch_text)
    if error:
        return ToolResult.failure(f"Patch parse failed: {error}")

    # Prepare every operation in memory first.
    prepared: list[tuple[PatchHunk, Path, str | None, str | None]] = []
    # (hunk, target, new_content_or_None, move_target_or_None)
    cache: dict[Path, str] = {}
    for hunk in hunks:
        try:
            target = context.resolve(hunk.path)
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        move_target: Path | None = None
        if hunk.move_to:
            try:
                move_target = context.resolve(hunk.move_to)
            except ValueError as exc:
                return ToolResult.failure(str(exc))

        if hunk.kind == "add":
            if target.exists():
                return ToolResult.failure(f"Add File target already exists: {hunk.path}")
            content_lines = []
            for line in hunk.lines:
                if line.startswith("+"):
                    content_lines.append(line[1:])
                elif line.startswith("\\"):
                    continue
                elif line == "":
                    content_lines.append("")
                else:
                    return ToolResult.failure(
                        f"Add File lines must start with '+': {line!r}"
                    )
            content = "\n".join(content_lines)
            if content and not content.endswith("\n"):
                content += "\n"
            prepared.append((hunk, target, content, None))
            continue

        if hunk.kind == "delete":
            if not target.exists():
                return ToolResult.failure(f"Delete File target does not exist: {hunk.path}")
            prepared.append((hunk, target, None, None))
            continue

        # update
        if not target.exists():
            return ToolResult.failure(f"Update File target does not exist: {hunk.path}")
        if target not in cache:
            try:
                cache[target] = target.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                return ToolResult.failure(f"Could not read {hunk.path}: {exc}")
        original = cache[target].splitlines()
        had_trailing_newline = cache[target].endswith("\n")
        chunks = _split_chunks(hunk.lines)
        if not chunks:
            return ToolResult.failure(f"Update File has no hunks: {hunk.path}")
        cursor = 0
        updated = original
        for chunk in chunks:
            updated, cursor, problem = _apply_chunk(updated, chunk, cursor)
            if problem:
                return ToolResult.failure(f"{hunk.path}: {problem}")
        new_content = "\n".join(updated)
        if had_trailing_newline or updated:
            new_content += "\n"
        cache[target] = new_content
        prepared.append((hunk, target, new_content, str(move_target) if move_target else None))

    # Commit.
    applied: list[str] = []
    try:
        for hunk, target, content, move_to in prepared:
            if hunk.kind == "add":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content or "", encoding="utf-8")
                applied.append(f"A {hunk.path}")
            elif hunk.kind == "delete":
                target.unlink()
                applied.append(f"D {hunk.path}")
            else:
                if move_to and Path(move_to) != target:
                    destination = Path(move_to)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(content or "", encoding="utf-8")
                    target.unlink()
                    applied.append(f"M {hunk.path} -> {context.relative(destination)}")
                else:
                    target.write_text(content or "", encoding="utf-8")
                    applied.append(f"M {hunk.path}")
    except Exception as exc:  # noqa: BLE001
        completed = ", ".join(applied) or "none"
        return ToolResult.failure(
            f"Patch failed while writing: {exc}. Completed before failure: {completed}"
        )

    return ToolResult.success(
        "Success. Updated the following files:\n" + "\n".join(applied),
        applied=applied,
    )


def patch_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="patch",
            description=(
                "Apply a multi-file patch. Format: '*** Begin Patch', then '*** Add File: path', "
                "'*** Update File: path' with '@@' hunks using ' ', '-', and '+' lines, or "
                "'*** Delete File: path', then '*** End Patch'. All changes are validated before "
                "anything is written."
            ),
            parameters=object_schema(
                {"patch_text": string_prop("Full patch text")},
                ["patch_text"],
            ),
            handler=apply_patch,
            permission="edit",
            aliases=("apply_patch",),
            max_output_chars=20_000,
        )
    ]

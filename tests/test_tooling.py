from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.tooling import ToolContext, build_default_registry
from core.tooling.filesystem import edit_file, glob_files, grep_search, read_file, write_file
from core.tooling.patch import apply_patch, parse_patch
from core.tooling.shell import run_bash


@pytest.fixture()
def workspace(tmp_path: Path) -> ToolContext:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    return ToolContext(root=tmp_path)


def test_read_write_and_no_change(workspace: ToolContext) -> None:
    result = read_file(workspace, {"path": "src/app.py", "start_line": 1, "end_line": 2})
    assert result.ok
    assert "1: def add" in result.output
    assert "2:     return a + b" in result.output

    result = write_file(workspace, {"path": "new.txt", "content": "hello\n"})
    assert result.ok and (workspace.root / "new.txt").read_text() == "hello\n"

    result = write_file(workspace, {"path": "new.txt", "content": "hello\n"})
    assert result.ok and "No changes" in result.output


def test_path_escape_denied(workspace: ToolContext) -> None:
    result = read_file(workspace, {"path": "../outside.txt"})
    assert not result.ok
    assert "escapes workspace" in result.output


def test_edit_unique_and_ambiguous(workspace: ToolContext) -> None:
    result = edit_file(
        workspace,
        {"path": "src/app.py", "old_string": "return a + b", "new_string": "return a + b + 0"},
    )
    assert result.ok
    assert "return a + b + 0" in (workspace.root / "src" / "app.py").read_text()

    result = edit_file(
        workspace,
        {"path": "src/app.py", "old_string": "def ", "new_string": "def x"},
    )
    assert not result.ok
    assert "occurs" in result.output


def test_edit_trailing_whitespace_fallback(workspace: ToolContext) -> None:
    path = workspace.root / "trail.txt"
    path.write_text("alpha   \nbeta\t\n", encoding="utf-8")
    result = edit_file(
        workspace,
        {"path": "trail.txt", "old_string": "alpha\nbeta", "new_string": "gamma"},
    )
    assert result.ok, result.output
    assert path.read_text() == "gamma\n"


def test_glob_and_grep(workspace: ToolContext) -> None:
    result = glob_files(workspace, {"pattern": "**/*.py"})
    assert result.ok and "src/app.py" in result.output

    result = grep_search(workspace, {"pattern": "return a", "include": "*.py"})
    assert result.ok and "src/app.py:2:" in result.output

    result = grep_search(workspace, {"pattern": "nomatch"})
    assert result.ok and "No matches" in result.output


def test_bash_success_and_failure(workspace: ToolContext) -> None:
    result = asyncio.run(run_bash(workspace, {"command": "echo hello"}))
    assert result.ok and "hello" in result.output

    result = asyncio.run(run_bash(workspace, {"command": "exit 3"}))
    assert not result.ok and "Exit code: 3" in result.output


def test_bash_permission_denied_includes_guidance(workspace: ToolContext) -> None:
    command = (
        "python -c 'import sys; sys.stderr.write(\"Permission denied\\n\"); sys.exit(1)'"
    )
    result = asyncio.run(run_bash(workspace, {"command": command}))
    assert not result.ok
    assert "Permission denied" in result.output
    assert "operating system denied" in result.output


def test_bash_timeout(workspace: ToolContext) -> None:
    result = asyncio.run(
        run_bash(
            workspace,
            {"command": "python -c 'import time; time.sleep(2)'", "timeout": 1},
        )
    )
    assert not result.ok
    assert "timed out" in result.output.lower()


def test_patch_add_update_delete(workspace: ToolContext) -> None:
    patch_text = """*** Begin Patch
*** Add File: src/new.py
+def mul(a, b):
+    return a * b
*** Update File: src/app.py
@@
 def add(a, b):
-    return a + b
+    return a + b  # sum
*** Delete File: README.md
*** End Patch
"""
    result = apply_patch(workspace, {"patch_text": patch_text})
    assert result.ok, result.output
    assert (workspace.root / "src" / "new.py").read_text() == "def mul(a, b):\n    return a * b\n"
    assert "# sum" in (workspace.root / "src" / "app.py").read_text()
    assert not (workspace.root / "README.md").exists()


def test_patch_is_atomic_on_bad_hunk(workspace: ToolContext) -> None:
    before = (workspace.root / "src" / "app.py").read_text()
    patch_text = """*** Begin Patch
*** Update File: src/app.py
@@
 this context does not exist
*** End Patch
"""
    result = apply_patch(workspace, {"patch_text": patch_text})
    assert not result.ok
    assert (workspace.root / "src" / "app.py").read_text() == before


def test_registry_dispatch_and_aliases(workspace: ToolContext) -> None:
    registry = build_default_registry()
    assert registry.get("read_file") is registry.get("read")
    result = asyncio.run(registry.execute("read_file", {"path": "src/app.py"}, workspace))
    assert result.ok
    result = asyncio.run(registry.execute("nope", {}, workspace))
    assert not result.ok and "Unknown tool" in result.output


def test_registry_coerces_string_integers(workspace: ToolContext) -> None:
    registry = build_default_registry()
    result = asyncio.run(
        registry.execute("read", {"path": "src/app.py", "start_line": "2", "end_line": "2"}, workspace)
    )
    assert result.ok
    assert "2:     return a + b" in result.output


def test_registry_coerces_string_values(workspace: ToolContext) -> None:
    registry = build_default_registry()
    result = asyncio.run(
        registry.execute("write", {"path": "value.txt", "content": 42}, workspace)
    )
    assert result.ok
    assert (workspace.root / "value.txt").read_text() == "42"

    result = asyncio.run(
        registry.execute("write", {"path": "value.json", "content": {"a": 1}}, workspace)
    )
    assert result.ok
    assert (workspace.root / "value.json").read_text() == '{"a": 1}'


def test_patch_parser_rejects_missing_end() -> None:
    hunks, error = parse_patch("*** Begin Patch\n*** Add File: x\n+hi\n")
    assert hunks == []
    assert error and "End Patch" in error


def test_glob_does_not_escape_workspace(workspace: ToolContext, tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        result = glob_files(workspace, {"pattern": "../**/*.txt"})
        assert "secret.txt" not in result.output
    finally:
        (outside / "secret.txt").unlink(missing_ok=True)
        outside.rmdir()


def test_grep_does_not_follow_symlinks_outside_workspace(
    workspace: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.tooling.filesystem as fs

    def no_rg(*args, **kwargs):
        raise FileNotFoundError("rg not installed")

    monkeypatch.setattr(fs.subprocess, "run", no_rg)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-grep"
    outside.mkdir(exist_ok=True)
    secret = outside / "secret.txt"
    secret.write_text("topsecretvalue", encoding="utf-8")
    link = tmp_path / "leak.txt"
    try:
        link.symlink_to(secret)
        result = grep_search(workspace, {"pattern": "topsecretvalue"})
        assert result.metadata.get("count") == 0
        assert "leak.txt" not in result.output
    finally:
        link.unlink(missing_ok=True)
        secret.unlink(missing_ok=True)
        outside.rmdir()

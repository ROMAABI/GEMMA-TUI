from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.tooling.git import git_tools
from core.tooling.registry import ToolRegistry
from core.tooling.types import ToolContext


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> ToolContext:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    return ToolContext(root=tmp_path)


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(git_tools())
    return registry


@pytest.mark.asyncio
async def test_git_status_diff_log_commit(repo: ToolContext, registry: ToolRegistry) -> None:
    status = await registry.execute("git_status", {}, repo)
    assert status.ok
    assert "?? notes.txt" in status.output

    await registry.execute("git_commit", {"message": "initial commit"}, repo)

    status = await registry.execute("git_status", {}, repo)
    assert status.ok
    assert "Working tree clean." in status.output

    log = await registry.execute("git_log", {"limit": 5}, repo)
    assert log.ok
    assert "initial commit" in log.output

    (repo.root / "notes.txt").write_text("hello\nworld\n", encoding="utf-8")
    diff = await registry.execute("git_diff", {}, repo)
    assert diff.ok
    assert "+world" in diff.output

    stat = await registry.execute("git_diff", {"stat": True}, repo)
    assert stat.ok
    assert "notes.txt" in stat.output


@pytest.mark.asyncio
async def test_git_commit_requires_message(repo: ToolContext, registry: ToolRegistry) -> None:
    result = await registry.execute("git_commit", {}, repo)
    assert not result.ok
    assert "Missing required argument" in result.output


@pytest.mark.asyncio
async def test_git_tools_outside_repo(tmp_path: Path, registry: ToolRegistry) -> None:
    context = ToolContext(root=tmp_path)
    result = await registry.execute("git_status", {}, context)
    assert not result.ok
    assert "Not a git repository" in result.output


@pytest.mark.asyncio
async def test_git_diff_rejects_escape(repo: ToolContext, registry: ToolRegistry) -> None:
    result = await registry.execute("git_diff", {"path": "../../etc/passwd"}, repo)
    assert not result.ok
    assert "escapes workspace" in result.output

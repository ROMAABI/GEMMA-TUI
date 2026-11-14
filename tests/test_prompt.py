from __future__ import annotations

from pathlib import Path

from core.tools import build_agent_system_prompt, load_workspace_instructions


def test_load_workspace_instructions(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use tabs.\nRun pytest.", encoding="utf-8")
    (tmp_path / ".gemma").mkdir()
    (tmp_path / ".gemma" / "AGENTS.md").write_text("Prefer small diffs.", encoding="utf-8")

    text = load_workspace_instructions(tmp_path)
    assert "### AGENTS.md" in text
    assert "Use tabs." in text
    assert "### .gemma/AGENTS.md" in text
    assert "Prefer small diffs." in text


def test_load_workspace_instructions_missing(tmp_path: Path) -> None:
    assert load_workspace_instructions(tmp_path) == ""


def test_load_workspace_instructions_truncates(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 500, encoding="utf-8")
    text = load_workspace_instructions(tmp_path, max_chars=100)
    assert "[instructions truncated]" in text
    assert len(text) < 500


def test_build_prompt_includes_instructions(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Always run the linter.", encoding="utf-8")
    prompt = build_agent_system_prompt(str(tmp_path), "- read(path)", "")
    assert "## Project Instructions" in prompt
    assert "Always run the linter." in prompt
    assert "{INSTRUCTIONS}" not in prompt
    assert "{TOOL_CATALOG}" not in prompt
    assert "- read(path)" in prompt


def test_instructions_are_not_treated_as_templates(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Literal {TOOL_CATALOG} token.", encoding="utf-8")
    prompt = build_agent_system_prompt(str(tmp_path), "- read(path)", "")
    assert "Literal {TOOL_CATALOG} token." in prompt


def test_explicit_instructions_override_file(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("from file", encoding="utf-8")
    prompt = build_agent_system_prompt(
        str(tmp_path), "- read(path)", "", instructions="from caller"
    )
    assert "from caller" in prompt
    assert "from file" not in prompt

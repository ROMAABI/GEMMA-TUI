from __future__ import annotations

from pathlib import Path

from core.skills import SkillsManager


def test_skill_discovery_and_tool(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".gemma" / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pdf-processing\ndescription: Work with PDF files\n---\n\nUse pdftotext.\n",
        encoding="utf-8",
    )
    manager = SkillsManager(tmp_path)
    assert "pdf-processing" in manager.skills
    skill = manager.get("pdf-processing")
    assert skill is not None and "pdftotext" in skill.content
    assert "pdf-processing" in manager.summary()

    specs = {spec.name: spec for spec in manager.tool_specs()}
    from core.tooling.types import ToolContext

    result = specs["skill"].handler(ToolContext(root=tmp_path), {"name": "pdf-processing"})
    assert result.ok and "pdftotext" in result.output

    result = specs["skill"].handler(ToolContext(root=tmp_path), {"name": "missing"})
    assert not result.ok


def test_plain_markdown_skill(tmp_path: Path) -> None:
    skills = tmp_path / ".gemma" / "skills"
    skills.mkdir(parents=True)
    (skills / "review.md").write_text("# Code review\nCheck tests.\n", encoding="utf-8")
    manager = SkillsManager(tmp_path)
    assert "review" in manager.skills
    assert manager.skills["review"].description.startswith("Code review")

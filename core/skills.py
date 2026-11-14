"""Skill discovery and progressive disclosure.

A skill is a Markdown file (usually ``SKILL.md``) with optional YAML-style
frontmatter::

    ---
    name: pdf-processing
    description: Extract text and tables from PDF files
    ---

    Full instructions...

Only the name and description are shown to the model up front. The full body is
returned when the model calls the ``skill`` tool.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.tooling.types import ToolContext, ToolResult, ToolSpec, object_schema, string_prop


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    content: str
    source: str = "workspace"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    metadata: dict[str, str] = {}
    end = None
    for index in range(1, len(lines)):
        line = lines[index].strip()
        if line == "---":
            end = index
            break
        if ":" in line:
            key, _, value = line.partition(":")
            metadata[key.strip().lower()] = value.strip().strip('"').strip("'")
    if end is None:
        return {}, text
    return metadata, "\n".join(lines[end + 1 :]).strip()


class SkillsManager:
    def __init__(
        self,
        workspace: str | Path = ".",
        extra_dirs: list[str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.extra_dirs = [Path(item).expanduser() for item in (extra_dirs or [])]
        self.skills: dict[str, Skill] = {}
        self.reload()

    def _directories(self) -> list[Path]:
        home = Path.home()
        return [
            self.workspace / ".gemma" / "skills",
            self.workspace / ".opencode" / "skills",
            *self.extra_dirs,
            home / ".config" / "gemma" / "skills",
            home / ".config" / "opencode" / "skills",
            Path(__file__).resolve().parent.parent / "skills",
        ]

    def reload(self) -> None:
        discovered: dict[str, Skill] = {}
        for directory in self._directories():
            if not directory.exists() or not directory.is_dir():
                continue
            for candidate in sorted(directory.iterdir()):
                skill: Skill | None = None
                if candidate.is_dir():
                    skill_file = candidate / "SKILL.md"
                    if not skill_file.exists():
                        skill_file = candidate / "skill.md"
                    if skill_file.exists():
                        skill = self._load(skill_file, candidate.name)
                elif candidate.suffix.lower() in (".md", ".markdown"):
                    skill = self._load(candidate, candidate.stem)
                if skill is not None and skill.name not in discovered:
                    discovered[skill.name] = skill
        self.skills = discovered

    def _load(self, path: Path, fallback_name: str) -> Skill | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        metadata, body = _parse_frontmatter(text)
        name = metadata.get("name") or fallback_name
        description = metadata.get("description") or ""
        if not description:
            for line in body.splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    description = stripped[:200]
                    break
        if not name or not body:
            return None
        return Skill(
            name=name.strip(),
            description=description.strip(),
            path=path,
            content=body,
            source="workspace" if path.is_relative_to(self.workspace) else "user",
        )

    def summary(self) -> str:
        if not self.skills:
            return ""
        lines = ["Available skills (call the `skill` tool with a name to load full instructions):"]
        for skill in sorted(self.skills.values(), key=lambda item: item.name):
            lines.append(f"- {skill.name}: {skill.description}")
        return "\n".join(lines)

    def get(self, name: str) -> Skill | None:
        if name in self.skills:
            return self.skills[name]
        lowered = name.lower()
        for skill in self.skills.values():
            if skill.name.lower() == lowered:
                return skill
        return None

    def tool_specs(self) -> list[ToolSpec]:
        def skill_tool(context: ToolContext, args: dict[str, Any]) -> ToolResult:
            name = str(args.get("name", "")).strip()
            skill = self.get(name)
            if skill is None:
                available = ", ".join(sorted(self.skills)) or "(none)"
                return ToolResult.failure(
                    f"Unknown skill '{name}'. Available skills: {available}"
                )
            return ToolResult.success(
                f"# Skill: {skill.name}\n\n{skill.content}",
                skill=skill.name,
                path=str(skill.path),
            )

        def list_tool(context: ToolContext, args: dict[str, Any]) -> ToolResult:
            if not self.skills:
                return ToolResult.success("No skills are installed.")
            lines = [
                f"- {skill.name}: {skill.description} ({skill.path})"
                for skill in sorted(self.skills.values(), key=lambda item: item.name)
            ]
            return ToolResult.success("\n".join(lines), count=len(self.skills))

        return [
            ToolSpec(
                name="skill",
                description=(
                    "Load the full instructions for a named skill. Use this when a task matches "
                    "one of the available skills."
                ),
                parameters=object_schema(
                    {"name": string_prop("Skill name from the available skills list")},
                    ["name"],
                ),
                handler=skill_tool,
                permission="read",
            ),
            ToolSpec(
                name="skills_list",
                description="List installed skills with descriptions and paths.",
                parameters=object_schema({}),
                handler=list_tool,
                permission="read",
            ),
        ]

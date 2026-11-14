"""Tool framework exports."""

from __future__ import annotations

from core.tooling.filesystem import filesystem_tools
from core.tooling.git import git_tools
from core.tooling.patch import patch_tools
from core.tooling.registry import ToolRegistry
from core.tooling.shell import shell_tools
from core.tooling.types import (
    ToolContext,
    ToolResult,
    ToolSpec,
    object_schema,
    string_prop,
    integer_prop,
    boolean_prop,
)
from core.tooling.web import web_tools

__all__ = [
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "boolean_prop",
    "build_default_registry",
    "filesystem_tools",
    "git_tools",
    "integer_prop",
    "object_schema",
    "patch_tools",
    "shell_tools",
    "string_prop",
    "web_tools",
]


def build_default_registry(*, include_git: bool = True, include_web: bool = True) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(filesystem_tools())
    registry.register_all(shell_tools())
    registry.register_all(patch_tools())
    if include_git:
        registry.register_all(git_tools())
    if include_web:
        registry.register_all(web_tools())
    return registry

"""Tool registry: schema generation, aliases, validation, and dispatch."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from core.tooling.types import (
    ToolContext,
    ToolResult,
    ToolSpec,
    coerce_arguments,
    truncate_output,
    validate_required,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec
        for alias in spec.aliases:
            self._aliases[alias] = spec.name

    def register_all(self, specs: list[ToolSpec]) -> None:
        for spec in specs:
            self.register(spec)

    def get(self, name: str) -> ToolSpec | None:
        canonical = self._aliases.get(name, name)
        return self._tools.get(canonical)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def openai_tools(self, compact: bool = True) -> list[dict[str, Any]]:
        return [spec.openai_schema(compact=compact) for spec in self._tools.values()]

    def prompt_catalog(self) -> str:
        """Human-readable catalog used by the prompt-based fallback."""
        lines: list[str] = []
        for spec in self._tools.values():
            params = spec.parameters.get("properties", {})
            required = set(spec.parameters.get("required", []))
            rendered = []
            for key, prop in params.items():
                marker = "" if key in required else "?"
                rendered.append(f"{key}{marker}: {prop.get('type', 'any')}")
            lines.append(f"- {spec.name}({', '.join(rendered)}): {spec.description}")
        return "\n".join(lines)

    async def execute(
        self,
        name: str,
        args: dict[str, Any] | None,
        context: ToolContext,
    ) -> ToolResult:
        spec = self.get(name)
        if spec is None:
            available = ", ".join(sorted(self._tools))
            return ToolResult.failure(
                f"Unknown tool '{name}'. Available tools: {available}"
            )
        arguments = coerce_arguments(spec, args or {})
        problem = validate_required(spec, arguments)
        if problem:
            return ToolResult.failure(problem)

        try:
            result = spec.handler(context, arguments)
            if inspect.isawaitable(result):
                result = await result
        except FileNotFoundError as exc:
            return ToolResult.failure(str(exc))
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        except asyncio.CancelledError:  # type: ignore[name-defined]
            raise
        except Exception as exc:  # noqa: BLE001 - tool boundary must not crash agent
            return ToolResult.failure(f"{type(exc).__name__}: {exc}")

        if not isinstance(result, ToolResult):
            result = ToolResult.success(str(result))
        result.output = truncate_output(result.output, spec.max_output_chars)
        return result

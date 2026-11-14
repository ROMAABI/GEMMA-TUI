"""Core tool contracts shared by every tool implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class ToolResult:
    """Outcome of a tool invocation.

    ``ok`` describes whether the operation itself succeeded. A shell command
    that exits non-zero still produced a valid observation, so callers should
    always forward ``output`` to the model regardless of ``ok``.
    """

    ok: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(cls, output: str, **metadata: Any) -> "ToolResult":
        return cls(ok=True, output=output, metadata=metadata)

    @classmethod
    def failure(cls, error: str, output: str | None = None, **metadata: Any) -> "ToolResult":
        return cls(
            ok=False,
            output=output if output is not None else f"Error: {error}",
            metadata=metadata,
            error=error,
        )


@dataclass
class ToolContext:
    """Runtime context handed to every tool handler."""

    root: Path
    session_id: str | None = None
    settings: Any = None
    permission: Any = None
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()

    def resolve(self, path: str | None, *, must_exist: bool = False) -> Path:
        """Resolve a workspace-relative path and reject escapes.

        Absolute paths are accepted only when they are inside the workspace.
        Symlinks are resolved before the containment check.
        """
        raw = (path or ".").strip()
        if not raw:
            raw = "."
        candidate = Path(raw)
        target = candidate if candidate.is_absolute() else self.root / candidate
        target = target.resolve()
        if target != self.root and not target.is_relative_to(self.root):
            raise ValueError(
                f"Access denied: '{raw}' escapes workspace '{self.root}'. "
                "Only paths inside the workspace are accessible; ask the user to "
                "move the file into the workspace or change the workspace root."
            )
        if must_exist and not target.exists():
            raise FileNotFoundError(f"Path does not exist: {raw}")
        return target

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root)).replace("\\", "/")
        except Exception:
            return str(path)


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult] | ToolResult]


@dataclass
class ToolSpec:
    """A model-visible tool and its implementation."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    permission: str = "read"
    aliases: tuple[str, ...] = ()
    max_output_chars: int = 30_000

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def object_schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }
    schema.update(extra)
    return schema


def string_prop(description: str, **extra: Any) -> dict[str, Any]:
    prop = {"type": "string", "description": description}
    prop.update(extra)
    return prop


def integer_prop(description: str, **extra: Any) -> dict[str, Any]:
    prop = {"type": "integer", "description": description}
    prop.update(extra)
    return prop


def boolean_prop(description: str, **extra: Any) -> dict[str, Any]:
    prop = {"type": "boolean", "description": description}
    prop.update(extra)
    return prop


def truncate_output(text: str, limit: int = 30_000) -> str:
    """Keep the head and tail of a long observation."""
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head - 80
    return (
        text[:head]
        + f"\n\n... [truncated {len(text) - head - tail} characters] ...\n\n"
        + text[-tail:]
    )


def coerce_arguments(spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
    """Best-effort coercion for weak local models.

    Models frequently send numbers as strings or JSON objects as strings. We
    repair the common cases instead of rejecting the whole call.
    """
    props = (spec.parameters or {}).get("properties", {})
    cleaned: dict[str, Any] = {}
    for key, value in (args or {}).items():
        expected = props.get(key, {}).get("type") if isinstance(props, dict) else None
        if expected == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)
        elif expected == "number" and isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                pass
        elif expected == "boolean" and isinstance(value, str):
            if value.strip().lower() in ("true", "1", "yes", "on"):
                value = True
            elif value.strip().lower() in ("false", "0", "no", "off"):
                value = False
        elif expected == "string" and isinstance(value, (int, float, bool)):
            value = "true" if value is True else "false" if value is False else str(value)
        elif expected == "string" and isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        elif expected in ("object", "array") and isinstance(value, str):
            try:
                parsed = json.loads(value)
                if expected == "object" and isinstance(parsed, dict):
                    value = parsed
                elif expected == "array" and isinstance(parsed, list):
                    value = parsed
            except Exception:
                pass
        cleaned[key] = value
    return cleaned


def validate_required(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    required = (spec.parameters or {}).get("required", [])
    missing = [name for name in required if name not in args or args[name] is None]
    if missing:
        return (
            f"Missing required argument(s) for {spec.name}: {', '.join(missing)}. "
            f"Expected schema: {json.dumps(spec.parameters, separators=(',', ':'))}"
        )
    return None

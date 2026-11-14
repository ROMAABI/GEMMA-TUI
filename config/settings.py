from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from utils.paths import config_file


def _toml_value(value: Any) -> str:
    """Serialize a Python value as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


@dataclass
class Settings:
    base_url: str = "http://127.0.0.1:8080"
    model: str = "gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M"
    temperature: float = 0.7
    api_key: str = ""
    title: str = "Gemma Local Assistant"
    username: str = ""
    theme: str = "opencode"
    searxng_url: str = "http://localhost:8888"
    searxng_deep_read: bool = True
    agent_mode: bool = True
    workspace_dir: str = "."
    agent_max_steps: int = 10
    agent_confirm_commands: bool = False
    agent_max_tokens: int = 4096
    agent_parallel_tools: int = 4
    agent_tool_timeout: int = 180
    provider: str = "auto"  # auto | native | prompt
    agent_warmup: bool = True
    context_limit: int = 32_768
    compact_threshold: float = 0.8
    compact_keep_tokens: int = 4_000
    permission_mode: str = "auto"  # auto | ask | readonly
    permission_rules: list[str] = field(default_factory=list)
    mcp_enabled: bool = True
    mcp_config: str = ".gemma/mcp.json"
    lsp_enabled: bool = True
    skills_enabled: bool = True
    skills_dirs: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        path = config_file()
        if path.exists():
            data = tomllib.loads(path.read_text())
            settings.base_url = data.get("server", {}).get("base_url", settings.base_url)
            settings.api_key = data.get("server", {}).get("api_key", settings.api_key)
            settings.model = data.get("model", {}).get("name", settings.model)
            settings.temperature = data.get("model", {}).get("temperature", settings.temperature)
            settings.title = data.get("ui", {}).get("title", settings.title)
            settings.username = data.get("ui", {}).get("name", settings.username)
            settings.theme = data.get("ui", {}).get("theme", settings.theme)
            settings.searxng_url = data.get("search", {}).get("searxng_url", settings.searxng_url)
            settings.searxng_deep_read = data.get("search", {}).get("deep_read", settings.searxng_deep_read)

            agent = data.get("agent", {})
            settings.agent_mode = agent.get("enabled", settings.agent_mode)
            settings.workspace_dir = agent.get("workspace_dir", settings.workspace_dir)
            settings.agent_max_steps = agent.get("max_steps", settings.agent_max_steps)
            settings.agent_confirm_commands = agent.get("confirm_commands", settings.agent_confirm_commands)
            settings.agent_max_tokens = agent.get("max_tokens", settings.agent_max_tokens)
            settings.agent_parallel_tools = agent.get("parallel_tools", settings.agent_parallel_tools)
            settings.agent_tool_timeout = agent.get("tool_timeout", settings.agent_tool_timeout)
            settings.provider = agent.get("provider", settings.provider)
            settings.agent_warmup = agent.get("warmup", settings.agent_warmup)
            settings.context_limit = agent.get("context_limit", settings.context_limit)
            settings.compact_threshold = agent.get("compact_threshold", settings.compact_threshold)
            settings.compact_keep_tokens = agent.get("compact_keep_tokens", settings.compact_keep_tokens)

            permissions = data.get("permissions", {})
            settings.permission_mode = permissions.get("mode", settings.permission_mode)
            rules = permissions.get("rules", settings.permission_rules)
            if isinstance(rules, list):
                settings.permission_rules = [str(rule) for rule in rules]

            mcp = data.get("mcp", {})
            settings.mcp_enabled = mcp.get("enabled", settings.mcp_enabled)
            settings.mcp_config = mcp.get("config", settings.mcp_config)

            lsp = data.get("lsp", {})
            settings.lsp_enabled = lsp.get("enabled", settings.lsp_enabled)

            skills = data.get("skills", {})
            settings.skills_enabled = skills.get("enabled", settings.skills_enabled)
            skill_dirs = skills.get("dirs", settings.skills_dirs)
            if isinstance(skill_dirs, list):
                settings.skills_dirs = [str(item) for item in skill_dirs]

        settings.base_url = os.getenv("GEMMA_BASE_URL", settings.base_url).rstrip("/")
        settings.api_key = os.getenv("GEMMA_API_KEY", settings.api_key)
        settings.searxng_url = os.getenv("SEARXNG_URL", settings.searxng_url).rstrip("/")
        env_agent = os.getenv("GEMMA_AGENT_MODE")
        if env_agent is not None:
            settings.agent_mode = env_agent.lower() in ("1", "true", "yes", "on")
        env_ws = os.getenv("GEMMA_WORKSPACE")
        if env_ws:
            settings.workspace_dir = env_ws
        settings.provider = os.getenv("GEMMA_PROVIDER", settings.provider).lower()
        env_warmup = os.getenv("GEMMA_WARMUP")
        if env_warmup is not None:
            settings.agent_warmup = env_warmup.lower() in ("1", "true", "yes", "on")
        settings.permission_mode = os.getenv(
            "GEMMA_PERMISSION_MODE", settings.permission_mode
        ).lower()
        env_context = os.getenv("GEMMA_CONTEXT_LIMIT")
        if env_context:
            try:
                settings.context_limit = int(env_context)
            except ValueError:
                pass
        env_model = os.getenv("GEMMA_MODEL")
        if env_model:
            settings.model = env_model
        else:
            # Auto-detect currently loaded model from live server.
            try:
                from app.lifecycle import get_loaded_model

                loaded = get_loaded_model(settings.base_url)
                if loaded:
                    settings.model = loaded
            except Exception:
                pass
        settings.theme = os.getenv("GEMMA_THEME", settings.theme)
        return settings

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_model(self, new_model: str) -> None:
        self.model = new_model
        self.save_config()

    def save_endpoint(self, base_url: str, model: str) -> None:
        """Switch provider endpoint and model in one write."""
        self.base_url = (base_url or self.base_url).rstrip("/")
        self.model = model or self.model
        self.save_config()

    def save_theme(self, new_theme: str) -> None:
        self.theme = new_theme
        self.save_config()

    def save_searxng_url(self, new_url: str) -> None:
        self.searxng_url = new_url.rstrip("/")
        self.save_config()

    def save_agent_mode(self, enabled: bool) -> None:
        self.agent_mode = enabled
        self.save_config()

    def save_workspace_dir(self, new_dir: str) -> None:
        self.workspace_dir = new_dir
        self.save_config()

    def save_agent_max_steps(self, steps: int) -> None:
        self.agent_max_steps = max(1, min(steps, 50))
        self.save_config()

    def save_agent_confirm_commands(self, confirm: bool) -> None:
        self.agent_confirm_commands = confirm
        self.save_config()

    def save_provider(self, provider: str) -> None:
        normalized = provider.lower()
        if normalized not in ("auto", "native", "prompt"):
            raise ValueError("provider must be auto, native, or prompt")
        self.provider = normalized
        self.save_config()

    def save_permission_mode(self, mode: str) -> None:
        normalized = mode.lower()
        if normalized not in ("auto", "ask", "readonly"):
            raise ValueError("permission mode must be auto, ask, or readonly")
        self.permission_mode = normalized
        self.save_config()

    def save_permission_rules(self, rules: list[str]) -> None:
        self.permission_rules = list(rules)
        self.save_config()

    def save_context_limit(self, limit: int) -> None:
        self.context_limit = max(2_000, min(int(limit), 1_000_000))
        self.save_config()

    def save_mcp_enabled(self, enabled: bool) -> None:
        self.mcp_enabled = enabled
        self.save_config()

    def save_lsp_enabled(self, enabled: bool) -> None:
        self.lsp_enabled = enabled
        self.save_config()

    def save_skills_enabled(self, enabled: bool) -> None:
        self.skills_enabled = enabled
        self.save_config()

    def _config_values(self) -> dict[str, dict[str, Any]]:
        """Return the sections this class owns, keyed by TOML table name."""
        return {
            "server": {
                "base_url": self.base_url,
                "api_key": self.api_key,
            },
            "model": {
                "name": self.model,
                "temperature": self.temperature,
            },
            "ui": {
                "title": self.title,
                "name": self.username,
                "theme": self.theme,
            },
            "search": {
                "searxng_url": self.searxng_url,
                "deep_read": self.searxng_deep_read,
            },
            "agent": {
                "enabled": self.agent_mode,
                "workspace_dir": self.workspace_dir,
                "max_steps": self.agent_max_steps,
                "confirm_commands": self.agent_confirm_commands,
                "max_tokens": self.agent_max_tokens,
                "parallel_tools": self.agent_parallel_tools,
                "tool_timeout": self.agent_tool_timeout,
                "provider": self.provider,
                "warmup": self.agent_warmup,
                "context_limit": self.context_limit,
                "compact_threshold": self.compact_threshold,
                "compact_keep_tokens": self.compact_keep_tokens,
            },
            "permissions": {
                "mode": self.permission_mode,
                "rules": self.permission_rules,
            },
            "mcp": {
                "enabled": self.mcp_enabled,
                "config": self.mcp_config,
            },
            "lsp": {
                "enabled": self.lsp_enabled,
            },
            "skills": {
                "enabled": self.skills_enabled,
                "dirs": self.skills_dirs,
            },
        }

    def save_config(self) -> None:
        """Write config, merging our keys into any existing file.

        Unknown sections and keys written by the user are preserved rather than
        silently discarded. Formatting/comments are not preserved.
        """
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)

        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                existing = {}

        merged: dict[str, Any] = dict(existing)
        for section, values in self._config_values().items():
            current = merged.get(section)
            if not isinstance(current, dict):
                current = {}
            current = dict(current)
            current.update(values)
            merged[section] = current

        lines: list[str] = []
        tables: list[tuple[str, dict[str, Any]]] = []
        for key, value in merged.items():
            if isinstance(value, dict):
                tables.append((key, value))
            else:
                lines.append(f"{key} = {_toml_value(value)}")
        if lines and tables:
            lines.append("")
        for index, (section, values) in enumerate(tables):
            lines.append(f"[{section}]")
            for key, value in values.items():
                lines.append(f"{key} = {_toml_value(value)}")
            if index < len(tables) - 1:
                lines.append("")

        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def ensure_written(self) -> Path:
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            self.save_config()
        return path

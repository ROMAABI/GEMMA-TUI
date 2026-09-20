from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from utils.paths import config_file


@dataclass
class Settings:
    base_url: str = "http://127.0.0.1:8080"
    model: str = "gemma-4-E4B-it-Q4_K_M"
    temperature: float = 0.7
    title: str = "Gemma Local Assistant"
    username: str = ""
    theme: str = "tokyo-night"
    searxng_url: str = "http://localhost:8888"
    searxng_deep_read: bool = True
    agent_mode: bool = True
    workspace_dir: str = "."
    agent_max_steps: int = 10
    agent_confirm_commands: bool = False

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        path = config_file()
        if path.exists():
            data = tomllib.loads(path.read_text())
            settings.base_url = data.get("server", {}).get("base_url", settings.base_url)
            settings.model = data.get("model", {}).get("name", settings.model)
            settings.temperature = data.get("model", {}).get("temperature", settings.temperature)
            settings.title = data.get("ui", {}).get("title", settings.title)
            settings.username = data.get("ui", {}).get("name", settings.username)
            settings.theme = data.get("ui", {}).get("theme", settings.theme)
            settings.searxng_url = data.get("search", {}).get("searxng_url", settings.searxng_url)
            settings.searxng_deep_read = data.get("search", {}).get("deep_read", settings.searxng_deep_read)
            settings.agent_mode = data.get("agent", {}).get("enabled", settings.agent_mode)
            settings.workspace_dir = data.get("agent", {}).get("workspace_dir", settings.workspace_dir)
            settings.agent_max_steps = data.get("agent", {}).get("max_steps", settings.agent_max_steps)
            settings.agent_confirm_commands = data.get("agent", {}).get("confirm_commands", settings.agent_confirm_commands)

        settings.base_url = os.getenv("GEMMA_BASE_URL", settings.base_url).rstrip("/")
        settings.searxng_url = os.getenv("SEARXNG_URL", settings.searxng_url).rstrip("/")
        env_agent = os.getenv("GEMMA_AGENT_MODE")
        if env_agent is not None:
            settings.agent_mode = env_agent.lower() in ("1", "true", "yes", "on")
        env_ws = os.getenv("GEMMA_WORKSPACE")
        if env_ws:
            settings.workspace_dir = env_ws
        env_model = os.getenv("GEMMA_MODEL")
        if env_model:
            settings.model = env_model
        else:
            # Auto-detect currently loaded model from live server
            try:
                from app.lifecycle import get_loaded_model
                loaded = get_loaded_model(settings.base_url)
                if loaded:
                    settings.model = loaded
            except Exception:
                pass
        settings.theme = os.getenv("GEMMA_THEME", settings.theme)
        return settings

    def as_dict(self) -> dict[str, str | float]:
        return asdict(self)

    def save_model(self, new_model: str) -> None:
        self.model = new_model
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

    def save_config(self) -> None:
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[server]\n"
            f'base_url = "{self.base_url}"\n\n'
            "[model]\n"
            f'name = "{self.model}"\n'
            f"temperature = {self.temperature}\n\n"
            "[ui]\n"
            f'title = "{self.title}"\n'
            f'name = "{self.username}"\n'
            f'theme = "{self.theme}"\n\n'
            "[search]\n"
            f'searxng_url = "{self.searxng_url}"\n'
            f"deep_read = {str(self.searxng_deep_read).lower()}\n\n"
            "[agent]\n"
            f"enabled = {str(self.agent_mode).lower()}\n"
            f'workspace_dir = "{self.workspace_dir}"\n'
            f"max_steps = {self.agent_max_steps}\n"
            f"confirm_commands = {str(self.agent_confirm_commands).lower()}\n"
        )
        path.write_text(content)

    def ensure_written(self) -> Path:
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            self.save_config()
        return path

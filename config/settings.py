from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from utils.paths import config_file


@dataclass
class Settings:
    base_url: str = "http://127.0.0.1:8080"
    model: str = "gemma"
    temperature: float = 0.7
    title: str = "Gemma Local Assistant"
    username: str = ""

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

        settings.base_url = os.getenv("GEMMA_BASE_URL", settings.base_url).rstrip("/")
        settings.model = os.getenv("GEMMA_MODEL", settings.model)
        return settings

    def as_dict(self) -> dict[str, str | float]:
        return asdict(self)

    def ensure_written(self) -> Path:
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                "[server]\n"
                f'base_url = "{self.base_url}"\n\n'
                "[model]\n"
                f'name = "{self.model}"\n'
                f"temperature = {self.temperature}\n\n"
                "[ui]\n"
                f'title = "{self.title}"\n'
            )
        return path

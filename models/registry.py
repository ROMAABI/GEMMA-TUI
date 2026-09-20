from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelProfile:
    name: str
    label: str
    filename: str = ""
    context: int | None = None
    description: str = ""


DEFAULT_MODELS = {
    "gemma": ModelProfile(name="gemma", label="Local Gemma via llama.cpp"),
}


def get_available_models(models_dir: Path | None = None) -> dict[str, ModelProfile]:
    """Return all available local GGUF models in the models directory."""
    models: dict[str, ModelProfile] = {}

    if models_dir is None:
        models_dir = Path(__file__).parent
    if models_dir.exists():
        for gguf in sorted(models_dir.glob("*.gguf")):
            stem = gguf.stem
            if stem.startswith("google_"):
                clean_name = stem[len("google_"):]
            else:
                clean_name = stem
            profile = ModelProfile(
                name=clean_name,
                label=f"Local GGUF: {clean_name}",
                filename=gguf.name,
                description=f"Path: models/{gguf.name}",
            )
            models[clean_name] = profile
            # Also register stem and exact filename as aliases if different
            if stem != clean_name:
                models[stem] = profile
            if gguf.name not in models:
                models[gguf.name] = profile

    if not models:
        models["gemma"] = ModelProfile(name="gemma", label="Local Gemma via llama.cpp")

    return models



from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    name: str
    label: str
    context: int | None = None


DEFAULT_MODELS = {
    "gemma": ModelProfile(name="gemma", label="Local Gemma via llama.cpp"),
}

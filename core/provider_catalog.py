"""Provider catalog shared by the HTTP API and the terminal client.

The local llama.cpp server is the default provider. Extra OpenAI-compatible
endpoints can be declared with ``GEMMA_PROVIDERS`` (a JSON array of
``{"id", "name", "base_url", "models"}``), so Ollama, LM Studio or a second
llama.cpp instance show up in the picker without touching code.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

LOCAL_PROVIDER_ID = "llama.cpp"
_PROBE_TIMEOUT = 1.5


def _local_models(settings: Any) -> list[dict[str, Any]]:
    """Current, server-loaded and local GGUF models, de-duplicated."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str, source: str) -> None:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            entries.append({"name": name, "source": source, "active": name == settings.model})

    add(settings.model, "current")
    try:
        from app.lifecycle import get_loaded_model

        loaded = get_loaded_model(settings.base_url)
        if loaded:
            add(loaded, "loaded")
    except Exception:  # noqa: BLE001 - the catalog must always render
        pass
    try:
        from models.registry import get_available_models

        for name in get_available_models().keys():
            add(name, "local")
    except Exception:  # noqa: BLE001
        pass
    return entries


def _env_providers() -> list[dict[str, Any]]:
    raw = (os.environ.get("GEMMA_PROVIDERS") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    providers: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        base = str(item.get("base_url") or "").rstrip("/")
        if not base:
            continue
        providers.append(
            {
                "id": str(item.get("id") or base),
                "name": str(item.get("name") or base),
                "base_url": base,
                "models": [str(model) for model in (item.get("models") or []) if model],
            }
        )
    return providers


def _probe_models(base_url: str) -> list[str]:
    try:
        response = httpx.get(f"{base_url}/v1/models", timeout=_PROBE_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 - an offline provider is not an error
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]


def catalog(settings: Any) -> dict[str, Any]:
    """Providers with their models; the local llama.cpp server always comes first."""
    providers: list[dict[str, Any]] = [
        {
            "id": LOCAL_PROVIDER_ID,
            "name": "llama.cpp (local)",
            "kind": "local",
            "base_url": settings.base_url,
            "is_default": True,
            "default_model": settings.model,
            "models": _local_models(settings),
        }
    ]

    for extra in _env_providers():
        names = extra["models"] or _probe_models(extra["base_url"])
        providers.append(
            {
                "id": extra["id"],
                "name": extra["name"],
                "kind": "openai",
                "base_url": extra["base_url"],
                "is_default": False,
                "default_model": names[0] if names else "",
                "models": [
                    {
                        "name": name,
                        "source": "remote",
                        "active": extra["base_url"] == settings.base_url and name == settings.model,
                    }
                    for name in names
                ],
            }
        )

    active = next(
        (provider for provider in providers if provider["base_url"] == settings.base_url),
        providers[0],
    )
    return {
        "default": LOCAL_PROVIDER_ID,
        "active": {"provider": active["id"], "model": settings.model},
        "providers": providers,
    }


def switch(settings: Any, provider_id: str, model: str) -> tuple[bool, str]:
    """Point the client at a provider/model pair. Returns (ok, error)."""
    payload = catalog(settings)
    provider = next((item for item in payload["providers"] if item["id"] == provider_id), None)
    if provider is None:
        return False, f"Unknown provider '{provider_id}'"

    chosen = (model or provider.get("default_model") or "").strip()
    names = [entry["name"] for entry in provider.get("models", [])]
    if chosen and names and chosen not in names:
        return False, f"Unknown model '{chosen}' for provider '{provider_id}'"
    if not chosen:
        return False, "model required"

    settings.base_url = provider["base_url"]
    settings.save_endpoint(provider["base_url"], chosen)
    return True, ""

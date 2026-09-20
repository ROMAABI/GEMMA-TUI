from __future__ import annotations

import os
import signal
import subprocess

import httpx


def server_is_healthy(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=2)
        return response.is_success
    except Exception:
        return False


async def async_server_is_healthy(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(f"{base_url.rstrip('/')}/health")
            return response.is_success
    except Exception:
        return False


def clean_model_name(raw_name: str) -> str:
    """Format a raw model path or identifier into a clean display name."""
    from pathlib import Path
    name = Path(raw_name).stem
    if name.startswith("google_"):
        name = name[len("google_"):]
    return name


def get_loaded_model(base_url: str) -> str | None:
    """Query llama.cpp server for the active loaded model name."""
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/v1/models", timeout=1.5)
        if response.is_success:
            data = response.json()
            models = data.get("data", []) or data.get("models", [])
            if models:
                raw = models[0].get("id") or models[0].get("name") or models[0].get("model")
                if raw:
                    return clean_model_name(raw)
    except Exception:
        pass
    return None


async def async_get_loaded_model(base_url: str) -> str | None:
    """Asynchronously query llama.cpp server for the active loaded model name."""
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(f"{base_url.rstrip('/')}/v1/models")
            if response.is_success:
                data = response.json()
                models = data.get("data", []) or data.get("models", [])
                if models:
                    raw = models[0].get("id") or models[0].get("name") or models[0].get("model")
                    if raw:
                        return clean_model_name(raw)
    except Exception:
        pass
    return None


def find_llama_server_pids() -> list[int]:
    """Find PIDs of any running llama-server / llama.cpp server processes."""
    pids: list[int] = []
    try:
        result = subprocess.run(
            ["pgrep", "-f", "llama-server|llama.cpp|llama_server"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return pids


def stop_llama_server() -> int:
    """Send SIGTERM to all llama.cpp server processes. Returns count killed."""
    pids = find_llama_server_pids()
    killed = 0
    my_pid = os.getpid()
    for pid in pids:
        if pid == my_pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except (ProcessLookupError, PermissionError):
            pass
    return killed

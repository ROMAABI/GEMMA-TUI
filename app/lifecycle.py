from __future__ import annotations

import os
import signal
import subprocess

import httpx


def server_is_healthy(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=2)
        return response.is_success
    except httpx.HTTPError:
        return False


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

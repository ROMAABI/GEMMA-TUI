"""Structured per-turn performance log (JSONL) for latency tuning.

Every agent/chat turn appends records so time-to-first-token, tool execution
time, prompt-eval time and generation time can be audited after the fact:

    ~/.local/share/gemma-local/turns.jsonl

Each record is also printed to stdout so it shows up in the server log.
"""

from __future__ import annotations

import json
import time

from utils.paths import data_dir

_PATH = data_dir() / "turns.jsonl"
# Long tool outputs are logged in full up to this cap; the length and digest
# recorded alongside let the UI verify it rendered exactly what the model saw.
MAX_LOG_OUTPUT = 20_000


def log(event: dict) -> None:
    """Append one record to the turn log; never raises."""
    record = {"ts": round(time.time(), 3), **event}
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        with _PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    print(f"[gemma] {line}", flush=True)


def output_for_log(output: str) -> dict:
    """Length, digest and capped text of a tool output, for exact-match audits."""
    import hashlib

    return {
        "output_chars": len(output),
        "output_sha": hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()[:16],
        "output": output[:MAX_LOG_OUTPUT],
    }

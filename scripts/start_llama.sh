#!/usr/bin/env bash
# systemd ExecStart wrapper: load the model chosen by the current power source.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="$("$ROOT/scripts/llama_model_for_power.sh")"
STATE_DIR="$HOME/.local/state/gemma"
mkdir -p "$STATE_DIR"
printf '%s\n' "$MODEL" > "$STATE_DIR/llama-model"

exec "$HOME/.local/bin/llama-server" \
    -m "$MODEL" \
    --host 127.0.0.1 --port 8080 \
    --ctx-size 32768 --parallel 1 -t 12

#!/usr/bin/env bash
# Restart llama-server when the power source (and therefore model) changes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WANTED="$("$ROOT/scripts/llama_model_for_power.sh")"
STATE="$HOME/.local/state/gemma/llama-model"
CURRENT=""
[[ -f "$STATE" ]] && CURRENT="$(cat "$STATE")"

if [[ "$WANTED" != "$CURRENT" ]]; then
    systemctl --user restart gemma-llama-server
fi

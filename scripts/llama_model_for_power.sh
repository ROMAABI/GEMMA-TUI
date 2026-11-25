#!/usr/bin/env bash
# Pick the GGUF to load: smaller model on battery, full 9B when plugged in.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIG="$ROOT/models/Qwen3.5-9B-abliterated.Q4_K_M.gguf"
SMALL="$ROOT/models/Qwen3.5-4B_Abliterated.Q4_K_M.gguf"

case "${GEMMA_FORCE_MODEL:-}" in
    small|4b)
        [[ -f "$SMALL" ]] && { printf '%s\n' "$SMALL"; exit 0; }
        ;;
    large|9b) printf '%s\n' "$BIG"; exit 0 ;;
esac

on_battery=false
if command -v upower >/dev/null 2>&1; then
    state="$(upower -i /org/freedesktop/UPower/devices/battery_BAT0 2>/dev/null | awk '/^[[:space:]]*state:/ {print $2}')"
    case "$state" in
        discharging|empty) on_battery=true ;;
    esac
fi

if $on_battery && [[ -f "$SMALL" ]]; then
    printf '%s\n' "$SMALL"
else
    printf '%s\n' "$BIG"
fi

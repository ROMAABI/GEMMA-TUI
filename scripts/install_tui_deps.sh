#!/usr/bin/env bash
# Install the Bun dependencies for the OpenTUI client on first launch.
#
# The script is idempotent: it re-runs only when tui-ts/node_modules is missing
# or tui-ts/bun.lock changed. A stamp file records the lockfile hash so a
# fresh checkout installs once and a lockfile update reinstalls.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TUI_DIR="$ROOT/tui-ts"
STAMP="$TUI_DIR/.deps-stamp"
LOCK="$TUI_DIR/bun.lock"

# Let the Python launcher pass the Bun binary it already located.
BUN="${BUN:-}"
if [[ -z "$BUN" ]]; then
    if command -v bun >/dev/null 2>&1; then
        BUN="$(command -v bun)"
    elif [[ -x "$HOME/.bun/bin/bun" ]]; then
        BUN="$HOME/.bun/bin/bun"
    else
        echo "error: bun not found (install it from https://bun.sh)" >&2
        exit 1
    fi
fi

if [[ ! -f "$LOCK" ]]; then
    echo "error: $LOCK is missing" >&2
    exit 1
fi

lock_hash() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$LOCK" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$LOCK" | awk '{print $1}'
    else
        # Fallback for systems without a sha256 tool: compare mtime.
        stat -c '%Y' "$LOCK" 2>/dev/null || stat -f '%m' "$LOCK"
    fi
}

hash="$(lock_hash)"
if [[ -d "$TUI_DIR/node_modules" && -f "$STAMP" && "$(cat "$STAMP")" == "$hash" ]]; then
    exit 0
fi

echo "Installing OpenTUI client dependencies (first run / bun.lock changed)..."
cd "$TUI_DIR"
"$BUN" install --frozen-lockfile
printf '%s\n' "$hash" > "$STAMP"
echo "OpenTUI client dependencies ready."

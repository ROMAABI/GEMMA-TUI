#!/usr/bin/env bash
# ==============================================================================
# GEMMA-TUI: Local SearXNG Launcher Script
# Starts a privacy-respecting SearXNG metasearch instance locally via Docker
# ==============================================================================

set -euo pipefail

CONTAINER_NAME="gemma-searxng"
PORT="8888"

echo "🔍 Checking container runtime..."
if command -v docker >/dev/null 2>&1; then
    RUNNER="docker"
elif command -v podman >/dev/null 2>&1; then
    RUNNER="podman"
else
    echo "❌ Error: Neither 'docker' nor 'podman' is installed."
    echo "Please install Docker to run SearXNG locally."
    exit 1
fi

# Check if container is already running
if $RUNNER ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "✅ SearXNG is already running on http://localhost:${PORT}"
    exit 0
fi

# Check if container exists but is stopped
if $RUNNER ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "▶ Starting existing '${CONTAINER_NAME}' container..."
    $RUNNER start "${CONTAINER_NAME}"
    echo "✅ SearXNG started on http://localhost:${PORT}"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEARXNG_DIR="${SCRIPT_DIR}/../searxng"
mkdir -p "${SEARXNG_DIR}"

# Generate settings.yml if it doesn't exist
if [ ! -f "${SEARXNG_DIR}/settings.yml" ]; then
    SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 64)
    cat <<EOF > "${SEARXNG_DIR}/settings.yml"
# SearXNG configuration for GEMMA-TUI
use_default_settings: true

server:
  secret_key: "${SECRET_KEY}"
  limiter: false
  image_proxy: false

search:
  safe_search: 0
  autocomplete: ""
  default_lang: ""
  formats:
    - html
    - json
EOF
    chmod 644 "${SEARXNG_DIR}/settings.yml"
fi

echo "🚀 Launching official SearXNG container on port ${PORT}..."
$RUNNER run -d \
    --name "${CONTAINER_NAME}" \
    -p "${PORT}:8080" \
    -v "${SEARXNG_DIR}:/etc/searxng" \
    -e "SEARXNG_BASE_URL=http://localhost:${PORT}/" \
    --restart unless-stopped \
    searxng/searxng:latest

echo ""
echo "🎉 SearXNG is now running at: http://localhost:${PORT}"
echo "You can test it in GEMMA-TUI with: /websearch status"
echo "To stop: $RUNNER stop ${CONTAINER_NAME}"

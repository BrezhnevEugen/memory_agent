#!/usr/bin/env bash
# Install LightRAG (DeepSeek + OpenAI embeddings) as a launchd service on macOS.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null || PY=python3

echo "▶ venv ($PY)"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q "lightrag-hku[api]" mcp httpx rumps psutil pyobjc-framework-Cocoa

[ -f .env ] || { cp .env.example .env; echo "⚠ .env created from example — fill in API keys"; }
mkdir -p rag_storage inputs logs

echo "▶ launchd"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS"
for p in com.lightrag.server com.lightrag.tray; do
  launchctl unload "$AGENTS/$p.plist" 2>/dev/null || true
  cp "launchd/$p.plist" "$AGENTS/"
  launchctl load "$AGENTS/$p.plist"
done

echo "▶ waiting for server"
for i in $(seq 1 30); do
  curl -sf http://localhost:9621/health >/dev/null && { echo "✓ http://localhost:9621"; exit 0; }
  sleep 2
done
echo "✗ server not up — see logs/server-error.log"; exit 1

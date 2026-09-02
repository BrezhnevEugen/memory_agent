#!/usr/bin/env bash
# Publish dist/BrainAI-<ver>.dmg as a GitHub release and update the repo description.
#   ./release.sh            # version from ./VERSION; release notes from CHANGELOG.md section
set -euo pipefail
cd "$(dirname "$0")"
VERSION="${VERSION:-$(tr -d "[:space:]" < VERSION)}"
DMG="dist/BrainAI-$VERSION.dmg"
[ -f "$DMG" ] || { echo "no $DMG — run ./build.sh first"; exit 1; }

gh repo edit --description "BrainAI — persistent memory for AI agents (Claude, Cursor, Codex): LightRAG knowledge graph + MCP, packaged as a macOS menu bar app. DeepSeek LLM, local bge-m3 embeddings." \
  --add-topic mcp --add-topic lightrag --add-topic knowledge-graph --add-topic claude --add-topic cursor --add-topic macos --add-topic deepseek --add-topic ollama

git rev-parse "v$VERSION" >/dev/null 2>&1 && { echo "tag v$VERSION exists — bump ./VERSION (./bump.sh patch|minor|major)"; exit 1; }
gh release view "v$VERSION" >/dev/null 2>&1 && { echo "release v$VERSION already published"; exit 1; }
git tag "v$VERSION"
git push origin "v$VERSION"

shasum -a 256 "$DMG" | tee "dist/BrainAI-$VERSION.sha256"

gh release create "v$VERSION" "$DMG" "dist/BrainAI-$VERSION.sha256" \
  --title "BrainAI $VERSION" \
  --notes "$(awk -v v="$VERSION" '/^## /{p=($2==v)} p' CHANGELOG.md | tail -n +2)"
echo "✓ https://github.com/BrezhnevEugen/memory_agent/releases/tag/v$VERSION"

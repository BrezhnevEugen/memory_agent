# Changelog

## 0.1.2 — 2026-09-03

- Keep the `pipmaster` runtime dependency when pruning packaging tools from the app bundle
- Bundle the Python Ollama client required by the configured `bge-m3` embedding provider
- Validate the bundled LightRAG server import before signing and notarization

## 0.1.1 — 2026-09-03

- Show the menu-bar icon immediately on cold `.app` launches
- Prevent duplicate instances with a process-lifetime file lock
- Stop bundled Ollama and LightRAG cleanly on Quit, `SIGTERM`, `SIGINT`, and logout
- Remove transient PID/lock files while preserving API tokens, settings, models, and knowledge data
- Remove dangling Intel-only Ollama links so the arm64 bundle passes strict Gatekeeper validation

## 0.1.0 — 2026-09-02

- First release: menu bar app bundling LightRAG server, Ollama (bge-m3 embeddings) and MCP server
- LLM via DeepSeek API (`deepseek-v4-flash` / `deepseek-v4-pro`), key entered in Settings
- One-click MCP install for Claude Desktop, Claude Code, Cursor, Codex
- Start at login, notifications for document/graph updates
- Signed and notarized DMG

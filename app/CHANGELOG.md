# Changelog

## 0.2.0 — 2026-09-03

- **Per-project isolation.** The server (`brainai_server.py`) keeps one LightRAG instance per project, routed by the `LIGHTRAG-WORKSPACE` header; each project's documents, vectors, graph, doc status and LLM cache live under `rag_storage/<project>/` (upload folder under `inputs/<project>/`). No filter on top of a shared graph — physically separate files
- MCP server requires `--project <id>` (or `BRAINAI_PROJECT`) and refuses to start without it; it also refuses to talk to a server without project support, so an old server can never silently mix projects
- Settings is split into **General** and **Projects** tabs. **Projects**: registry (`projects.json`) with a display name, an immutable id and the linked folders of each project. **New…** creates a project, **Link folder…** writes the project-scoped MCP configs for Claude Code (`.mcp.json`), Cursor (`.cursor/mcp.json`) and Codex (`.codex/config.toml`) into a folder in one go, **Unlink** removes them. Any number of folders can share one project. **Claude Desktop →** binds the global Claude Desktop config to the selected project
- Tray: **Open WebUI** lists the projects; picking one points the WebUI at that project and opens it (✓ marks the current one, persisted in `.env` as `BRAINAI_UI_PROJECT`). Document / entity counters name the project they refer to; the entity count is exact
- Existing flat `rag_storage/` is moved into project `default` on first start (files renamed in place, nothing re-indexed)
- Fix MCP tools broken by LightRAG 1.5 API changes: `list_documents` (POST), `get_entity` (`/graph/entity/exists` + neighbourhood), `search_graph` (label search + subgraph), `create_entity` / `create_relation` (nested `entity_data` / `relation_data`), `delete_document` (`doc_ids`)
- `dev.sh reload` now syncs every bundled `.py` (including `updater.py`, `update_ui.py`, `brainai_server.py`)


## 0.1.3 — 2026-09-03

- Add silent six-hour update checks and a manual **Check for updates…** tray action
- Download and install GitHub release DMGs with live progress and automatic relaunch
- Verify the published SHA-256 checksum, Developer ID team and Gatekeeper assessment before replacing the app
- Roll back to the previous app bundle if installation cannot complete
- Migrate both LightRAG MCP entry points to the MCP 2.x `MCPServer` API
- Require `mcp>=2,<3` and fail the app build if the packaged MCP server cannot initialize
- Clarify that BrainAI's LightRAG tools are separate from an agent's native file-based auto-memory

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

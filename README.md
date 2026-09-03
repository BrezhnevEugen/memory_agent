# BrainAI — persistent memory for your AI agents

A macOS menu bar app that gives Claude, Cursor and Codex a shared long-term memory: a local knowledge graph built by [LightRAG](https://github.com/HKUDS/LightRAG), exposed to agents over [MCP](https://modelcontextprotocol.io).

Every chat session is ephemeral. BrainAI is not. Decisions, bug fixes, configs, preferences — an agent saves them during work and finds them again in the next session, in any tool.

Memory is split into **projects** that never overlap: each project has its own documents, vectors and graph on disk, and every MCP connection is bound to exactly one project.

**[⬇ Download the latest BrainAI release](https://github.com/BrezhnevEugen/memory_agent/releases/latest)** · macOS 12+, Apple Silicon

## How it works

```
Claude Desktop / Claude Code / Cursor / Codex
            │  MCP (stdio), one process per project (--project <id>)
            ▼
   ┌─────────────────────────── BrainAI.app ───────────────────────────┐
   │  mcp_server.py ─(LIGHTRAG-WORKSPACE: <id>)→ brainai_server.py     │
   │                        │  LightRAG instance per project           │
   │                        │  rag_storage/<id>/ docs, vectors, graph  │
   │                        │ LLM: DeepSeek API (entities, relations,  │
   │                        │      answers)                            │
   │                        │ Embeddings: bundled Ollama + bge-m3      │
   │                        │      (local, free)                       │
   └───────────────────────────────────────────────────────────────────┘
```

Everything ships inside the `.app`: relocatable Python, LightRAG, the Ollama binary, the MCP server. No Homebrew, no Python, no Docker on the target Mac. The only external dependency is a DeepSeek API key (a few cents per thousand memories).

## First 5 minutes

1. Install Claude Code, Cursor or Codex as usual.
2. Install BrainAI (DMG → Applications), launch it, paste the DeepSeek API key in Settings → General → **Apply**.
3. Settings → **Projects** → **＋ New…** (name, e.g. *ESMO*; the id `esmo` is derived) → **Link folder…** → pick your code folder. BrainAI writes `.mcp.json`, `.cursor/mcp.json` and `.codex/config.toml` into it. Nothing to configure inside the agents.
4. Open the agent in that folder. Claude Code asks once to trust the `lightrag` server; Codex reads `.codex/config.toml` only for trusted folders. Memory is live.

The same steps are in Settings → **Readme**, together with the JSON for agents BrainAI does not know (**Copy config** puts it on the clipboard).

## Install

1. Open the DMG, drag **BrainAI** to Applications, launch it.
2. First run downloads the `bge-m3` embedding model (~1.2 GB, progress in the menu bar).
3. Settings opens → paste your DeepSeek API key (**Get** opens the key page) → **Apply**.
4. Settings → **Projects**: create a project (**New…**: a name plus a short id), then **Link folder…** for every code folder that should use it. BrainAI writes the project-scoped MCP configs (`.mcp.json` for Claude Code, `.cursor/mcp.json` for Cursor, `.codex/config.toml` for Codex) into that folder. Several folders can share one project. Claude Desktop has no folders, so **Claude Desktop →** binds its global config to the selected project. Restart the agent afterwards.

The 🧠 icon in the menu bar shows server status, document and entity counts, RAM, and lets you start/stop the server, open the WebUI (`http://127.0.0.1:9621`) or switch between `deepseek-v4-flash` and `deepseek-v4-pro`. **Open WebUI** lists the projects: pick one to view its graph (the counters refer to the same project).

## Projects

- A project has a display name (anything, editable) and an id: lowercase `[a-z0-9_]`, max 64 characters, immutable (`esmo`, `work_crm`). Folder → id → storage: many folders may point at one id, a folder always has exactly one id. The registry lives in `projects.json`.
- Every MCP process is started with `--project <id>` and sends it as the `LIGHTRAG-WORKSPACE` header on each request. Without an id the MCP server exits; against a server that lacks project support it refuses every call. There is no shared fallback.
- The server keeps one LightRAG instance per project. Storage is physically separate: `rag_storage/<id>/` (documents, chunks, vectors, graph, doc status, LLM cache) and `inputs/<id>/` (uploads). Projects are created on first use; delete a project by removing its folder while the server is stopped.
- Memory that existed before 0.2.0 is moved into project `default` on first start.
- Codex loads a project-level `.codex/config.toml` only for trusted projects.

BrainAI silently checks for a new GitHub release at most once every six hours. **Check for updates…** downloads and installs a signed, notarized DMG in place after verifying its SHA-256 checksum and Developer ID; the previous app is restored automatically if the swap fails.

Data lives in `~/Library/Application Support/BrainAI/` (`.env`, `rag_storage/<project>/`, `inputs/<project>/`, `logs/`, `ollama/models/`). If you already run Ollama on `:11434`, BrainAI reuses it.

## What agents get

MCP tools: `query`, `query_data`, `insert_text`, `create_entity`, `create_relation`, `search_graph`, `get_entity`, `get_graph_labels`, `list_documents`, `delete_document`, `delete_entity`, `health_check`.

A companion [memory skill](lightrag-legacy/memory-skill-unpacked/memory/SKILL.md) tells the agent *when* to read and write: query at the start of non-trivial tasks, save decisions/bugs/configs afterwards, tag entries by domain (`work/`, `personal-project/`, `hobby-esp32/`…), write in English with dates. Since 0.2.0 the hard boundary between projects is the project id, not the domain tag; tags remain useful inside a project.

BrainAI does not replace an agent's native file-based auto-memory. It exposes a separate MCP server whose tools appear under the `mcp__lightrag__*` namespace. Without the companion memory skill (or explicit instructions), the agent will not automatically mirror its private file memory into the LightRAG graph.

## Repository layout

| Path | What |
|---|---|
| `app/` | BrainAI.app source and `build.sh` (bundle, sign, notarize, DMG) — see [app/README.md](app/README.md) |
| `lightrag/` | Same stack without the bundle: venv + launchd for a dev machine |
| `lightrag-legacy/` | Archive of the earlier Ollama-only setup and the memory skill |

## Build from source

```bash
git clone https://github.com/BrezhnevEugen/memory_agent && cd memory_agent/app
./build.sh        # → dist/BrainAI.app, dist/BrainAI-<version>.dmg
```

Signing and notarization are picked up automatically from a Developer ID identity and a `notarytool` keychain profile; without them the build is ad-hoc signed.

## Privacy

Text you save goes to DeepSeek for entity extraction and to answer queries. Embeddings and the graph itself never leave your Mac. Nothing is sent anywhere else.

## License

MIT. Built on LightRAG (MIT) and Ollama (MIT).

# BrainAI — persistent memory for your AI agents

A macOS menu bar app that gives Claude, Cursor and Codex a shared long-term memory: a local knowledge graph built by [LightRAG](https://github.com/HKUDS/LightRAG), exposed to agents over [MCP](https://modelcontextprotocol.io).

Every chat session is ephemeral. BrainAI is not. Decisions, bug fixes, configs, preferences — an agent saves them during work and finds them again in the next session, in any tool.

**[⬇ Download BrainAI-0.1.0.dmg](https://github.com/BrezhnevEugen/memory_agent/releases/latest)** · macOS 12+, Apple Silicon

## How it works

```
Claude Desktop / Claude Code / Cursor / Codex
            │  MCP (stdio)
            ▼
   ┌─────────────────────────── BrainAI.app ───────────────────────────┐
   │  mcp_server.py  →  LightRAG server (:9621)  →  knowledge graph    │
   │                        │ LLM: DeepSeek API (entities, relations,  │
   │                        │      answers)                            │
   │                        │ Embeddings: bundled Ollama + bge-m3      │
   │                        │      (local, free)                       │
   └───────────────────────────────────────────────────────────────────┘
```

Everything ships inside the `.app`: relocatable Python, LightRAG, the Ollama binary, the MCP server. No Homebrew, no Python, no Docker on the target Mac. The only external dependency is a DeepSeek API key (a few cents per thousand memories).

## Install

1. Open the DMG, drag **BrainAI** to Applications, launch it.
2. First run downloads the `bge-m3` embedding model (~1.2 GB, progress in the menu bar).
3. Settings opens → paste your DeepSeek API key (**Get** opens the key page) → **Apply**.
4. Settings → **Connect agents** → click Claude Desktop / Claude Code / Cursor / Codex. Restart that app.

The 🧠 icon in the menu bar shows server status, document and entity counts, RAM, and lets you start/stop the server, open the WebUI (`http://127.0.0.1:9621`) or switch between `deepseek-v4-flash` and `deepseek-v4-pro`.

Data lives in `~/Library/Application Support/BrainAI/` (`.env`, `rag_storage/`, `logs/`, `ollama/models/`). If you already run Ollama on `:11434`, BrainAI reuses it.

## What agents get

MCP tools: `query`, `query_data`, `insert_text`, `create_entity`, `create_relation`, `search_graph`, `get_entity`, `get_graph_labels`, `list_documents`, `delete_document`, `delete_entity`, `health_check`.

A companion [memory skill](lightrag-legacy/memory-skill-unpacked/memory/SKILL.md) tells the agent *when* to read and write: query at the start of non-trivial tasks, save decisions/bugs/configs afterwards, tag entries by domain (`work/`, `personal-project/`, `hobby-esp32/`…), write in English with dates.

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

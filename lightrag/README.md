# LightRAG — DeepSeek + Ollama embeddings

Persistent memory server for agents (Cowork / Cursor / Claude Code) via LightRAG + MCP.
LLM = DeepSeek API (`deepseek-v4-flash`); embeddings = local Ollama `bge-m3` (DeepSeek has no embeddings API). No local LLM.

## Install (macOS)

```bash
cd ~/dev_soft/memory_agent/lightrag
./install.sh           # venv, deps, launchd server + tray
```

On first launch the tray opens Settings — enter the DeepSeek API key there (stored in `.env`, server restarts automatically).

Server: http://localhost:9621 (WebUI, `/docs`, `/health`).

## Files

| File | Purpose |
|---|---|
| `.env.example` | Server config template; `.env` is git-ignored |
| `install.sh` | venv + `lightrag-hku[api]` + launchd |
| `launchd/*.plist` | `com.lightrag.server` (KeepAlive), `com.lightrag.tray` |
| `brainai_server.py` | Server entry used by launchd: one LightRAG instance per project (copy of `app/brainai_server.py`) |
| `mcp_server.py` | MCP (stdio) → LightRAG REST, bound to one project via `--project`; tools: query, insert_text, create_entity/relation, … |
| `lightrag_tray.py` | Menu bar app: server/API status, model switch, docs/entities count, notifications |
| `rag_storage/` | Graph data (git-ignored) |
| `logs/` | launchd stdout/stderr |

This MCP connection is separate from an agent's native file-based auto-memory. Its tools appear as `mcp__lightrag__*`; automatic read/write behavior requires the companion memory skill or equivalent agent instructions.

## MCP client config

Project-scoped (`.mcp.json` in the project folder for Claude Code, `.cursor/mcp.json` for Cursor); `--project` is mandatory and each project gets its own `rag_storage/<id>/`:

```json
{ "mcpServers": { "lightrag": {
  "command": "/Users/eugenbrezhnev/dev_soft/memory_agent/lightrag/.venv/bin/python",
  "args": ["/Users/eugenbrezhnev/dev_soft/memory_agent/lightrag/mcp_server.py", "--lightrag-url", "http://localhost:9621", "--project", "memory_agent"]
}}}
```

## Service management

```bash
launchctl unload ~/Library/LaunchAgents/com.lightrag.server.plist   # stop
launchctl load   ~/Library/LaunchAgents/com.lightrag.server.plist   # start
tail -f logs/server-error.log
```

## Notes

- Old graph from `lightrag-legacy/rag_storage` uses the same bge-m3 (dim 1024) — vectors are reusable, but entities were extracted by qwen; better to re-insert the 28 docs from `kv_store_full_docs.json` so DeepSeek rebuilds the graph.
- Switch to `deepseek-v4-pro` via tray Settings or `LLM_MODEL` in `.env`; `QUERY_LLM_MODEL=deepseek-v4-pro` lets you keep flash for indexing and pro for answers.

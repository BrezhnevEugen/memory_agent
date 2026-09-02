# LightRAG — DeepSeek + OpenAI embeddings

Persistent memory server for agents (Cowork / Cursor / Claude Code) via LightRAG + MCP.
Cloud only, no Ollama: LLM = DeepSeek (`deepseek-v4-flash`), embeddings = OpenAI `text-embedding-3-small`.

## Install (macOS)

```bash
cd ~/dev_soft/memory_agent/lightrag
./install.sh           # venv, deps, launchd server + tray
```

On first launch the tray opens Settings — enter DeepSeek and OpenAI API keys there (stored in `.env`, server restarts automatically).

Server: http://localhost:9621 (WebUI, `/docs`, `/health`).

## Files

| File | Purpose |
|---|---|
| `.env.example` | Server config template; `.env` is git-ignored |
| `install.sh` | venv + `lightrag-hku[api]` + launchd |
| `launchd/*.plist` | `com.lightrag.server` (KeepAlive), `com.lightrag.tray` |
| `mcp_server.py` | MCP (stdio) → LightRAG REST; tools: query, insert_text, create_entity/relation, … |
| `lightrag_tray.py` | Menu bar app: server/API status, model switch, docs/entities count, notifications |
| `rag_storage/` | Graph data (git-ignored) |
| `logs/` | launchd stdout/stderr |

## MCP client config

```json
{ "mcpServers": { "lightrag": {
  "command": "/Users/eugenbrezhnev/dev_soft/memory_agent/lightrag/.venv/bin/python",
  "args": ["/Users/eugenbrezhnev/dev_soft/memory_agent/lightrag/mcp_server.py", "--lightrag-url", "http://localhost:9621"]
}}}
```

## Service management

```bash
launchctl unload ~/Library/LaunchAgents/com.lightrag.server.plist   # stop
launchctl load   ~/Library/LaunchAgents/com.lightrag.server.plist   # start
tail -f logs/server-error.log
```

## Notes

- Old graph from `lightrag-legacy/rag_storage` (bge-m3, dim 1024) is incompatible with dim 1536 — start fresh and re-insert the 28 docs from `kv_store_full_docs.json` if needed.
- Switch to `deepseek-v4-pro` via tray Settings or `LLM_MODEL` in `.env`; `QUERY_LLM_MODEL=deepseek-v4-pro` lets you keep flash for indexing and pro for answers.

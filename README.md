# memory_agent

Persistent memory for AI agents (Cowork, Cursor, Claude Code).

- `app/` — self-contained **BrainAI.app** (bundled Python + LightRAG + Ollama binary, DeepSeek API for LLM). See `app/README.md`.
- `lightrag/` — current setup: LightRAG server on DeepSeek API + OpenAI embeddings, MCP server, macOS tray, launchd. See `lightrag/README.md`.
- `lightrag-legacy/` — archive of the previous Ollama-based BrainAI setup. Graph data and `.env` are kept locally only.

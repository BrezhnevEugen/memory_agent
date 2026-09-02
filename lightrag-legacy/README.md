# LightRAG legacy archive

Сохранённые артефакты из `/Users/eugenbrezhnev/dev_soft/LightRAG-main` (удалён 2026-06-15).
Это был клон upstream HKUDS/LightRAG с кастомными надстройками. Сам upstream-клон,
webui/node_modules (~462 МБ) и логи (~20 МБ) не сохранялись — регенерируемо.

## Что здесь
- `rag_storage/` — рабочий граф знаний (28 docs / 28 entities / 28 relations, graphml).
- `rag_storage_backup_20260412/` — бэкап графа на 2026-04-12.
- `memory.skill` + `memory-skill-unpacked/` — Claude-навык «memory» (модель доменов и типов сущностей).
- `custom-code/` — mcp_server.py (LightRAG MCP, Python-референс), lightrag_tray.py, pyproject.toml.
- `docs/` — CLAUDE.md, SETUP_BRAINAI.md, AGENTS.md, BrainAI.zip (ранний снапшот).
- `config/` — .env (с секретами!), launchd-плисты, config.ini.example. Права 700.

## Откуда брать данные графа
Конфиг сервера был: Ollama LLM `qwen2.5:14b`, embeddings `bge-m3` (dim 1024), API на :9621.

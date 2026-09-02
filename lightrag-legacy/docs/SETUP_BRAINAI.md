# BrainAI Setup Guide

Complete guide to setting up LightRAG as persistent AI memory with monitoring, notifications, and integration with Claude Cowork and Cursor IDE.

Based on [LightRAG](https://github.com/HKUDS/LightRAG) — a graph-based RAG framework by HKUDS.

## Prerequisites

- macOS (Apple Silicon tested on M3 Pro)
- Python 3.12 (`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`)
- Homebrew (`/opt/homebrew/bin/brew`)

## 1. Install Ollama

```bash
brew install ollama
```

Pull required models:

```bash
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

Set up autostart:

```bash
brew services start ollama
```

Verify:

```bash
curl http://localhost:11434/api/tags
```

## 2. Install LightRAG

```bash
cd ~/dev_soft/LightRAG-main
pip3 install -e ".[api]"
```

This installs LightRAG in editable mode with API dependencies (FastAPI, uvicorn, etc.).

### Known issue: FastAPI/Starlette version conflict

If you see `TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`, run:

```bash
pip3 install -e ".[api]"
```

This will downgrade starlette to a compatible version (0.41.x). There may be warnings about sse-starlette/reflex conflicts — they don't affect LightRAG.

### Install MCP server dependencies

```bash
pip3 install mcp httpx rumps
```

- `mcp` — Model Context Protocol server library
- `httpx` — async HTTP client for MCP server
- `rumps` — macOS menu bar app framework

## 3. Configure environment

The `.env` file in the project root contains all settings. Key values:

```
HOST=0.0.0.0
PORT=9621
LLM_BINDING=ollama
LLM_BINDING_HOST=http://localhost:11434
LLM_MODEL=qwen2.5:7b
EMBEDDING_BINDING=ollama
EMBEDDING_BINDING_HOST=http://localhost:11434
EMBEDDING_MODEL=nomic-embed-text
```

## 4. Set up autostart for LightRAG server

Copy the launchd plist:

```bash
cp ~/dev_soft/LightRAG-main/com.lightrag.server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.lightrag.server.plist
```

The plist file (`com.lightrag.server.plist`) contains:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lightrag.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Library/Frameworks/Python.framework/Versions/3.12/bin/lightrag-server</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/eugenbrezhnev/dev_soft/LightRAG-main</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Library/Frameworks/Python.framework/Versions/3.12/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>/Users/eugenbrezhnev/dev_soft/LightRAG-main/lightrag-server.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/eugenbrezhnev/dev_soft/LightRAG-main/lightrag-server-error.log</string>
</dict>
</plist>
```

Key details:
- `KeepAlive` with `SuccessfulExit: false` — restarts if server crashes
- `ThrottleInterval: 30` — waits 30 sec between retries (gives Ollama time to start first)
- Logs go to `lightrag-server.log` and `lightrag-server-error.log`

Verify:

```bash
curl http://localhost:9621/health
```

### Startup order after reboot

1. Ollama starts via `brew services` (Login Item)
2. LightRAG starts via launchd, retries every 30 sec until Ollama is ready
3. BrainAI tray starts via launchd

## 5. Set up BrainAI menu bar app

The tray app (`lightrag_tray.py`) provides:
- Green/red status indicator in menu bar
- LightRAG and Ollama status
- Document count
- Start/Stop server controls
- Open WebUI / API Docs
- View logs
- Start/Stop Ollama
- macOS notifications for all document status changes

Copy the launchd plist:

```bash
cp ~/dev_soft/LightRAG-main/com.lightrag.tray.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.lightrag.tray.plist
```

The plist file (`com.lightrag.tray.plist`) contains:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lightrag.tray</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Library/Frameworks/Python.framework/Versions/3.12/bin/python3</string>
        <string>/Users/eugenbrezhnev/dev_soft/LightRAG-main/lightrag_tray.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/eugenbrezhnev/dev_soft/LightRAG-main</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/3.12/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
```

### Notifications

Notifications use `osascript` (not rumps) because macOS blocks Python app notifications without special permissions. All document lifecycle events are tracked:
- **New document received** — when a document is added
- **Processing started** — when Ollama begins extraction
- **Document completed** — when indexing finishes successfully
- **Processing failed** — when extraction fails
- **Knowledge graph updated** — when new entities are extracted

### Manual start/stop

```bash
# Start tray
python3 ~/dev_soft/LightRAG-main/lightrag_tray.py &

# Stop tray
pkill -f lightrag_tray.py
```

## 6. Set up Claude Cowork integration

The memory skill is installed as a Cowork skill (`.claude/skills/memory/SKILL.md`). It instructs Claude to:
- Automatically query LightRAG before complex tasks
- Save architecture decisions, bug fixes, config quirks, user preferences
- Write entries in English with dates for consistent retrieval

The skill triggers when LightRAG MCP tools are available or when the user says "remember this", "what did we decide", etc.

### MCP connection

Cowork connects to LightRAG via the MCP server (`mcp_server.py`) which exposes these tools:
- `query` / `query_data` — search the knowledge base
- `insert_text` — add documents
- `create_entity` / `create_relation` — build the graph
- `delete_document` / `delete_entity` — manage data
- `health_check` — verify server status
- `list_documents` — browse documents
- `get_graph_labels` / `search_graph` / `get_entity` — explore the graph

## 7. Set up Cursor IDE integration

Two files in `.cursor/`:

### `.cursor/mcp.json` — MCP server connection

```json
{
  "mcpServers": {
    "lightrag": {
      "command": "python3",
      "args": [
        "/Users/eugenbrezhnev/dev_soft/LightRAG-main/mcp_server.py",
        "--lightrag-url",
        "http://localhost:9621"
      ]
    }
  }
}
```

### `.cursor/skills/memory/SKILL.md` — Memory skill

Same content as the Cowork memory skill. Cursor activates it via Dynamic Context Discovery when the task matches the skill description.

After adding these files, reload Cursor (`Cmd+Shift+P` → "Reload Window"). The `lightrag` server should appear in Settings → MCP Servers.

## 8. Verify everything works

```bash
# Check Ollama
curl http://localhost:11434/api/tags

# Check LightRAG
curl http://localhost:9621/health

# Check MCP server (manual test)
python3 ~/dev_soft/LightRAG-main/mcp_server.py --lightrag-url http://localhost:9621

# Check tray is running
pgrep -f lightrag_tray.py

# Check launchd services
launchctl list | grep lightrag
```

## Service management

```bash
# Stop LightRAG server
launchctl unload ~/Library/LaunchAgents/com.lightrag.server.plist

# Start LightRAG server
launchctl load ~/Library/LaunchAgents/com.lightrag.server.plist

# Stop tray
launchctl unload ~/Library/LaunchAgents/com.lightrag.tray.plist

# Start tray
launchctl load ~/Library/LaunchAgents/com.lightrag.tray.plist

# Restart Ollama
brew services restart ollama

# View logs
tail -f ~/dev_soft/LightRAG-main/lightrag-server.log
tail -f ~/dev_soft/LightRAG-main/lightrag-server-error.log
```

## Troubleshooting

### LightRAG won't start after reboot
Check if Ollama is running first: `curl http://localhost:11434/api/tags`. LightRAG retries every 30 sec, but if Ollama never starts, check `brew services list`.

### "model not found" errors
Run `ollama pull qwen2.5:7b` and `ollama pull nomic-embed-text`.

### FastAPI `on_startup` error
Run `pip3 install -e ".[api]"` to reinstall with compatible starlette version.

### Tray crashes on start
Check `python3 ~/dev_soft/LightRAG-main/lightrag_tray.py` manually to see the error. Usually a missing `rumps` dependency: `pip3 install rumps`.

### No notifications
Notifications use `osascript`. Test manually: `osascript -e 'display notification "test" with title "BrainAI"'`. If this doesn't work, check System Settings → Notifications → Script Editor.

### Documents stuck in "Failed" status
Usually means Ollama wasn't ready. Click Scan/Retry in WebUI after Ollama is running.

## File inventory

```
~/dev_soft/LightRAG-main/
├── .env                          # Server configuration
├── mcp_server.py                 # MCP server (stdio transport)
├── lightrag_tray.py              # BrainAI menu bar app
├── com.lightrag.server.plist     # launchd config for server
├── com.lightrag.tray.plist       # launchd config for tray
├── lightrag-server.log           # Server stdout log
├── lightrag-server-error.log     # Server stderr log
├── rag_storage/                  # Knowledge graph data
├── .cursor/
│   ├── mcp.json                  # Cursor MCP config
│   └── skills/
│       └── memory/
│           └── SKILL.md          # Cursor memory skill
└── SETUP_BRAINAI.md              # This file
```

```
~/Library/LaunchAgents/
├── com.lightrag.server.plist     # Server autostart
└── com.lightrag.tray.plist       # Tray autostart
```

#!/usr/bin/env bash
# Dev helpers for the built bundle (no rebuild):
#   ./dev.sh reload   copy brainai.py/mcp_server.py/updater.py/update_ui.py into dist/BrainAI.app and relaunch via `open`
#   ./dev.sh diag     launch via open, then via terminal; print the diag lines from both
#   ./dev.sh shot     screenshots of the menu bar after open-launch and terminal-launch (dist/menubar-*.png)
#   ./dev.sh logs     tail app logs
#   ./dev.sh kill     stop app + children
set -uo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
APP="$ROOT/dist/BrainAI.app"; RES="$APP/Contents/Resources"
LOGS="$HOME/Library/Application Support/BrainAI/logs"

kill_all() { pkill -f "$APP/Contents/MacOS/BrainAI"; pkill -f brainai.py; pkill -f "$RES/ollama/ollama"; pkill -f "lightrag.api.lightrag_server"; sleep 1; }
sync_py() { cp brainai.py mcp_server.py updater.py update_ui.py env.default VERSION "$RES/"; codesign --force --sign - "$APP" >/dev/null 2>&1 || true; }

case "${1:-}" in
  reload) kill_all; sync_py; open "$APP"; sleep 3; pgrep -f "$APP/Contents/MacOS/BrainAI" >/dev/null && echo "running" || echo "NOT running — see $LOGS/launcher.log" ;;
  diag)
    kill_all; sync_py
    echo "── via open"; open "$APP"; sleep 8; grep diag "$LOGS/brainai.log" | tail -1
    kill_all
    echo "── via terminal"; "$APP/Contents/MacOS/BrainAI" >/dev/null 2>&1 & sleep 8; grep diag "$LOGS/brainai.log" | tail -1
    ;;
  shot)
    kill_all; sync_py; open "$APP"; sleep 8
    W=$(system_profiler SPDisplaysDataType 2>/dev/null | grep -o 'Resolution: [0-9]*' | head -1 | grep -o '[0-9]*'); W=${W:-3456}
    screencapture -x -R "0,0,$((W/2)),30" dist/menubar-open.png
    kill_all; "$APP/Contents/MacOS/BrainAI" >/dev/null 2>&1 & sleep 8
    screencapture -x -R "0,0,$((W/2)),30" dist/menubar-terminal.png
    echo "saved dist/menubar-open.png dist/menubar-terminal.png"
    ;;
  logs) tail -n 20 "$LOGS"/launcher.log "$LOGS"/brainai.log "$LOGS"/server.log 2>/dev/null ;;
  kill) kill_all; echo stopped ;;
  *) sed -n 2,7p "$0" ;;
esac

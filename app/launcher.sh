#!/bin/bash
# BrainAI.app/Contents/MacOS/BrainAI
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
LOG="$HOME/Library/Application Support/BrainAI/logs"
mkdir -p "$LOG"
export PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd "$HOME"
{
  echo "=== $(date) launch pid=$$ RES=$RES"
  exec "$RES/python/bin/python3" "$RES/brainai.py"
} >> "$LOG/launcher.log" 2>&1

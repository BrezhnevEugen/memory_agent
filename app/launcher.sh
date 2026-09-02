#!/bin/bash
# BrainAI.app/Contents/MacOS/BrainAI
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
export PYTHONNOUSERSITE=1
exec "$RES/python/bin/python3" "$RES/brainai.py"

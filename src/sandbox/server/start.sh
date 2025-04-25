#!/usr/bin/env bash

# ──────────────────────────────────────────────────────────────────────────────
# 0) Load user environment
# ──────────────────────────────────────────────────────────────────────────────
# Important: Paramiko SSH sessions don't source ~/.profile automatically.
source ~/.profile

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# 1) Validate required environment variables
# ──────────────────────────────────────────────────────────────────────────────
if [[ -z "${DISPLAY:-}" ]]; then
    echo "❌ DISPLAY not set"
    exit 1
fi

if [[ -z "${XAUTHORITY:-}" ]]; then
    echo "❌ XAUTHORITY not set"
    exit 1
fi

if [[ ! -f "$XAUTHORITY" ]]; then
    echo "❌ XAUTHORITY file does not exist: $XAUTHORITY"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
# 2) Required defaults
# ──────────────────────────────────────────────────────────────────────────────
: "${HOST:=0.0.0.0}"
: "${PORT:=8765}"
: "${SHARED_DIR:=/tmp/sandbox-server}"
: "${SERVER_LOG:=sandbox-server.log}"

export HOST PORT SHARED_DIR SERVER_LOG DISPLAY XAUTHORITY

# ──────────────────────────────────────────────────────────────────────────────
# 3) Prepare logging
# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# 3) Prepare logging
# ──────────────────────────────────────────────────────────────────────────────
mkdir -p "$SHARED_DIR"
LOG_PATH="$SHARED_DIR/$SERVER_LOG"

# Rotate log if too big
MAX_LOG_SIZE=500000
if [[ -f "$LOG_PATH" && $(stat -c%s "$LOG_PATH") -gt $MAX_LOG_SIZE ]]; then
    backup="$LOG_PATH.$(date +%s).bak"
    mv "$LOG_PATH" "$backup"
    echo "📦 Rotated old log → $backup"
fi

touch "$LOG_PATH"
exec >>"$LOG_PATH" 2>&1

echo "──────────────────────────────────────────────────────"
echo "✅ Starting FastAPI server setup..."
date
echo "→ DISPLAY:    $DISPLAY"
echo "→ XAUTHORITY: $XAUTHORITY"
echo "→ LOG PATH:   $LOG_PATH"
echo "──────────────────────────────────────────────────────"

# ──────────────────────────────────────────────────────────────────────────────
# 4) Allow local X11 access
# ──────────────────────────────────────────────────────────────────────────────
xhost +SI:localuser:"$(whoami)" 2>/dev/null || true

# ──────────────────────────────────────────────────────────────────────────────
# 5) Sync environment if necessary
# ──────────────────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv not found in PATH! Please install uv manually."
    exit 1
fi

# Only run `uv sync` if no .venv exists
if [[ ! -d ".venv" ]]; then
    echo "📦 No virtual environment detected. Running uv sync..."
    uv sync
else
    echo "✅ Virtual environment already exists. Skipping uv sync."
fi

# ──────────────────────────────────────────────────────────────────────────────
# 6) Start FastAPI server inside uv
# ──────────────────────────────────────────────────────────────────────────────
echo "🚀 Launching FastAPI server at http://$HOST:$PORT"
exec uv run -- uvicorn main:app --host="$HOST" --port="$PORT" --reload

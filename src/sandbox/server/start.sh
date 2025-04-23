#!/usr/bin/env bash

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
mkdir -p "$SHARED_DIR"
LOG_PATH="$SHARED_DIR/$SERVER_LOG"

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
# 5) Install uv if needed
# ──────────────────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "→ uv not found, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# ──────────────────────────────────────────────────────────────────────────────
# 6) Start FastAPI server
# ──────────────────────────────────────────────────────────────────────────────
uv sync

echo "🚀 Launching FastAPI server at http://$HOST:$PORT"
nohup uv run -- uvicorn main:app --host="$HOST" --port="$PORT" --reload &

echo "✅ Server launched (PID $!)"

#!/usr/bin/env bash
set -euo pipefail
set -x # Enable x-trace (prints each command before executing)

# ──────────────────────────────────────────────────────────────────────────────
# 1) Ensure `uv` is installed (used to run Python with dependency management)
# ──────────────────────────────────────────────────────────────────────────────

if ! command -v uv >/dev/null 2>&1; then
    echo "→ uv not found – installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# Add local bin to PATH to ensure uv is accessible
export PATH="$HOME/.local/bin:$PATH"

# ──────────────────────────────────────────────────────────────────────────────
# 2) Set up X11 environment (Wayland workaround for GUI access inside container)
# ──────────────────────────────────────────────────────────────────────────────

export DISPLAY="${DISPLAY:-:0}" # Use :0 if DISPLAY is unset or empty

# Find Mutter Xwayland authorization file
shopt -s nullglob
auth_files=(/run/user/1000/.mutter-Xwaylandauth.*)
shopt -u nullglob
AUTH="${auth_files[0]:-}"

if [[ -n "$AUTH" ]]; then
    ln -sf "$AUTH" "$HOME/.Xauthority"
    export XAUTHORITY="$HOME/.Xauthority"
    echo "Using Xauthority → $XAUTHORITY"
else
    echo "ERROR: no Mutter Xwaylandauth found; cannot connect to X" >&2
    exit 1
fi

echo "DISPLAY=$DISPLAY"
echo "XAUTHORITY=$XAUTHORITY"

# ──────────────────────────────────────────────────────────────────────────────
# 3) Validate required environment variables and defaults
# ──────────────────────────────────────────────────────────────────────────────

: "${HOST:=0.0.0.0}"
: "${PORT:=8765}"
: "${SHARED_DIR:=/tmp/sandbox-server}"
: "${SERVER_LOG:=sandbox-server.log}"

if [[ -z "${SHARED_DIR:-}" ]]; then
    echo "ERROR: need SHARED_DIR" >&2
    exit 1
fi

# Allow any client to access X server (use with caution)
xhost +

# ──────────────────────────────────────────────────────────────────────────────
# 4) Rotate log if it exceeds MAX_LOG_SIZE (~500KB)
# ──────────────────────────────────────────────────────────────────────────────

MAX_LOG_SIZE=500000
LOG_PATH="$SHARED_DIR/$SERVER_LOG"

if [[ -f "$LOG_PATH" && $(stat -c%s "$LOG_PATH") -gt $MAX_LOG_SIZE ]]; then
    backup_path="$LOG_PATH.$(date +%s).bak"
    mv "$LOG_PATH" "$backup_path"
    echo "📦 Rotated old log file → $backup_path"
fi

# ──────────────────────────────────────────────────────────────────────────────
# 5) Start the server and log both to file and terminal
# ──────────────────────────────────────────────────────────────────────────────

echo "Launching FastAPI server with uv..."

uv run -- uvicorn main:app --host="$HOST" --port="$PORT" --reload

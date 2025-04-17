#!/usr/bin/env bash
set -x

export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:/usr/bin:/bin"
curl -LsSf https://astral.sh/uv/install.sh | sh

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8765}"
export DISPLAY="${DISPLAY:-:0}"

# 1) Prepare Xauthority
XAUTH="$HOME/.Xauthority"
AUTH="$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -n1)"

if [ -n "$AUTH" ]; then
    echo "Linking Mutter Xauthority → $XAUTH"
    ln -sf "$AUTH" "$XAUTH"
else
    echo "No Mutter Xauthority; generating new one at $XAUTH"
    # Ensure we have xauth
    if ! command -v xauth >/dev/null; then
        echo "ERROR: xauth not found; install it in the VM" >&2
        exit 1
    fi
    touch "$XAUTH"
    chmod 600 "$XAUTH"
    xauth generate "$DISPLAY" . trusted
fi

export XAUTHORITY="$XAUTH"

# 2) Allow local processes
xhost +local:

# 3) Debug
echo "DISPLAY=$DISPLAY"
echo "XAUTHORITY=$XAUTHORITY"
xdpyinfo -display "$DISPLAY" || echo "WARNING: xdpyinfo failed"

# 4) Check screenshots path
if [ -z "$SCREENSHOTS_PATH" ]; then
    echo "ERROR: SCREENSHOTS_PATH not set" >&2
    exit 1
fi

# 5) Launch
uv run main.py --host="$HOST" --port="$PORT" --screenshots-path="$SCREENSHOTS_PATH"

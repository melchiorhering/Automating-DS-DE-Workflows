#!/usr/bin/env bash
set -x

# … install uv, set PATH, HOST, PORT, DISPLAY …

# 1) Pick up Mutter’s cookie (don’t generate your own)
AUTH="$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -n1)"
if [ -n "$AUTH" ]; then
    ln -sf "$AUTH" "$HOME/.Xauthority"
    export XAUTHORITY="$HOME/.Xauthority"
    echo "Using existing Xauthority → $XAUTHORITY"
else
    echo "ERROR: no Mutter Xwaylandauth found; cannot connect to X" >&2
    exit 1
fi

# 2) Allow your user
xhost +SI:localuser:$(whoami)

# 3) Debug
echo "DISPLAY=$DISPLAY"
echo "XAUTHORITY=$XAUTHORITY"
xdpyinfo -display "$DISPLAY" || echo "WARNING: xdpyinfo failed"

# 4) Ensure SCREENSHOTS_PATH, then launch
[ -z "$SCREENSHOTS_PATH" ] && {
    echo "ERROR: need SCREENSHOTS_PATH"
    exit 1
}
uv run main.py --host="$HOST" --port="$PORT" --screenshots-path="$SCREENSHOTS_PATH"

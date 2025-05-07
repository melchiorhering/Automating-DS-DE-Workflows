#!/usr/bin/env bash
source ~/.profile
set -euo pipefail

# ────────────────────────────────────────────────
# ENV Defaults
# ────────────────────────────────────────────────
: "${HOST:=0.0.0.0}"
: "${PORT:=8765}"
: "${SHARED_DIR:=/tmp/sandbox-server}"
: "${SERVER_LOG:=sandbox-server.log}"
: "${JUPYTER_KERNEL_GATEWAY_APP_HOST:=0.0.0.0}"
: "${JUPYTER_KERNEL_GATEWAY_APP_PORT:=8888}"
: "${JUPYTER_KERNEL_NAME:=sandbox-kernel}"

export HOST PORT SHARED_DIR SERVER_LOG \
    JUPYTER_KERNEL_GATEWAY_APP_HOST JUPYTER_KERNEL_GATEWAY_APP_PORT \
    JUPYTER_KERNEL_NAME DISPLAY XAUTHORITY

# ────────────────────────────────────────────────
# Sanity Checks
# ────────────────────────────────────────────────
if [[ -z "${DISPLAY:-}" || -z "${XAUTHORITY:-}" || ! -f "$XAUTHORITY" ]]; then
    echo "❌ DISPLAY or XAUTHORITY not set or invalid"
    exit 1
fi

mkdir -p "$SHARED_DIR"
LOG_PATH="$SHARED_DIR/$SERVER_LOG"
: >"$LOG_PATH" # truncate

exec >>"$LOG_PATH" 2>&1

echo "──────────────────────────────────────────────"
echo "✅ Starting sandbox services..."
date
echo "→ DISPLAY:    $DISPLAY"
echo "→ LOG PATH:   $LOG_PATH"
echo "→ HOST:       $HOST:$PORT"
echo "→ JUPYTER:    $JUPYTER_KERNEL_GATEWAY_APP_HOST:$JUPYTER_KERNEL_GATEWAY_APP_PORT"
echo "──────────────────────────────────────────────"

xhost +SI:localuser:"$(whoami)" 2>/dev/null || true

# ────────────────────────────────────────────────
# Install runtime deps
# ────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv not found"
    exit 1
fi

uv venv --seed
uv pip install jupyter_kernel_gateway smolagents pyautogui requests numpy pandas

# ────────────────────────────────────────────────
# Kill stale services
# ────────────────────────────────────────────────
echo "🔪 Killing existing services..."
pkill -f 'jupyter.*kernelgateway' || true
pkill -f 'uvicorn main:app' || true

# ────────────────────────────────────────────────
# Start Jupyter Kernel Gateway
# ────────────────────────────────────────────────
echo "🚀 Starting Jupyter Kernel Gateway..."
nohup .venv/bin/jupyter kernelgateway \
    --KernelGatewayApp.api=kernel_gateway.jupyter_websocket \
    --ip="$JUPYTER_KERNEL_GATEWAY_APP_HOST" \
    --port="$JUPYTER_KERNEL_GATEWAY_APP_PORT" \
    --KernelGatewayApp.allow_origin='*' \
    --debug >>"$SHARED_DIR/jupyter-kernel.log" 2>&1 &

KERNEL_URL="http://$JUPYTER_KERNEL_GATEWAY_APP_HOST:$JUPYTER_KERNEL_GATEWAY_APP_PORT/api"
deadline=$((SECONDS + 30))
while ((SECONDS < deadline)); do
    if curl -s -o /dev/null -w "%{http_code}" "$KERNEL_URL" | grep -q "200"; then
        echo "✅ Jupyter Kernel Gateway is ready"
        break
    fi
    sleep 1
done

if ((SECONDS >= deadline)); then
    echo "❌ Timeout waiting for Jupyter Kernel Gateway"
    exit 1
fi

# ────────────────────────────────────────────────
# Start FastAPI
# ────────────────────────────────────────────────
nohup uv run --with uvicorn uvicorn main:app --host="$HOST" --port="$PORT" --log-level debug >>"$LOG_PATH" 2>&1 &

sleep 2
if ! pgrep -f 'uvicorn main:app' >/dev/null; then
    echo "❌ FastAPI failed to start. Dumping last 20 log lines:"
    tail -n 20 "$LOG_PATH"
    exit 1
fi

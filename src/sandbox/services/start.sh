#!/usr/bin/env bash
source ~/.profile
set -euo pipefail

# ────────────────────────────────────────────────
# ENV Defaults
# ────────────────────────────────────────────────
: "${HOST:=0.0.0.0}"
: "${PORT:=8765}"
: "${SHARED_DIR:=/tmp/sandbox-server}"
: "${SERVICES_LOG:=sandbox-server.log}"
: "${JUPYTER_KERNEL_GATEWAY_APP_HOST:=0.0.0.0}"
: "${JUPYTER_KERNEL_GATEWAY_APP_PORT:=8888}"

export HOST PORT SHARED_DIR SERVICES_LOG \
    JUPYTER_KERNEL_GATEWAY_APP_HOST JUPYTER_KERNEL_GATEWAY_APP_PORT \
    DISPLAY XAUTHORITY

# ────────────────────────────────────────────────
# Sanity Checks
# ────────────────────────────────────────────────
if [[ -z "${DISPLAY:-}" || -z "${XAUTHORITY:-}" || ! -f "$XAUTHORITY" ]]; then
    echo "❌ DISPLAY or XAUTHORITY not set or invalid"
    exit 1
fi

mkdir -p "$SHARED_DIR"
LOG_PATH="$SHARED_DIR/$SERVICES_LOG"
: >"$LOG_PATH"
exec >>"$LOG_PATH" 2>&1
490
echo "──────────────────────────────────────────────"
echo "✅ Starting sandbox services..."
date
echo "→ DISPLAY:  $DISPLAY"
echo "→ LOG PATH: $LOG_PATH"
echo "→ HOST:     $HOST:$PORT"
echo "→ JUPYTER:  $JUPYTER_KERNEL_GATEWAY_APP_HOST:$JUPYTER_KERNEL_GATEWAY_APP_PORT"
echo "──────────────────────────────────────────────"

xhost +SI:localuser:"$(whoami)" 2>/dev/null || true

# ────────────────────────────────────────────────
# Setup Python venv
# ────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv not found"
    exit 1
fi

uv venv --seed
source .venv/bin/activate

echo "📦 Installing Python dependencies..."
pip install --no-cache-dir \
    jupyter_kernel_gateway \
    smolagents \
    pyautogui \
    requests \
    numpy

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
    --JupyterWebsocketPersonality.list_kernels=True \
    --debug >>"$SHARED_DIR/jupyter-kernel.log" 2>&1 &

# ────────────────────────────────────────────────
# Wait for Kernel Gateway
# ────────────────────────────────────────────────
KERNEL_URL="http://$JUPYTER_KERNEL_GATEWAY_APP_HOST:$JUPYTER_KERNEL_GATEWAY_APP_PORT/api"
deadline=$((SECONDS + 30))
while ((SECONDS < deadline)); do
    if curl -s -f "$KERNEL_URL" >/dev/null; then
        echo "✅ Jupyter Kernel Gateway is ready at /api"
        break
    else
        echo "⏳ Waiting for Jupyter Kernel Gateway..."
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
echo "🚀 Starting FastAPI..."
# either change directory …
nohup uv run uvicorn main:app \
    --host="$HOST" \
    --port="$PORT" \
    --log-level debug >>"$LOG_PATH" 2>&1 &

echo "⏳ Waiting for FastAPI server to start..."
for i in {1..10}; do
    if curl -s "http://$HOST:$PORT/health" | grep -q "ok"; then
        echo "✅ FastAPI server is up and responding"
        break
    fi
    sleep 2
done

if ! pgrep -f 'uvicorn main:app' >/dev/null; then
    echo "❌ FastAPI failed to start. Dumping last 20 log lines:"
    tail -n 20 "$LOG_PATH"
    exit 1
fi

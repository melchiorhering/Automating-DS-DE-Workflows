#!/usr/bin/env bash

# ──────────────────────────────────────────────────────────────────────────────
# 0) Load user environment
# ──────────────────────────────────────────────────────────────────────────────
source ~/.profile
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# 1) Environment Defaults
# ──────────────────────────────────────────────────────────────────────────────
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

# ──────────────────────────────────────────────────────────────────────────────
# 2) X11 Checks
# ──────────────────────────────────────────────────────────────────────────────
if [[ -z "${DISPLAY:-}" || -z "${XAUTHORITY:-}" || ! -f "$XAUTHORITY" ]]; then
    echo "❌ DISPLAY or XAUTHORITY not set or invalid"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
# 3) Logging Setup
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
echo "✅ Starting sandbox services..."
date
echo "→ DISPLAY:    $DISPLAY"
echo "→ XAUTHORITY: $XAUTHORITY"
echo "→ LOG PATH:   $LOG_PATH"
echo "→ SHARED_DIR: $SHARED_DIR"
echo "→ HOST:       $HOST"
echo "→ PORT:       $PORT"
echo "→ JUPYTER_KERNEL_GATEWAY_APP_HOST: $JUPYTER_KERNEL_GATEWAY_APP_HOST"
echo "→ JUPYTER_KERNEL_GATEWAY_APP_PORT: $JUPYTER_KERNEL_GATEWAY_APP_PORT"
echo "→ JUPYTER_KERNEL_NAME: $JUPYTER_KERNEL_NAME"
echo "──────────────────────────────────────────────────────"

# ──────────────────────────────────────────────────────────────────────────────
# 4) X11 Access
# ──────────────────────────────────────────────────────────────────────────────
xhost +SI:localuser:"$(whoami)" 2>/dev/null || true

# ──────────────────────────────────────────────────────────────────────────────
# 5) Virtual Environment Setup
# ──────────────────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv not found! Please install it manually."
    exit 1
fi

# Sync the virtual environment
uv sync

# Update the virtual environment
uv lock --upgrade

#──────────────────────────────────────────────────────────────────────────────
# 6) Register Jupyter kernel if not yet available
#──────────────────────────────────────────────────────────────────────────────

# With UV it is a little bit different, here is an example from the following: https://github.com/astral-sh/uv/issues/6329
# # Start project which generates .venv
# uv init
# # Add a sample library
# uv add requests
# # Add ipykernel to work with notebooks
# uv add ipykernel
# # Install kernelspec for the current .venv
# uv run ipython kernel install --user --name=uv_test
# # Run Jupyter Lab and open a new notebook using the newly created kernel `uv_test`
# uvx --from jupyter-core --with jupyter jupyter lab
# if ! uv run --with jupyter kernelspec list | grep -q "$JUPYTER_KERNEL_NAME"; then
#     echo "📦 Registering Jupyter kernel: $JUPYTER_KERNEL_NAME"
#     uv run ipykernel install --user --name "$JUPYTER_KERNEL_NAME" --display-name "Python (Sandbox)"
# else
#     echo "✅ Jupyter kernel '$JUPYTER_KERNEL_NAME' already registered"
# fi

# ──────────────────────────────────────────────────────────────────────────────
# 7) Start Jupyter Kernel Gateway (background)
# ──────────────────────────────────────────────────────────────────────────────

if ! lsof -iTCP:"$JUPYTER_KERNEL_GATEWAY_APP_PORT" -sTCP:LISTEN >/dev/null; then
    echo "🚀 Starting Jupyter Kernel Gateway on: $JUPYTER_KERNEL_GATEWAY_APP_HOST:$JUPYTER_KERNEL_GATEWAY_APP_PORT"

    # include pip in your project's virtual environment by running uv venv --seed
    uv venv --seed

    nohup uv run -- jupyter kernelgateway \
        --api="kernel_gateway.jupyter_websocket" \
        --ip="$JUPYTER_KERNEL_GATEWAY_APP_HOST" \
        --port=$JUPYTER_KERNEL_GATEWAY_APP_PORT \
        --KernelGatewayApp.allow_origin='*' \
        --debug \
        >>"$SHARED_DIR/jupyter-kernel.log" 2>&1 &
else
    echo "✅ Jupyter Kernel Gateway already running"
fi

# ──────────────────────────────────────────────────────────────────────────────
# 7.5) Wait for Jupyter Kernel Gateway to become available
# ──────────────────────────────────────────────────────────────────────────────

KERNEL_GATEWAY_URL="http://$JUPYTER_KERNEL_GATEWAY_APP_HOST:$JUPYTER_KERNEL_GATEWAY_APP_PORT/api"
echo "⏳ Waiting for Jupyter Kernel Gateway to become ready at $KERNEL_GATEWAY_URL"

deadline=$((SECONDS + 30))
while ((SECONDS < deadline)); do
    if curl -s -o /dev/null -w "%{http_code}" "$KERNEL_GATEWAY_URL" | grep -q "200"; then
        echo "✅ Jupyter Kernel Gateway is ready"
        break
    fi
    sleep 1
done

if ((SECONDS >= deadline)); then
    echo "❌ Timeout waiting for Jupyter Kernel Gateway to become ready"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
# 8) Start FastAPI server
# ──────────────────────────────────────────────────────────────────────────────
echo "🚀 Launching FastAPI server at http://$HOST:$PORT"
exec uv run -- uvicorn main:app --host="$HOST" --port="$PORT" --reload --log-level debug

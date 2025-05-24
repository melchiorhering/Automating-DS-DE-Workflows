#!/usr/bin/env bash
source ~/.profile
set -euo pipefail

# ────────────────────────────────────────────────
# ENV Defaults
# ────────────────────────────────────────────────
: "${SHARED_DIR:=/tmp/sandbox-server}"
: "${TASK_SETUP_LOG:=task-setup.log}"
: "${JUPYTER_PORT:=8888}"
: "${DISPLAY:=:0}"

export DISPLAY

cd /home/user/Desktop

mkdir -p "$SHARED_DIR"
LOG_PATH="$SHARED_DIR/$TASK_SETUP_LOG"
: >"$LOG_PATH"
exec >>"$LOG_PATH" 2>&1

echo "──────────────────────────────────────────────"
echo "📦 Starting Jupyter setup..."
date
echo "→ Log file: $LOG_PATH"
echo "→ Display: $DISPLAY"
echo "──────────────────────────────────────────────"

# ────────────────────────────────────────────────
# 1. Setup Python environment
# ────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "🔧 Creating virtual environment..."
    uv venv --seed
fi
source .venv/bin/activate

# ────────────────────────────────────────────────
# 2. Install dependencies
# ────────────────────────────────────────────────
echo "📦 Installing Python dependencies..."
uv pip install ipykernel jupyterlab

# ────────────────────────────────────────────────
# 3. Register kernel
# ────────────────────────────────────────────────
echo "🧠 Registering kernel..."
uv run ipython kernel install --user \
    --name=jupyterlab \
    --display-name "Python (jupyterlab)" \
    --env VIRTUAL_ENV "$(pwd)/.venv"

# ────────────────────────────────────────────────
# 4. Configure browser
# ────────────────────────────────────────────────
browser=$(command -v chromium || command -v chromium-browser || true)
if [[ -n "$browser" ]]; then
    echo "🌐 Configuring Chromium as Jupyter default browser"
    mkdir -p ~/.jupyter
    echo "c.ServerApp.browser = '$browser'" >~/.jupyter/jupyter_lab_config.py
else
    echo "⚠️ Chromium not found, skipping browser config"
fi

# ────────────────────────────────────────────────
# 5. Disable token login
# ────────────────────────────────────────────────
export JUPYTER_TOKEN=""
echo "🔐 Token login disabled"

# ────────────────────────────────────────────────
# 6. Start JupyterLab in background
# ────────────────────────────────────────────────
echo "🚀 Launching JupyterLab..."
nohup bash -c "BROWSER=\"$browser\" uv run --with jupyter jupyter lab \
    --notebook-dir=\"/home/user/Desktop\" \
    --port=$JUPYTER_PORT \
    --ip=0.0.0.0 \
    --ServerApp.token='' \
    --ServerApp.password=''" \
    >>"$SHARED_DIR/jupyter.log" 2>&1 &

# ────────────────────────────────────────────────
# 7. Wait for Jupyter and launch GUI
# ────────────────────────────────────────────────
echo "⏳ Waiting for JupyterLab to become available..."
jupyter_url="http://localhost:$JUPYTER_PORT/lab"
for i in {1..20}; do
    if curl -s -A "Mozilla/5.0" -L "$jupyter_url" | grep -q "<title>JupyterLab"; then
        echo "✅ JupyterLab is ready at $jupyter_url"
        if [[ -n "$browser" && -n "$DISPLAY" ]]; then
            echo "🌍 Opening JupyterLab in Chromium..."
            nohup "$browser" "$jupyter_url" >/dev/null 2>&1 &
        else
            echo "⚠️ Cannot launch browser (DISPLAY or browser not set)"
        fi
        exit 0
    fi
    sleep 5
done

echo "❌ Timeout: JupyterLab did not respond in time"
exit 1

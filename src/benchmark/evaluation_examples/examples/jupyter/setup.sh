#!/bin/bash
source ~/.profile
set -euxo pipefail

# ────────────────────────────────────────────────
# ENV Defaults
# ────────────────────────────────────────────────
: "${SHARED_DIR:=/tmp/sandbox-server}"
: "${TASK_SETUP_LOG:=task-setup.log}"

cd /home/user/Desktop

mkdir -p "$SHARED_DIR"
LOG_PATH="$SHARED_DIR/$TASK_SETUP_LOG"
: >"$LOG_PATH"
exec >>"$LOG_PATH" 2>&1

echo "──────────────────────────────────────────────"
echo "📦 Starting Jupyter setup..."
date
echo "→ Log file: $LOG_PATH"
echo "──────────────────────────────────────────────"

# 1. Setup Python environment
if [ ! -d ".venv" ]; then
    echo "🔧 Creating virtual environment..."
    uv venv
fi
source .venv/bin/activate

# 2. Install required Python packages
echo "📦 Installing Python dependencies with uv..."
uv pip install \
    jupyter==1.0.0 \
    jupyterlab==4.1.6 \
    ipykernel==6.29.4 \
    numpy==1.26.4 \
    pandas==2.2.2 \
    matplotlib==3.8.4 \
    seaborn==0.13.2 \
    scipy==1.13.0 \
    scikit-learn==1.5.0

# 3. Generate Jupyter configs
echo "🛠 Generating Jupyter configuration files..."
jupyter notebook --generate-config
jupyter lab --generate-config

# 4. Set Chromium as default browser for Jupyter
browser=$(which chromium)
echo "🌐 Setting default browser: $browser"
echo "c.ServerApp.browser = '$browser'" >>~/.jupyter/jupyter_notebook_config.py
echo "c.ServerApp.browser = '$browser'" >>~/.jupyter/jupyter_lab_config.py

# 5. Register the Jupyter kernel
echo "🧠 Registering IPython kernel..."
python -m ipykernel install --user --name=jupyterlab --display-name "Python (jupyterlab)"

# 6. Disable token login
echo "🔐 Disabling Jupyter token..."
export JUPYTER_TOKEN=""

# 7. Launch JupyterLab (auto-opens Chromium)
echo "🚀 Launching JupyterLab..."
jupyter lab --notebook-dir="/home/user/Desktop"

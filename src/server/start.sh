#!/usr/bin/env bash
set -x
# SandboxServer Startup Script

echo "WebSocket Sandbox Server started in $(pwd)"

# Source the runtime environment file, if it exists.
if [ -f /home/user/.vm_env ]; then
    source /home/user/.vm_env
    echo "Sourced runtime environment from /home/user/.vm_env"
else
    echo "WARNING: /home/user/.vm_env not found."
fi

# Install uv if needed.
curl -LsSf https://astral.sh/uv/install.sh | sh

export PATH="$HOME/.local/bin:$PATH"

# These environment variables are set as defaults; they may be overwritten by the runtime env file.
export HOST=${HOST:-"0.0.0.0"}
export PORT=${PORT:-"8765"}
export DISPLAY=${DISPLAY:-":0"}

# Check if the VM_SHARED_DIR variable is set; alse fail the script.
if [ -z "$VM_SHARED_DIR" ]; then
    echo "ERROR: VM_SHARED_DIR is not set. Please set it to a valid path."
    exit 1
fi

echo "PWD is $(pwd)"
echo "$(ls -la)"

# Dynamically detect the Xauthority file and create a symlink.
AUTH_FILE=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -n 1)
if [ -n "$AUTH_FILE" ]; then
    ln -sf "$AUTH_FILE" /home/user/.Xauthority
    export XAUTHORITY=/home/user/.Xauthority
    echo "Using dynamic Xauthority file: $AUTH_FILE"
else
    echo "WARNING: No Xauthority file found in /run/user/1000/."
fi

xhost +SI:localuser:$(whoami)

# Start the server.
uv run main.py --host="$HOST" --port="$PORT" --screenshots-path="$VM_SHARED_DIR"

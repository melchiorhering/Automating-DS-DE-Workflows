#!/usr/bin/env bash
# SandboxServer Startup Script
# This script launches the WebSocket server that provides a sandboxed Python environment

# Move to the script's directory to ensure correct pathing
cd "$(dirname "$0")"

# Set default environment variables if not already set
export HOST=${HOST:-"0.0.0.0"} # Listen on all interfaces by default
export PORT=${PORT:-"8765"}    # Default WebSocket port
export SANDBOX_SCREENSHOTS_PATH=${SANDBOX_SCREENSHOTS_PATH:-"/shared/screenshots"}

echo "Starting SandboxServer on $HOST:$PORT"
echo "Screenshots will be saved to: $SANDBOX_SCREENSHOTS_PATH"

# Run using uv, passing any additional command-line arguments
uv run main.py --host="$HOST" --port="$PORT" --screenshots-path="$SANDBOX_SCREENSHOTS_PATH" "$@"

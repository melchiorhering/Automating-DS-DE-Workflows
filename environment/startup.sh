#!/bin/bash
set -e

echo "============================CUSTOM STARTUP SCRIPT============================"

echo "Waiting for desktop to be ready..."
/usr/bin/desktop_ready

cd $HOME/server

echo "Starting sandbox server with configuration:"
echo "  Working Directory: $(pwd)"
echo "  SANDBOX_HOST: ${SANDBOX_HOST}"
echo "  SANDBOX_PORT: ${SANDBOX_PORT}"
echo "  VNC_RESOLUTION: ${VNC_RESOLUTION}"

exec uv run sandbox_server.py --host "${SANDBOX_HOST:-0.0.0.0}" --port "${SANDBOX_PORT:-8765}"

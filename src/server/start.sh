#!/usr/bin/env bash
set -euo pipefail
set -x # keep the x‑trace

# ─── 1) ensure `uv` is present ─────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "→ uv not found – installing…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# ─── 2) X‑setup ────────────────────────────────────────────────────────────
export DISPLAY="${DISPLAY:--:0}" # fallback to :0 if unset **or empty**

# pick up Mutter’s cookie
shopt -s nullglob # empty globs expand to nothing
auth_files=(/run/user/1000/.mutter-Xwaylandauth.*)
shopt -u nullglob
AUTH="${auth_files[0]:-}"

if [[ -n "$AUTH" ]]; then
    ln -sf "$AUTH" "$HOME/.Xauthority"
    export XAUTHORITY="$HOME/.Xauthority"
    echo "Using Xauthority → $XAUTHORITY"
else
    echo "ERROR: no Mutter Xwaylandauth found; cannot connect to X" >&2
    exit 1
fi

# xhost +SI:localuser:"$(whoami)"

echo "DISPLAY=$DISPLAY"
echo "XAUTHORITY=$XAUTHORITY"
# xdpyinfo -display "$DISPLAY" || echo "WARNING: xdpyinfo failed"

# ─── 3) parameter sanity ───────────────────────────────────────────────────
: "${HOST:=0.0.0.0}" # default if missing
: "${PORT:=8765}"    #     »    »
if [[ -z "${SCREENSHOTS_PATH:-}" ]]; then
    echo "ERROR: need SCREENSHOTS_PATH" >&2
    exit 1
fi

# ─── 4) launch server ──────────────────────────────────────────────────────
uv run main.py \
    --host="$HOST" \
    --port="$PORT" \
    --screenshots-path="$SCREENSHOTS_PATH"

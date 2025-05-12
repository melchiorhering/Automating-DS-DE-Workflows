#!/bin/bash
set -euxo pipefail

cd /home/user/Desktop

# Prepare Python env
if [ ! -d ".venv" ]; then
    uv venv
fi
source .venv/bin/activate

uv pip install jupyterlab ipykernel

python -m ipykernel install --user --name=jupyterlab --display-name "Python (jupyterlab)"

# Disable token login
export JUPYTER_TOKEN=""

# Launch JupyterLab and open in Firefox
jupyter lab --notebook-dir=/home/user/Desktop

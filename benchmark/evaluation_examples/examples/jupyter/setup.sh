#!/bin/bash
set -euxo pipefail

cd /home/user/Desktop

# 1. Setup Python environment
if [ ! -d ".venv" ]; then
    uv venv
fi
source .venv/bin/activate

# 2. Install required Python packages
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
jupyter notebook --generate-config
jupyter lab --generate-config

# 4. Set Firefox as default browser for Jupyter
browser=$(which firefox)
echo "c.ServerApp.browser = '$browser'" >>~/.jupyter/jupyter_notebook_config.py
echo "c.ServerApp.browser = '$browser'" >>~/.jupyter/jupyter_lab_config.py

# 5. Register the Jupyter kernel
python -m ipykernel install --user --name=jupyterlab --display-name "Python (jupyterlab)"

# 6. Disable token login
export JUPYTER_TOKEN=""

# 7. Launch JupyterLab (auto-opens Firefox)
jupyter lab --notebook-dir=/home/user/Desktop

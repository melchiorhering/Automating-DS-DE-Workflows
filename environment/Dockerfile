ARG BASE_TAG="develop"
ARG BASE_IMAGE="core-ubuntu-noble"
ARG INSTALL_EXTRA=""

# Base image from Kasm
# https://github.com/kasmtech/workspaces-core-images/blob/develop/dockerfile-kasm-core
FROM kasmweb/$BASE_IMAGE:$BASE_TAG
USER root

# Set up environment
ENV HOME=/home/kasm-default-profile
ENV STARTUPDIR=/dockerstartup
WORKDIR $HOME

### Environment Config
ENV DEBIAN_FRONTEND=noninteractive \
    SKIP_CLEAN=true \
    KASM_RX_HOME=$STARTUPDIR/kasmrx \
    DONT_PROMPT_WSL_INSTALL="No_Prompt_please" \
    INST_DIR=$STARTUPDIR/install \
    INST_SCRIPTS="/ubuntu/install/tools/install_tools_deluxe.sh \
    /ubuntu/install/misc/install_tools.sh \
    /ubuntu/install/chromium/install_chromium.sh \
    /ubuntu/install/firefox/install_firefox.sh \
    /ubuntu/install/vs_code/install_vs_code.sh \
    /ubuntu/install/cleanup/cleanup.sh"


# Copy install scripts
COPY environment/src $INST_DIR

# Run installations
RUN \
    for SCRIPT in $INST_SCRIPTS; do \
    bash ${INST_DIR}${SCRIPT} || exit 1; \
    done && \
    $STARTUPDIR/set_user_permission.sh $HOME && \
    rm -f /etc/X11/xinit/Xclients && \
    chown 1000:0 $HOME && \
    mkdir -p /home/kasm-user && \
    chown -R 1000:0 /home/kasm-user && \
    rm -Rf ${INST_DIR}


######### Additional Package Installation ###########
RUN apt-get update && apt-get install -y \
    python3-dev \
    python3-tk \
    libx11-6 \
    libx11-dev \
    libxext-dev \
    libxrender-dev \
    libxtst-dev \
    libxi6 \
    scrot \
    gnome-screenshot \
    && rm -rf /var/lib/apt/lists/*

# Define a build argument for additional packages
RUN if [ -n "$INSTALL_EXTRA" ]; then \
    echo "Installing extra packages: $INSTALL_EXTRA" && \
    apt-get update && \
    apt-get install -y $INSTALL_EXTRA && \
    rm -rf /var/lib/apt/lists/*; \
    fi

######### Server Setup ###########
# Add Sandbox Server using UV
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_HTTP_TIMEOUT=60
WORKDIR $HOME/server

# Copy and synchronize server code
COPY src/controller/ controller/
COPY environment/pyproject.toml src/sandbox_server.py ./
RUN uv sync

# Custom Startup Script
# https://kasmweb.com/docs/latest/how_to/building_images.html#custom-startup
COPY environment/startup.sh $STARTUPDIR/custom_startup.sh
RUN chmod +x $STARTUPDIR/custom_startup.sh

######### End Customizations ###########
RUN chown 1000:0 $HOME
RUN $STARTUPDIR/set_user_permission.sh $HOME

ENV HOME /home/kasm-user
WORKDIR $HOME
RUN mkdir -p $HOME && chown -R 1000:0 $HOME

USER 1000

CMD ["--tail-log"]
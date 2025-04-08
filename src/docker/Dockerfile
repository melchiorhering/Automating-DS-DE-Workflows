FROM qemux/qemu:latest

# Define SERVER_DIR argument
ARG SERVER_DIR=./server

# Environment variables matching Docker Compose configuration
ENV BOOT="ubuntu" \
    DEBUG="Y" \
    RAM_SIZE="4G" \
    CPU_CORES="4" \
    DISK_SIZE="12G" \
    PORT="8765" \
    HOST="localhost"

# Document required devices and capabilities for QEMU
# These will be handled by the Python Docker SDK when creating the container:
# Required devices:
# - /dev/kvm
# - /dev/net/tun
# Required capabilities:
# - NET_ADMIN

# Copy the base ENTRYPOINT from qemux/qemu
ENTRYPOINT ["/usr/bin/tini", "-s", "/run/entry.sh"]

# SERVER SETUP
# Install UV from Astral
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_HTTP_TIMEOUT=60

# Setup server directory
WORKDIR /server
COPY ${SERVER_DIR} /server/
RUN uv sync

# Expose both the VNC port (from base image) and the server port
EXPOSE 8006 8765

# Add the run server command
CMD ["uv", "run", "main.py", "--host", "$HOST", "--port", "$PORT"]
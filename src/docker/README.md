# QEMU/KVM (Docker) Ubuntu Base Image

_Build once with QEMU/KVM, run anywhere with the **`qemux/qemu`** Docker image_

---

## 📂 Docker Directory Layout

```
📆docker
 ┣ 📆shared # Directory for shared files between host and container
 ┣ 📆vms
 ┃ ┣ 📆snapshots # The copies of your base image should be stored here
 ┃ ┗ 📆ubuntu-base # OPTIONAL: NEW VARIANT OF UBUNTU BASE IMAGE; Already configured for SSH
 ┃ ┃ ┣ 📋boot.iso # Needed on first start (installs distro)
 ┃ ┃ ┣ 📋data.img # Needed
 ┃ ┃ ┣ 📋qemu.mac # Created on container startup
 ┃ ┃ ┣ 📋uefi.rom # Created on container startup
 ┃ ┃ ┗ 📋uefi.vars # Created on container startup
 ┣ 📋README.md
 ┣ 📋upload_base.py # Python script to upload a base vm to HF repository
 ┣ 📋download_base.py # Python script to download a base vm from a HF repository
 ┗ 📋compose.qemu.yaml
```

---

## 1 · Overview

We use **QEMU/KVM** via the [`qemux/qemu`](https://github.com/qemus/qemu) Docker container to install, configure, and run Ubuntu or any other OS.
Just point `BOOT` at an Ubuntu release alias (e.g. `ubuntu`) and the container will download the ISO for you.

---

## 2 · Host prerequisites

| Host role             | Packages / tools example                |
| --------------------- | --------------------------------------- |
| **Builder + runtime** | **Docker + Docker Compose**             |
|                       | Add user to `docker` _and_ `kvm` groups |

Verify KVM with `kvm-ok` or `lsmod | grep kvm`. If your host is itself a VM, enable **nested VT-x/AMD-V**.

The qemux/qemu Docker image contains all necessary QEMU components, so you don't need to install additional packages like qemu-utils or libguestfs-tools on the host.

> ✨ **Note**: If you are building your own **base image** inside the VM (e.g. `ubuntu-base/`), you must also install **Docker inside the VM** using to support containerized tools like Airbyte and Superset.

---

## 3 · Interactive install via Docker Compose

Create `docker-compose.yml` alongside an empty folder `vms/ubuntu-base`:

```yaml
# Global VM resource settings
# Configuration can be found here: https://github.com/qemus/qemu
# THIS DOCKER COMPOSE FILE IS USED FOR SETTING UP THE BASE VM ENVIRONMENT
services:
  ubuntu-base:
    image: qemux/qemu
    container_name: ubuntu-base

    # Mount the qcow2 we built earlier as /boot.qcow2 (overrides BOOT)
    volumes:
      # - ${ROOT_DIR}/src/docker/vms/snapshots/ubuntu-base-snap1.qcow2:/boot.qcow2 # For using snapshots
      - ${ROOT_DIR}/src/docker/vms/ubuntu-base:/storage # Setting the storage directory
      # - ${ROOT_DIR}/src/docker/shared/ubuntu-base:/shared # Shared directory for the VM; in the container you have to mount `mount -t 9p -o trans=virtio shared /mnt/example`

    # Grant KVM + networking devices
    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN

    # Runtime tweaks
    environment:
      # BOOT: "https://releases.ubuntu.com/jammy/ubuntu-22.04.5-desktop-amd64.iso" # Downloads the Spider2-V
      BOOT: "ubuntu" # SET THIS ONCE FOR THE FIRST CONTAINER BUILD/RUN  (THIS DOWNLOADS THE LATEST ISO)
      RAM_SIZE: "4G" # ↑ RAM (default 2G)
      CPU_CORES: "4" # ↑ vCPUs (default 2)
      DISK_SIZE: "25g" # Set this to resize the disk
      DEBUG: "Y"
      # ARGUMENTS: # Optional: You can create the ARGUMENTS environment variable to provide additional arguments to QEMU at runtime

    ports:
      - 8006:8006 # Web console (noVNC)
      - 2222:22 # Optional SSH from host → guest

    restart: unless-stopped
    stop_grace_period: 2m
```

```bash
docker compose up -f <compose-file> -d
```

1. Browse to **[http://localhost:8006](http://localhost:8006)**.
2. In the web console you’ll see the Ubuntu live environment; run the installer and target the virtual disk under `/storage`.
3. When installation finishes, **shut down the VM** from the guest.

---

## 4 · First boot into your new image

[More details](https://github.com/qemus/qemu?tab=readme-ov-file#faq-) about `qemux/qemu` VM configuration.

```bash
docker compose up -f <compose-file> -d
```

> **Important:** When setting up your VM, be sure to install the specific packages and tools required for your SANDBOX environment. The packages listed below are generic recommendations, but your specific use case may require additional or different tools. Consider your security, automation, and testing requirements when customizing your sandbox VM.

### 4.1 · Install guest tools & SSH

Inside the guest:

```bash
sudo apt update && sudo apt dist-upgrade -y
sudo apt install -y openssh-server curl wget git htop net-tools \
                    build-essential ca-certificates software-properties-common gnome-screenshot
```

> ✅ Also install `docker` if you plan to run containers from inside the guest VM:
> Follow the [Docker installation guide](https://docs.docker.com/engine/install/ubuntu/) for the latest instructions.

### 4.2 · Configure SSH & passwordless sudo

1. **Edit** `/etc/ssh/sshd_config` to allow password authentication if needed (`PasswordAuthentication yes`).

2. **Restart SSH**:

   ```bash
   sudo systemctl restart ssh
   sudo systemctl enable ssh
   ```

3. **Grant user passwordless sudo**:

   ```bash
   sudo tee /etc/sudoers.d/user-nopasswd << 'EOF'
   user ALL=(ALL) NOPASSWD:ALL
   EOF
   sudo chmod 440 /etc/sudoers.d/user-nopasswd
   ```

---

## 5 · Additional setup for GUI control

### 5.1 · Disable Wayland and enable X11 (required for full pyautogui support)

Wayland blocks pyautogui mouse/keyboard control. You must switch to **X11**:
Use [this post to disable Wayland and enable X11](https://askubuntu.com/questions/1343805/failed-to-enable-link-training-when-resuming-from-suspend/1470563#1470563)

### 5.2 · Configure X11 startup in your app container (e.g., FastAPI)

For pyautogui and pynput to work, you need to set up X11 and Xauthority in your app container.
This means that DISPLAY and XAUTHORITY must be set up correctly. Also setup `xhost` to allow access.

```bash
xhost +SI:localuser:$(whoami)  # allow X access
```

> NOTE: Ensure the VM user has `.Xauthority` set up and that you’re not running under Wayland.

---

## 6 · Installed Packages Recap

| Category         | Packages                                                       |
| ---------------- | -------------------------------------------------------------- |
| GUI Tools        | `gnome-screenshot`                                             |
| Dev Essentials   | `build-essential`, `curl`, `wget`, `git`, `python3-dev`, `uv`  |
| Automation       | `pyautogui`, `pynput`, `Pillow`, `numpy`, `fastapi`, `uvicorn` |
| Screenshot/Tests | `Pillow`, `pyautogui`, `Xcursor` (custom, optional)            |

---

## 7 · UV Installation for Persistent PATH Access

Inside the guest VM:

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> ✅ This ensures `uv` is available immediately after login, for **SSH**, **FastAPI server**, and **agent operations**.

---

# 🚀 Conclusion

> This setup gives you a fully portable, Dockerized, reproducible VM system with GUI automation, REST APIs for code execution, screenshots, and recording, ready for agent benchmarking or any sandbox experiments!

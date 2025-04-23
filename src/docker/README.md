# QEMU/KVM (Docker) Ubuntu Base Image

_Build once with QEMU/KVM, run anywhere with the **`qemux/qemu`** Docker image_

---

## 1 · Overview

We use **QEMU/KVM** via the [`qemux/qemu`](https://github.com/qemus/qemu) Docker container to install, configure, and run Ubuntu.
Just point `BOOT` at an Ubuntu release alias (e.g. `ubuntu`) and the container will download the ISO for you.

```
docker compose up ──▶ downloader & installer ──▶ ubuntu-base.qcow2 ──▶ qemux/qemu
```

---

## 2 · Host prerequisites

| Host role             | Packages / tools                                                                                | Ubuntu example                                                                          |
| --------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Builder + runtime** | **Docker 24 + Docker Compose v2**,<br>`qemu-kvm`, `qemu-utils`, `libguestfs-tools` (`virt-v2v`) | `sudo apt install docker.io docker-compose-plugin qemu-kvm qemu-utils libguestfs-tools` |
|                       | Add user to `docker` _and_ `kvm` groups                                                         | `sudo usermod -aG docker,kvm $USER && newgrp docker`                                    |

Verify KVM with `kvm-ok` or `lsmod | grep kvm`. If your host is itself a VM, enable **nested VT-x/AMD-V**.

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
      - ${ROOT_DIR}/src/docker/vms/ubuntu-base:/storage # Setting the storage directory
      # - ${ROOT_DIR}/src/docker/vms/snapshots/ubuntu-base-snap1.qcow2:/boot.qcow2 # For using snapshots
      # - ${ROOT_DIR}/src/docker/shared:/shared

    # Grant KVM + networking devices
    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN

    # Runtime tweaks
    environment:
      # BOOT: "https://releases.ubuntu.com/jammy/ubuntu-22.04.5-desktop-amd64.iso" # Downloads the Spider2-V
      BOOT: "ubuntu"
      RAM_SIZE: "4G" # ↑ RAM (default 2G)
      CPU_CORES: "4" # ↑ vCPUs (default 2)
      DISK_SIZE: "16G"
      DEBUG: "Y"

    ports:
      - 8006:8006 # Web console (noVNC)
      - 2222:22 # Optional SSH from host → guest

    restart: unless-stopped
    stop_grace_period: 2m
```

```bash
docker compose up -f <compose-file> -d
```

1. Browse to **http://localhost:8006**.
2. In the web console you’ll see the Ubuntu live environment; run the installer and target the virtual disk under `/storage`.
3. When installation finishes, **shut down the VM** from the guest.

---

## 4 · First boot into your new image

[More details](https://github.com/qemus/qemu?tab=readme-ov-file#faq-) about `qemux/qemu` VM configuration.

```bash
docker compose up -f <compose-file> -d
```

### 4.1 · Install guest tools & SSH

Inside the guest:

```bash
sudo apt update && sudo apt dist-upgrade -y
sudo apt install -y qemu-guest-agent openssh-server curl wget git vim htop net-tools \
                    build-essential ca-certificates software-properties-common gnome-screenshot
```

### 4.2 · Configure SSH & passwordless sudo

1. **Edit** `/etc/ssh/sshd_config` as shown previously
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

1. Edit the GDM configuration file:

   ```bash
   sudo nano /etc/gdm3/custom.conf
   ```

2. Uncomment or add:

   ```ini
   [daemon]
   WaylandEnable=false
   ```

3. Reboot the VM:

   ```bash
   sudo reboot
   ```

Ref: [askubuntu.com guide](https://askubuntu.com/questions/1343805/failed-to-enable-link-training-when-resuming-from-suspend/1470563#1470563)

### 5.2 · Configure X11 startup in your app container (e.g., FastAPI)

In your container entrypoint (`start.sh`), add:

```bash
export DISPLAY=:0
export XAUTHORITY=$HOME/.Xauthority
xhost +SI:localuser:$(whoami)  # allow X access
```

Ensure the VM user has `.Xauthority` set up and that you’re not running under Wayland.

---

## 6 · Installed Packages Recap

| Category         | Packages                                                             |
| ---------------- | -------------------------------------------------------------------- |
| GUI Tools        | `gnome-screenshot`, `x11-utils`, `fonts-dejavu`, `xdotool`           |
| Dev Essentials   | `build-essential`, `curl`, `wget`, `git`, `python3-dev`, `uv`        |
| Automation       | `pyautogui`, `pynput`, `opencv-python`, `Pillow`, `numpy`, `fastapi` |
| Input Recording  | `pynput`, `xlib`, `Xauthority`, `xhost`                              |
| Screenshot/Tests | `Pillow`, `ImageGrab`, `pyautogui`, `Xcursor` (custom, optional)     |

---

With this setup, you can:

- Launch a headless Ubuntu VM
- Use FastAPI to control GUI (e.g. click, move, screenshot)
- Run `pyautogui` and `pynput` in X11 context
- Serve via noVNC + RESTful interface

Enjoy your sandbox! 🤖

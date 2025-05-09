# QEMU/KVM Ubuntu Base Image (via Docker)

_Build once with QEMU/KVM, run anywhere with the **[`qemux/qemu`](https://github.com/qemus/qemu)** Docker image_

---

## ✅ Features

| Feature                   | Included |
| ------------------------- | -------- |
| SSH-ready Ubuntu base VM  | ✅       |
| Docker Compose setup      | ✅       |
| Upload/download to HF Hub | ✅       |
| GUI Automation (X11)      | ✅       |
| Web VNC (noVNC)           | ✅       |

---

## 📂 Docker Directory Layout

```bash
📦docker
 ┣ 📂shared                  # Shared files between host and container
 ┣ 📂vms
 ┃ ┣ 📂snapshots
 ┃ ┣ 📂spider2-v             # OPTIONAL: use the Spider2-V image (download separately)
 ┃ ┃ ┣ 📜Ubuntu.qcow2
 ┃ ┃ ┗ 📜Ubuntu.qcow2.zip
 ┃ ┗ 📂ubuntu-base           # OPTIONAL: SSH-configured Ubuntu base image
 ┃ ┃ ┣ 📜boot.iso            # Required
 ┃ ┃ ┣ 📜data.img            # Required
 ┃ ┃ ┣ 📜qemu.mac            # Created at runtime
 ┃ ┃ ┣ 📜uefi.rom            # Created at runtime
 ┃ ┃ ┗ 📜uefi.vars           # Created at runtime
 ┣ 📜README.md
 ┣ 📜upload_base.py          # Upload VM base to HF dataset
 ┣ 📜download_base.py        # Download VM base from HF dataset
 ┗ 📜compose.qemu.yaml
```

---

## 🧠 Overview

This setup uses **QEMU/KVM** via the [`qemux/qemu`](https://github.com/qemus/qemu) Docker container to install, configure, and run Ubuntu or other operating systems.

```bash
docker compose up ──▶ downloader & installer ──▶ ubuntu-base.qcow2 ──▶ qemux/qemu
```

A reusable Ubuntu image can be uploaded/downloaded via the Hugging Face Hub.

---

## 1 · Host Prerequisites

| Role              | Packages & Setup                      | Example Command                                      |
| ----------------- | ------------------------------------- | ---------------------------------------------------- |
| Builder + Runtime | Docker + Docker Compose + KVM         | `sudo apt install docker.io docker-compose-plugin`   |
|                   | Add user to `docker` and `kvm` groups | `sudo usermod -aG docker,kvm $USER && newgrp docker` |

To verify KVM support:

```bash
kvm-ok  # or:
lsmod | grep kvm
```

✅ If your host is itself a VM, enable **nested virtualization** (VT-x/AMD-V).

📌 You do **not** need to install `qemu-utils` or `libguestfs-tools` on the host.

---

## 2 · Interactive Install via Docker Compose

First, create a `docker-compose.yml` file like this:

```yaml
# THIS FILE IS USED TO CREATE A NEW UBUNTU BASE IMAGE
services:
  ubuntu-base:
    image: qemux/qemu
    container_name: ubuntu-base

    volumes:
      - ${ROOT_DIR}/src/docker/vms/ubuntu-base:/storage
      - ${ROOT_DIR}/src/docker/shared:/shared

    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN

    environment:
      BOOT: "ubuntu"
      RAM_SIZE: "4G"
      CPU_CORES: "4"
      DISK_SIZE: "16G"
      DEBUG: "Y"

    ports:
      - 8006:8006 # Web console (noVNC)
      - 2222:22 # SSH access

    restart: unless-stopped
    stop_grace_period: 2m
```

Then launch:

```bash
docker compose -f compose.qemu.yaml up -d
```

1. Visit [http://localhost:8006](http://localhost:8006) (noVNC web UI).
2. Complete the Ubuntu installation onto `/storage/data.img`.
3. Shut down the VM after installation is done.

---

## 3 · First Boot into Your New VM

Re-run the Compose command:

```bash
docker compose -f compose.qemu.yaml up -d
```

This boots into your newly installed image instead of the installer ISO.

---

## 4 · Initial Guest Setup

### 4.1 · Install Guest Tools

Inside the guest:

```bash
sudo apt update && sudo apt dist-upgrade -y
sudo apt install -y qemu-guest-agent openssh-server curl wget git \
                    htop net-tools build-essential ca-certificates \
                    software-properties-common gnome-screenshot
```

---

### 4.2 · Enable SSH and Passwordless Sudo

1. Edit `/etc/ssh/sshd_config` and set:

   ```
   PasswordAuthentication yes
   ```

2. Restart SSH:

   ```bash
   sudo systemctl restart ssh
   sudo systemctl enable ssh
   ```

3. Allow passwordless sudo:

   ```bash
   sudo tee /etc/sudoers.d/user-nopasswd << 'EOF'
   user ALL=(ALL) NOPASSWD:ALL
   EOF
   sudo chmod 440 /etc/sudoers.d/user-nopasswd
   ```

---

## 5 · GUI Support for pyautogui

### 5.1 · Disable Wayland

Wayland blocks GUI automation (e.g., pyautogui).
Follow this guide to [disable Wayland and enable X11](https://askubuntu.com/questions/1343805/failed-to-enable-link-training-when-resuming-from-suspend/1470563#1470563).

---

### 5.2 · X11 Setup in Containers

To control the VM display from an app container:

```bash
xhost +SI:localuser:$(whoami)
```

Ensure the user has a valid `.Xauthority` file and is using X11.

---

## 6 · Upload/Download to Hugging Face

### 6.1 · Environment Setup

```bash
export HF_USERNAME="your-hf-username"
export HF_TOKEN="your-hf-token"
```

### 6.2 · Upload VM

```bash
python upload_base.py --path vms/ubuntu-base
```

### 6.3 · Download VM

```bash
python download_base.py --target vms/ubuntu-base
```

---

## 7 · Installed Packages Summary

| Category         | Packages                                                       |
| ---------------- | -------------------------------------------------------------- |
| GUI Tools        | `gnome-screenshot`                                             |
| Dev Essentials   | `build-essential`, `curl`, `wget`, `git`, `python3-dev`, `uv`  |
| Automation       | `pyautogui`, `pynput`, `Pillow`, `numpy`, `fastapi`, `uvicorn` |
| Screenshot Tools | `Pillow`, `pyautogui`, `Xcursor` (optional)                    |

---

## 8 · UV Installation

Inside the guest:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh

echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.profile

sudo bash -c 'echo "export PATH=\$HOME/.local/bin:\$PATH" >> /etc/profile'

source ~/.bashrc
source ~/.profile
```

✅ Ensures `uv` is available in SSH, FastAPI, and other environments.

---

# 🚀 Conclusion

This setup gives you a fully portable, Dockerized, reproducible VM system with:

- SSH & GUI support
- Agent sandboxing
- REST APIs for remote control
- Snapshot management
- Hugging Face Hub for cloud storage

Perfect for building and benchmarking AI agents in a controlled virtual environment.

# QEMU/KVM (Docker) Ubuntu Base Image

_Build once with QEMU/KVM, run anywhere with the **`qemux/qemu`** Docker image_

---

## 📂 Docker Directory Layout

```text
📆docker
 ┣ 📆shared                # Host ↔ guest 9p shared folders
 ┣ 📆vms
 ┃ ┣ 📆snapshots           # All clones / snapshots live here
 ┃ ┗ 📆ubuntu-base         # Golden image you install once
 ┃   ┗ 📋data.img          # **Only file you need to clone**
 ┣ 📋compose.qemu.yaml      # Sample compose file
 ┣ 📋upload_base.py         # Upload data.img to HF
 ┣ 📋download_base.py       # Download data.img from HF
 ┗ 📋README.md              # ← this file
```

> **Tip:** After you have installed the OS, **_only_ `data.img` matters**.
> `boot.iso` can be deleted; the container boots directly from the disk image.

---

## 1 · Overview

We use **QEMU/KVM** via the [`qemux/qemu`](https://github.com/qemus/qemu) Docker image to build and run Ubuntu (or any x86_64 distro).
Set `BOOT=ubuntu` on the first run and the container downloads the official installer ISO automatically.

---

## 2 · Host prerequisites

| Role                  | Required packages / actions                      |
| --------------------- | ------------------------------------------------ |
| **Builder & runtime** | Docker Engine, Docker Compose                    |
|                       | Add your user to the `docker` _and_ `kvm` groups |

_Verify hardware VT:_ `kvm-ok` _(Ubuntu)_ or `cat /proc/cpuinfo | grep -E "vmx|svm"`. If the host is itself a VM turn on **nested VT‑x/AMD‑V**.

The `qemux/qemu` image already ships QEMU & OVMF, so the host needs no extra QEMU packages.

---

## 3 · Interactive install (compose)

Create `compose.qemu.yaml` and point the storage volume at an **empty** folder.

```yaml
services:
  ubuntu-base:
    image: qemux/qemu
    container_name: ubuntu-base

    volumes:
      - ./docker/vms/ubuntu-base:/storage # virtual disks live here
      - ./docker/shared/ubuntu-base:/shared # optional 9p share

    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN

    environment:
      BOOT: "ubuntu" # only for the very first run
      RAM_SIZE: "4G"
      CPU_CORES: "4"
      DISK_SIZE: "25g"
      DEBUG: "Y"

    ports:
      - 8006:8006 # noVNC web console
      - 2222:22 # SSH host → guest

    restart: unless-stopped
    stop_grace_period: 2m
```

```bash
docker compose -f compose.qemu.yaml up -d
```

1. Open **[http://localhost:8006](http://localhost:8006)** → run the Ubuntu installer to `/storage/data.img`.
2. When done, **shut the VM down** from inside the guest.

Now `data.img` is your golden disk; keep it safe, copy it to `snapshots/` for new VMs.

---

## 4 · First‑boot provisioning

Enter the guest through the noVNC console **or** with the VS Code **Remote ‑ SSH** extension (see § 8).

```bash
sudo apt update && sudo apt dist-upgrade -y
sudo apt install -y \
  openssh-server curl wget git htop net-tools build-essential \
  software-properties-common ca-certificates gnome-screenshot
```

\### 4.1 · Passwordless sudo for the `user` account

Create a drop‑in file instead of editing `/etc/sudoers`:

```bash
# as root inside the VM
printf '%s ALL=(ALL) NOPASSWD:ALL\n' user > /etc/sudoers.d/sandbox.conf
chmod 440 /etc/sudoers.d/sandbox.conf
```

\### 4.2 · sshd minimal config

Put the snippet below into **`/etc/ssh/sshd_config.d/10-sandbox.conf`** and reload sshd.

```ssh
Port 22
PermitRootLogin yes       # optional, disable if you prefer
PasswordAuthentication yes
PubkeyAuthentication   no
AcceptEnv *                  # let clients pass any env var
PermitUserEnvironment yes    # honour ~/.ssh/environment
```

```bash
sudo sshd -t && sudo systemctl reload sshd
```

\### 4.3 · Enable sshd at boot

```bash
sudo systemctl enable ssh
```

---

## GUI control (pyautogui etc.)

- Disable Wayland → switch to Xorg (X11) on the login screen.
- Inside the VM: `xhost +SI:localuser:$(whoami)`.
- Pass `DISPLAY` and `XAUTHORITY` to any container that needs GUI automation.

---

## 6 · Installed packages recap

| Category       | Packages                                                      |
| -------------- | ------------------------------------------------------------- |
| Dev tools      | `build-essential`, `git`, `curl`, `wget`, `python3-dev`, `uv` |
| GUI helpers    | `gnome-screenshot`, `pyautogui`, `pynput`, `Pillow`           |
| Network / misc | `openssh-server`, `net-tools`, `htop`                         |

---

## 7 · `uv` install (Python pkg mgr)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 8 · Working with the VM from VS Code

1. **Install** the _Remote ‑ SSH_ extension.
2. Press `F1` → **Remote‑SSH: Connect to Host…** → `user@localhost:2222` (or whatever port you mapped).
3. The extension copies its server bits, then opens a new VS Code window that runs _inside_ your Ubuntu VM.
4. From there you can modify config files (`/etc/ssh/sshd_config.d/…`, `/etc/sudoers.d/sandbox.conf`), install software, or run terminals as if you were on a local machine.

---

## 🚀 Conclusion

You now have a **portable, reproducible Ubuntu VM** running under Docker/KVM, with:

- One‑file cloning (`data.img`)
- Password‑only SSH + passwordless sudo
- GUI automation via X11 & `pyautogui`
- Seamless development through VS Code Remote‑SSH

Happy hacking! 🎉

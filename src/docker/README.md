# QEMU/KVM (Docker) Ubuntu Base Image

_Build once with QEMU/KVM, run anywhere with the **`qemux/qemu`** Docker image_

---

## 1 · Overview

We use **QEMU/KVM** via the [`qemux/qemu`](https://github.com/qemus/qemu) Docker container to install, configure, and run Ubuntu.
Just point `BOOT` at an Ubuntu release alias (e.g. `ubuntu`) and the container will download the ISO for you.

```
docker compose up ──► downloader & installer ──► ubuntu-base.qcow2 ──► qemux/qemu
```

---

## 2 · Host prerequisites

| Host role             | Packages / tools                                                                                | Ubuntu example                                                                          |
| --------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Builder + runtime** | **Docker 24 + Docker Compose v2**,<br>`qemu-kvm`, `qemu-utils`, `libguestfs-tools` (`virt-v2v`) | `sudo apt install docker.io docker-compose-plugin qemu-kvm qemu-utils libguestfs-tools` |
|                       | Add user to `docker` _and_ `kvm` groups                                                         | `sudo usermod -aG docker,kvm $USER && newgrp docker`                                    |

Verify KVM with `kvm-ok` or `lsmod | grep kvm`. If your host is itself a VM, enable **nested VT-x/AMD-V**.

---

## 3 · Interactive install via Docker Compose

Create `docker-compose.yml` alongside an empty folder `vms/ubuntu-base`:

```yaml
services:
  ubuntu-base:
    image: qemux/qemu
    container_name: ubuntu-noble-base

    volumes:
      - ./vms/ubuntu-noble-base:/storage

    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN

    environment:
      BOOT: "ubuntu" # alias → auto-downloads Ubuntu live ISO
      RAM_SIZE: "4G"
      CPU_CORES: "4"
      DISK_SIZE: "16G"
      DEBUG: "Y"

    ports:
      - "8006:8006" # noVNC console
      - "2222:22" # SSH (optional)

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

## 4 · First boot into your new image

[Here](https://github.com/qemus/qemu?tab=readme-ov-file#faq-) you can find more information about the qemu image and VM configuration.

```bash
docker compose up -f <compose-file> -d
```

### 4.1 Install guest tools & SSH

Inside the guest:

```bash
sudo apt update && sudo apt dist-upgrade -y
sudo apt install -y qemu-guest-agent openssh-server curl wget git vim htop net-tools \
                    build-essential ca-certificates software-properties-common
```

### 4.2 Configure SSH & passwordless sudo

1. **Edit** `/etc/ssh/sshd_config`:

   ```conf
   Port 22
   ListenAddress 0.0.0.0
   UseDNS no

   PermitRootLogin no
   PasswordAuthentication yes
   ChallengeResponseAuthentication yes
   UsePAM yes
   AllowUsers user

   ClientAliveInterval 300
   ClientAliveCountMax 2

   Subsystem sftp internal-sftp
   AcceptEnv *
   PermitUserEnvironment yes
   AllowTcpForwarding local
   X11Forwarding no

   LogLevel VERBOSE
   ```

2. **Restart SSH**:

   ```bash
   sudo systemctl restart ssh
   sudo systemctl enable ssh
   ```

3. **Grant `user` passwordless sudo**:

   ```bash
   sudo tee /etc/sudoers.d/user-nopasswd << 'EOF'
   user ALL=(ALL) NOPASSWD:ALL
   EOF
   sudo chmod 440 /etc/sudoers.d/user-nopasswd
   ```

4. **Optional: verify** from host:

   ```bash
   ssh user@localhost -p 2222 'echo OK && sudo -n whoami'
   ```

---

## 5 · SSH & automation

You can test connectivity and root privileges with this Paramiko script:

```python
#!/usr/bin/env python3
import sys, argparse, paramiko

def run_ssh_command(ssh, command, timeout=10):
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode().strip(), stderr.read().decode().strip()

def connect_ssh(host, port, user, pwd, timeout=10):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=host, port=port, username=user, password=pwd, timeout=timeout)
    return c

def test_root(ssh):
    code, out, err = run_ssh_command(ssh, "sudo -n whoami")
    return code==0 and out=="root", code, out, err

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--host",default="localhost")
    p.add_argument("--port",type=int,default=2222)
    p.add_argument("--user",default="user")
    p.add_argument("--password",required=True)
    args=p.parse_args()

    try:
        ssh=connect_ssh(args.host,args.port,args.user,args.password)
    except Exception as e:
        print(f"Connection failed: {e}",file=sys.stderr); sys.exit(1)

    code,out,err = run_ssh_command(ssh,"echo OK")
    print(f"[Basic] {code=} {out=} {err=}")

    ok,code,out,err = test_root(ssh)
    print("[Root] OK" if ok else f"[Root] failed {code=} {out=} {err=}")

    ssh.close()
```

To push and run your server:

```bash
scp -r ./server user@localhost:2222:/home/user/server
ssh user@localhost -p 2222 \
  'sudo bash -lc "cd /home/user/server && chmod +x start.sh && ./start.sh"'
```

## 6 · Snapshot / thin‑clone

You have two options for spinning up branches of your golden image:

### 6.1 · CLI: create thin‑clones with `qemu-img`

Point `BASE` at your original ISO (or QCOW2 image), then run from inside the `vms/` directory:

```bash
# Note: backing-file paths (-b) are interpreted relative to the new image’s directory.
# To avoid errors, either use an absolute path or a relative path from snapshots/.

# Option A: use absolute path for backing file
BASE="$(pwd)/ubuntu-base/boot.iso"
qemu-img create -f qcow2 -F raw -b "$BASE" snapshots/ubuntu-base-snap1.qcow2
qemu-img create -f qcow2 -F raw -b "$BASE" snapshots/ubuntu-base-snap2.qcow2

# Option B: use relative path (backing path relative to snapshots/ directory)
qemu-img create -f qcow2 -F raw -b ../ubuntu-base/boot.iso snapshots/ubuntu-base-snap1.qcow2
qemu-img create -f qcow2 -F raw -b ../ubuntu-base/boot.iso snapshots/ubuntu-base-snap2.qcow2
```

Each new `.qcow2` file stores only the differences from `BASE`; the base stays pristine.

---

#### Booting a thin‑clone

From the same `vms/` directory, run:

```bash
qemu-system-x86_64 \
  -m 2048 \
  -drive file=snapshots/ubuntu-base-snap1.qcow2,if=virtio \
  -boot d
```

This boots your thin‑clone, capturing all writes in `snapshots/ubuntu-base-snap1.qcow2`.

### 6.2 · Python SDK: one‑shot snapshot + container launch

Automate both snapshot creation and container startup in one script using Docker’s Python SDK:

> **Note:** In the `ports` mapping, the **key** is the container (guest) port (e.g. `"8106/tcp"`), and the **value** is the host port to bind (e.g. `8106`).

```python
import shutil
import sys
from pathlib import Path

from docker import from_env
from docker.types import Mount


def copy_disk_images(base_boot: Path, base_data: Path, target_dir: Path):
    print(f"[*] Preparing VM in: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    boot_target = target_dir / "boot.iso"
    data_target = target_dir / "data.img"

    shutil.copy(base_boot, boot_target)
    shutil.copy(base_data, data_target)

    return boot_target, data_target


def validate_image(path: Path):
    if not path.exists():
        sys.exit(f"❌ Image not found: {path}")


def launch_container(name: str, boot_img: Path, data_img: Path, vnc: int, ssh: int, work_dir: Path):
    print(f"[*] Launching container: {name}")
    client = from_env()

    mounts = [
        Mount(target="/boot.iso", source=str(boot_img.resolve()), type="bind", read_only=False),
        Mount(target="/data.img", source=str(data_img.resolve()), type="bind", read_only=False),
        Mount(target="/storage", source=str(work_dir.resolve()), type="bind", read_only=False),
    ]

    container = client.containers.run(
        "qemux/qemu",
        name=name,
        devices=["/dev/kvm", "/dev/net/tun"],
        cap_add=["NET_ADMIN"],
        mounts=mounts,
        environment={
            "BOOT": "ubuntu",
            "RAM_SIZE": "4G",
            "CPU_CORES": "4",
            "DISK_SIZE": "16G",
            "DEBUG": "Y",
        },
        ports={
            "8006/tcp": vnc,
            "22/tcp": ssh,
        },
        detach=True,
    )

    print(f"[+] {name} started (id={container.short_id})")
    print(f"    ▸ VNC: localhost:{vnc}")
    print(f"    ▸ SSH: ssh user@localhost -p {ssh}")


def main():
    script_dir = Path(__file__).resolve().parent.parent
    base_dir = script_dir / "vms/ubuntu-base"
    base_boot = base_dir / "boot.iso"
    base_data = base_dir / "data.img"

    validate_image(base_boot)
    validate_image(base_data)

    snaps = ["snap1", "snap2"]
    for idx, snap_name in enumerate(snaps, start=1):
        snap_dir = script_dir / f"vms/snapshots/{snap_name}"
        boot_img, data_img = copy_disk_images(base_boot, base_data, snap_dir)

        vnc_port = 8000 + idx * 100 + 6
        ssh_port = 2222 + idx
        launch_container(f"ubuntu-{snap_name}", boot_img, data_img, vnc_port, ssh_port, snap_dir)


if __name__ == "__main__":
    main()

```

Running this script will:

- Create `ubuntu-base-snap1.qcow2` and `ubuntu-base-snap2.qcow2` in `vms/snapshots/`.
- Validate each snapshot file for existence and supported format before launch.
- Launch two containers named `ubuntu-snap1` and `ubuntu-snap2`.
- Expose noVNC on ports **8106** and **8206**, and SSH on **2223** and **2224** respectively.

---

#### 6.3 · Docker Compose variant

You can also define a `docker-compose.yml` for your snapshots:

```yaml
version: "3.8"
services:
  ubuntu-base-snap1:
    image: qemux/qemu
    container_name: ubuntu-base-snap1
    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN
    volumes:
      - ./vms/snapshots/ubuntu-base-snap1.qcow2:/boot.qcow2:ro
    environment:
      BOOT: "ubuntu"
      RAM_SIZE: "4G"
      CPU_CORES: "4"
      DISK_SIZE: "16G"
      DEBUG: "Y"
    ports:
      - "8106:8006" # Web console (noVNC)
      - "2223:22" # SSH
    restart: unless-stopped
    stop_grace_period: 2m

  ubuntu-base-snap2:
    image: qemux/qemu
    container_name: ubuntu-base-snap2
    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN
    volumes:
      - ./vms/snapshots/ubuntu-base-snap2.qcow2:/boot.qcow2:ro
    environment:
      BOOT: "ubuntu"
      RAM_SIZE: "4G"
      CPU_CORES: "4"
      DISK_SIZE: "16G"
      DEBUG: "Y"
    ports:
      - "8206:8006"
      - "2224:22"
    restart: unless-stopped
    stop_grace_period: 2m
```

## 7 · Ephemeral (RAM‑only) VMs

For throw‑away VMs that discard all changes on stop, mount the base read‑only and use a tmpfs scratch:

```yaml
services:
  ubuntu-temp:
    image: qemux/qemu
    container_name: ubuntu-temp
    volumes:
      - ./vms/ubuntu-base/ubuntu-base.qcow2:/storage/ubuntu-base.qcow2:ro
      - tmpfs:/storage/scratch
    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN
    environment:
      BOOT: "/storage/ubuntu-base.qcow2"
      RAM_SIZE: "2G"
      CPU_CORES: "2"
      DEBUG: "Y"
    ports:
      - "8306:8006"
      - "2225:22"
    restart: no

volumes:
  tmpfs:
    driver: local
    driver_opts:
      type: tmpfs
      device: tmpfs
      o: size=4G
```

All writes go into the in‑memory `scratch` volume and vanish when stopped.

---

## 8 · Quick reference

| Goal                      | How‑to                                                  |
| ------------------------- | ------------------------------------------------------- |
| New thin‑clone            | `qemu-img create -f qcow2 -b base.qcow2 snap.qcow2`     |
| Automated snapshots + run | use the Python script in 6.2                            |
| Multiple VMs via Compose  | define multiple services, each pointing at its `.qcow2` |
| Ephemeral (ram-only) VM   | mount base ro + tmpfs scratch volume                    |

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

## 6 · Snapshot / thin‑clone

You have two options for spinning up branches of your golden image:

### 6.1 · CLI: create thin‑clones with `qemu-img`

Point `BASE` at your original QCOW2, then run:

```bash
BASE=./vms/ubuntu-base/ubuntu-base.iso

# create two independent snapshots
qemu-img create -f qcow2 -b "$BASE" ./vms/ubuntu-base-snap1.qcow2
qemu-img create -f qcow2 -b "$BASE" ./vms/ubuntu-base-snap2.qcow2
```

Each new `.qcow2` file stores only the differences from `BASE`; the base stays pristine.

---

### 6.2 · Python SDK: one‑shot snapshot + container launch

Automate both steps—snapshot creation and `qemux/qemu` container startup—in a single script:

```python
#!/usr/bin/env python3
import os, subprocess, sys
from docker import from_env

def create_snapshot(base, snap):
    print(f"[*] Creating {snap}")
    subprocess.run(["qemu-img","create","-f","qcow2","-b",base,snap], check=True)

def launch(name, qcow, vnc, ssh):
    client = from_env()
    print(f"[*] Launching {name}")
    c = client.containers.run(
        "qemux/qemu",
        name=name,
        devices=["/dev/kvm","/dev/net/tun"],
        volumes={ qcow: {"bind": "/storage/ubuntu-base.qcow2","mode":"ro"}},
        environment={
          "RAM_SIZE": "4G",
          "CPU_CORES": "4",
          "DISK_SIZE":"16G",
          "DEBUG":"Y",
        },
        ports={f"{vnc}/tcp": vnc, f"{ssh}/tcp": ssh},
        detach=True
    )
    print(f"[+] {name} started (id={c.short_id}), VNC:{vnc}, SSH:{ssh}")

if __name__=="__main__":
    BASE = os.path.abspath("./vms/ubuntu-base/ubuntu-base.iso")
    if not os.path.exists(BASE):
        print("Base image missing", file=sys.stderr); sys.exit(1)

    snaps = ["snap1","snap2"]
    for i, nm in enumerate(snaps,1):
        path = f"./vms/ubuntu-base-{nm}.qcow2"
        create_snapshot(BASE, path)
        vnc_port = 8000 + i*100 + 6   # 8106, 8206
        ssh_port = 2222 + i           # 2223, 2224
        launch(f"ubuntu-{nm}", path, vnc_port, ssh_port)
```

Running this script will:

- generate `ubuntu-base-snap1.qcow2` and `ubuntu-base-snap2.qcow2`
- launch `ubuntu-snap1` on **8106** (noVNC) & **2223** (SSH)
- launch `ubuntu-snap2` on **8206** (noVNC) & **2224** (SSH)

---

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

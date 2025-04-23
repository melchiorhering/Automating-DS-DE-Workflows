from __future__ import annotations

import logging
import os
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Union

import docker
from docker.client import DockerClient
from docker.types import Mount
from sandbox.errors import RemoteCommandError, VMCreationError, VMOperationError
from sandbox.ssh import SSHClient, SSHConfig


# ────────────────────────────── State Enum ──────────────────────────────
class VMState(Enum):
    INITIALIZING = "initializing"
    CREATING = "creating"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


# ────────────────────────────── Decorator ──────────────────────────────
def with_state(state: VMState):
    def decorator(fn: Callable):
        def wrapper(self, *args, **kwargs):
            self._set_state(state)
            return fn(self, *args, **kwargs)

        return wrapper

    return decorator


# ────────────────────────────── Configs ──────────────────────────────
@dataclass
class VMConfig:
    root_dir: Path = Path("docker")
    container_image: str = "qemux/qemu"
    container_name: str = "qemu"
    unique_container_name: bool = False
    vm_ram: str = "4G"
    vm_cpu_cores: int = 4
    vm_disk_size: str = "16G"
    vm_boot_image: str = "ubuntu"
    enable_debug: bool = True
    extra_env: Dict[str, str] = field(default_factory=dict)
    vnc_port: int = 8006
    ssh_port: int = 2222
    extra_ports: Dict[Union[str, int], int] = field(default_factory=dict)
    guest_shared_dir: Path = Path("/shared")
    restart_policy: str = "always"

    def __post_init__(self):
        if self.unique_container_name:
            self.container_name = f"{self.container_name}_{int(time.time())}"

        self.root_dir = self.root_dir.resolve()
        self.shared_root = self.root_dir / "shared"
        # VM paths
        self.vms_dir = self.root_dir / "vms"
        self.vm_base_dir = self.vms_dir / "ubuntu-base"
        self.snapshots_dir = self.vms_dir / "snapshots"
        self.base_iso = self.vm_base_dir / "boot.iso"
        self.base_data = self.vm_base_dir / "data.img"
        # Container paths
        self.container_dir = self.snapshots_dir / self.container_name
        self.container_iso = self.container_dir / "boot.iso"
        self.container_data = self.container_dir / "data.img"
        self.container_shared_dir = self.shared_root / self.container_name

        for p in (
            self.vm_base_dir,
            self.snapshots_dir,
            self.shared_root,
            self.container_dir,
            self.container_shared_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

        if not self.base_iso.exists() or not self.base_data.exists():
            raise VMCreationError("Missing base VM files")


@dataclass
class AgentVMConfig(VMConfig):
    # Host server settings
    host_sandbox_server_port: int = 8765
    host_sandbox_server_host: str = "localhost"
    host_server_dir: Optional[Path] = Path("server/")

    # Sandbox server settings
    sandbox_server_port: int = 8765
    sandbox_server_host: str = "0.0.0.0"
    sandbox_server_dir: Path = Path("/home/user/server")

    runtime_env: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()
        self.extra_ports[self.sandbox_server_port] = self.host_sandbox_server_port

        if self.host_server_dir:
            self.host_server_dir = self.host_server_dir.resolve()
            if not self.host_server_dir.is_dir():
                raise VMCreationError(f"server_host_dir not found: {self.host_server_dir}")
            start_sh = self.host_server_dir / "start.sh"
            if not (start_sh.is_file() and os.access(start_sh, os.X_OK)):
                raise VMCreationError(f"start.sh missing or not executable: {start_sh}")


# ────────────────────────────── VMManager ──────────────────────────────
class VMManager:
    def __init__(
        self,
        config: VMConfig,
        docker_client: DockerClient = None,
        logger: logging.Logger = None,
        ssh_cfg: SSHConfig = None,
    ):
        self.cfg = config
        self.log = logger or logging.Logger
        self.docker = docker_client or docker.from_env()
        self.ssh = SSHClient(ssh_cfg or SSHConfig(port=self.cfg.ssh_port), self.log)
        self.container = None
        self.state = VMState.INITIALIZING
        self.on_state_change = None

        self._validate_config()
        self._attach_to_existing_container_if_running()

    def _set_state(self, new: VMState):
        old = self.state
        self.state = new
        self.log.info("VM state: %s -> %s", old.value, new.value)
        if self.on_state_change:
            try:
                self.on_state_change(new)
            except Exception as e:
                self.log.error("State callback error: %s", e)

    def _validate_config(self):
        if not self.cfg.base_iso.exists() or not self.cfg.base_data.exists():
            raise VMCreationError("Base ISO or data.img not found")
        for port in (self.cfg.vnc_port, self.cfg.ssh_port, *self.cfg.extra_ports.values()):
            if not (1 <= port <= 65535):
                raise VMCreationError(f"Invalid port: {port}")

    def _attach_to_existing_container_if_running(self):
        self.log.info("Checking for existing container: %s", self.cfg.container_name)
        try:
            container = self.docker.containers.get(self.cfg.container_name)
            if container.status == "running":
                self.log.info("Reusing running container: %s", self.cfg.container_name)
                self.container = container
                self._set_state(VMState.RUNNING)
        except docker.errors.NotFound:
            pass

    def copy_vm_base_files(self):
        self.log.info("Copying VM base files to container directory")
        self.cfg.container_dir.mkdir(parents=True, exist_ok=True)

        self.log.info("Copying base ISO: `%s` -> `%s`", self.cfg.base_iso, self.cfg.container_iso)
        shutil.copy(self.cfg.base_iso, self.cfg.container_iso)

        self.log.info("Copying base data: `%s` -> `%s`", self.cfg.base_data, self.cfg.container_data)
        shutil.copy(self.cfg.base_data, self.cfg.container_data)

    @with_state(VMState.CREATING)
    def create_container(self):
        self._ensure_image()
        self.copy_vm_base_files()

        self.log.info("Creating container: %s", self.cfg.container_name)
        mounts = [
            Mount(target="/boot.iso", source=str(self.cfg.container_iso), type="bind"),
            Mount(target="/storage", source=str(self.cfg.container_dir), type="bind"),
            Mount(target=str(self.cfg.guest_shared_dir), source=str(self.cfg.container_shared_dir), type="bind"),
        ]
        ports = {
            "8006/tcp": self.cfg.vnc_port,
            "22/tcp": self.cfg.ssh_port,
        }

        # Add extra ports without overwriting defaults
        for key, val in self.cfg.extra_ports.items():
            if key in ports:
                self.log.warning(
                    "⚠️ Port %s already defined (default: %s), skipping extra override (%s).", key, ports[key], val
                )
            else:
                ports[key] = val

        env = {
            "BOOT": self.cfg.vm_boot_image,
            "RAM_SIZE": self.cfg.vm_ram,
            "CPU_CORES": str(self.cfg.vm_cpu_cores),
            "DISK_SIZE": self.cfg.vm_disk_size,
            "DEBUG": "Y" if self.cfg.enable_debug else "N",
            **self.cfg.extra_env,
        }

        self.container = self.docker.containers.run(
            image=self.cfg.container_image,
            name=self.cfg.container_name,
            environment=env,
            mounts=mounts,
            ports=ports,
            devices=["/dev/kvm", "/dev/net/tun"],
            cap_add=["NET_ADMIN"],
            detach=True,
            restart_policy={"Name": self.cfg.restart_policy},
        )
        self._set_state(VMState.RUNNING)

    def _ensure_image(self):
        self.log.info("Checking for image: %s", self.cfg.container_image)
        try:
            self.docker.images.get(self.cfg.container_image)
        except docker.errors.ImageNotFound:
            self.log.info("Pulling image: %s", self.cfg.container_image)
            self.docker.images.pull(self.cfg.container_image)

    def cleanup(self, delete_storage=True):
        self.log.info("Cleaning up VM resources")
        if self.container:
            self.container.remove(force=True)
            self.container = None
        if delete_storage:
            self.log.info("Removing container directory: %s", self.cfg.container_dir)
            shutil.rmtree(self.cfg.container_dir, ignore_errors=True)
        self._set_state(VMState.STOPPED)

    def ssh_shell(self):
        if self.state != VMState.RUNNING:
            raise VMOperationError("VM is not running")
        self.ssh.open_shell()


# ───────────────────────────── AgentVMManager ─────────────────────────────
class AgentVMManager(VMManager):
    def __init__(self, config: AgentVMConfig, **kwargs):
        if not isinstance(config, AgentVMConfig):
            raise TypeError("AgentVMManager requires AgentVMConfig")
        super().__init__(config, **kwargs)
        self._should_cleanup = True  # ensure cleanup if something fails early

    def __enter__(self) -> AgentVMManager:
        try:
            self._start_agent_vm()
            self._should_cleanup = False  # startup succeeded, let __exit__ decide
            return self
        except Exception as e:
            self.log.error("❌ Exception during VM startup: %s", e, exc_info=True)
            self.cleanup(delete_storage=True)
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._should_cleanup or exc_type is not None:
            self.log.warning("⚠️ Exiting VM context with error, cleaning up...")
        else:
            self.log.info("🧹 Normal exit, cleaning up VM container...")
        self.cleanup(delete_storage=True)
        return False  # do not suppress exceptions

    def _ensure_mounted(self, mount_point: str, tag: str):
        self.log.info("Mounting %s -> %s", tag, mount_point)

        # Ensure directory exists
        self.ssh.exec_command(f"echo '{self.ssh.cfg.password}' | sudo -S mkdir -p {mount_point}")

        mount_cmd = f"echo '{self.ssh.cfg.password}' | sudo -S mount -t 9p -o trans=virtio {tag} {mount_point}"
        try:
            self.ssh.exec_command(mount_cmd)
        except RemoteCommandError as e:
            # Check if already mounted
            if "already mounted" in e.stderr:
                self.log.warning("⚠️ Shared directory already mounted, continuing...")
            else:
                raise

    def _start_agent_vm(self):
        if not self.container or self.container.status != "running":
            self.create_container()
            time.sleep(5)  # Short wait to allow the container to stabilize

        self.ssh = SSHClient(SSHConfig(port=self.cfg.ssh_port), logger=self.log)
        mount = f"/mnt/{self.cfg.container_name}"
        self._ensure_mounted(mount, self.cfg.guest_shared_dir.name)

        self.cfg.runtime_env["SHARED_DIR"] = mount
        self.cfg.runtime_env["PORT"] = str(self.cfg.sandbox_server_port)

        if self.cfg.host_server_dir:
            self.ssh.transfer_directory(self.cfg.host_server_dir, str(self.cfg.sandbox_server_dir))
            self.ssh.exec_command(f"chmod +x {self.cfg.sandbox_server_dir}/start.sh")
            self.ssh.stream_command("./start.sh", cwd=str(self.cfg.sandbox_server_dir), env=self.cfg.runtime_env)

        self._wait_for_fastapi_server()

    def _wait_for_fastapi_server(self, timeout: float = 30.0, interval: float = 1.0):
        """
        Wait until the FastAPI server's /health endpoint is reachable.
        """
        host = self.cfg.host_sandbox_server_host
        port = self.cfg.host_sandbox_server_port
        url = f"http://{host}:{port}/health"

        self.log.info("🔍 Waiting for FastAPI server health check at %s...", url)
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        self.log.info(response.read().decode("utf-8"))
                        self.log.info("✅ FastAPI server is healthy at %s", url)
                        return
            except urllib.error.URLError as e:
                self.log.debug("⏳ Server not ready: %s", e)
            except Exception as e:
                self.log.debug("⏳ Unexpected error: %s", e)

            time.sleep(interval)

        raise VMOperationError(f"❌ FastAPI server did not become reachable at {url} within {timeout:.1f} seconds.")

    def tail_server_logs(self, lines: int = 100) -> str:
        """
        Tail the local sandbox-server.log file mounted from the container.
        """
        path = self.cfg.container_shared_dir / "sandbox-server.log"
        if not path.exists():
            raise VMOperationError(f"❌ Log file not found at {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                return "".join(f.readlines()[-lines:])
        except Exception as e:
            raise VMOperationError(f"❌ Failed to read log file: {e}") from e

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from enum import Enum

from smolagents import AgentLogger, LogLevel

import docker
from docker.client import DockerClient
from docker.types import Mount

from .configs import VMConfig
from .errors import VMCreationError, VMOperationError
from .ssh import SSHClient, SSHConfig


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


# ────────────────────────────── VMManager ──────────────────────────────
class VMManager:
    def __init__(
        self,
        config: VMConfig,
        docker_client: DockerClient = None,
        logger: AgentLogger = None,
        ssh_cfg: SSHConfig = None,
    ):
        self.cfg = config
        self.logger = logger or AgentLogger(level=LogLevel.INFO)
        self.docker = docker_client or docker.from_env()
        self.ssh = SSHClient(ssh_cfg or SSHConfig(port=self.cfg.host_ssh_port), logger=self.logger)
        self.container = None
        self.state = VMState.INITIALIZING
        self.on_state_change = None

        self._validate_config()
        self._attach_to_existing_container_if_running()

    def _set_state(self, new: VMState):
        old = self.state
        self.state = new
        self.logger.log(f"VM state: {old.value} -> {new.value}", level=LogLevel.INFO)
        if self.on_state_change:
            try:
                self.on_state_change(new)
            except Exception as e:
                self.logger.log_error(f"State callback error: {e}")

    def _validate_config(self):
        if not self.cfg.base_iso.exists() or not self.cfg.base_data.exists():
            raise VMCreationError("Base ISO or data.img not found")
        for port in (self.cfg.host_vnc_port, self.cfg.host_ssh_port, *self.cfg.extra_ports.values()):
            if not (1 <= port <= 65535):
                raise VMCreationError(f"Invalid port: {port}")

    def _attach_to_existing_container_if_running(self):
        self.logger.log(f"Checking for existing container: {self.cfg.container_name}", level=LogLevel.INFO)
        try:
            container = self.docker.containers.get(self.cfg.container_name)
            if container.status == "running":
                self.logger.log(f"Reusing running container: {self.cfg.container_name}", level=LogLevel.INFO)
                self.container = container
                self._set_state(VMState.RUNNING)
        except docker.errors.NotFound:
            pass

    def _wait_for_ssh_ready(self, timeout: float = 120, interval: float = 5.0):
        host = self.ssh.cfg.hostname
        port = self.ssh.cfg.port
        self.logger.log(f"🔍 Waiting for SSH server to respond on {host}:{port}...", level=LogLevel.INFO)
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                result = self.ssh.exec_command("echo ready")
                if result["stdout"].strip() == "ready":
                    self.logger.log("✅ SSH server is ready", level=LogLevel.INFO)
                    return
            except Exception as e:
                self.logger.log(f"⏳ SSH command failed: {e}", level=LogLevel.DEBUG)

            time.sleep(interval)

        raise TimeoutError(f"❌ SSH server not ready after {timeout} seconds")

    def copy_vm_base_files(self):
        self.logger.log("Copying VM base files to container directory", level=LogLevel.INFO)
        self.cfg.host_container_dir.mkdir(parents=True, exist_ok=True)

        self.logger.log(
            f"Copying base ISO: `{self.cfg.base_iso}` -> `{self.cfg.host_container_iso}`", level=LogLevel.INFO
        )
        shutil.copy(self.cfg.base_iso, self.cfg.host_container_iso)

        self.logger.log(
            f"Copying base data: `{self.cfg.base_data}` -> `{self.cfg.host_container_data}`", level=LogLevel.INFO
        )
        shutil.copy(self.cfg.base_data, self.cfg.host_container_data)

    @with_state(VMState.CREATING)
    def create_container(self):
        self._ensure_image()
        self.copy_vm_base_files()

        self.logger.log(f"Creating container: {self.cfg.container_name}", level=LogLevel.INFO)
        mounts = [
            Mount(target="/boot.iso", source=str(self.cfg.host_container_iso), type="bind"),
            Mount(target="/storage", source=str(self.cfg.host_container_dir), type="bind"),
            Mount(target=str(self.cfg.guest_shared_dir), source=str(self.cfg.host_container_shared_dir), type="bind"),
        ]
        ports = {
            "8006/tcp": self.cfg.host_vnc_port,
            "22/tcp": self.cfg.host_ssh_port,
        }

        for key, val in self.cfg.extra_ports.items():
            if key in ports:
                self.logger.log(
                    f"⚠️ - Port {key} already defined (default: {ports[key]}), skipping extra override ({val}).",
                    level=LogLevel.WARNING,
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
        self.logger.log(f"Checking for image: {self.cfg.container_image}", level=LogLevel.INFO)
        try:
            self.docker.images.get(self.cfg.container_image)
        except docker.errors.ImageNotFound:
            self.logger.log(f"Pulling image: {self.cfg.container_image}", level=LogLevel.INFO)
            self.docker.images.pull(self.cfg.container_image)

    @with_state(VMState.STOPPED)
    def cleanup(self, delete_storage=True):
        self.logger.log("Cleaning up VM resources", level=LogLevel.INFO)
        if self.container:
            self.container.remove(force=True)
            self.container = None
        if delete_storage:
            self.logger.log(f"Removing container directory: {self.cfg.host_container_dir}", level=LogLevel.INFO)
            shutil.rmtree(self.cfg.host_container_dir, ignore_errors=True)

    def ssh_shell(self):
        if self.state != VMState.RUNNING:
            raise VMOperationError("VM is not running")
        self.ssh.open_shell()

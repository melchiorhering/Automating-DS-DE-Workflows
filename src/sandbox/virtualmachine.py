from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from enum import Enum

import docker
from docker.client import DockerClient
from docker.types import Mount

from .configs import VMConfig
from .errors import VMCreationError, VMOperationError
from .ssh import SSHClient, SSHConfig

# ────────────────────────────── Logging Setup ──────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
        logger: logging.Logger = logger,
        ssh_cfg: SSHConfig = None,
    ):
        self.cfg = config
        self.log = logger
        self.docker = docker_client or docker.from_env()
        self.ssh = SSHClient(ssh_cfg or SSHConfig(port=self.cfg.host_ssh_port), self.log)
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
        for port in (self.cfg.host_vnc_port, self.cfg.host_ssh_port, *self.cfg.extra_ports.values()):
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

    def _wait_for_ssh_ready(self, timeout: float = 120, interval: float = 5.0):
        host = self.ssh.cfg.hostname
        port = self.ssh.cfg.port
        self.log.info("🔍 Waiting for SSH server to respond on %s:%d...", host, port)
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                # Use exec_command to run a simple test command
                result = self.ssh.exec_command("echo ready")
                if result["stdout"].strip() == "ready":
                    self.log.info("✅ SSH server is ready")
                    return
            except Exception as e:
                self.log.debug("⏳ SSH command failed: %s", e)

            time.sleep(interval)

        raise TimeoutError(f"❌ SSH server not ready after {timeout} seconds")

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
            "8006/tcp": self.cfg.host_vnc_port,
            "22/tcp": self.cfg.host_ssh_port,
        }

        # Add extra ports without overwriting defaults
        for key, val in self.cfg.extra_ports.items():
            if key in ports:
                self.log.warning(
                    "⚠️ - Port %s already defined (default: %s), skipping extra override (%s).", key, ports[key], val
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

    @with_state(VMState.STOPPED)
    def cleanup(self, delete_storage=True):
        self.log.info("Cleaning up VM resources")
        if self.container:
            self.container.remove(force=True)
            self.container = None
        if delete_storage:
            self.log.info("Removing container directory: %s", self.cfg.container_dir)
            shutil.rmtree(self.cfg.container_dir, ignore_errors=True)

    def ssh_shell(self):
        if self.state != VMState.RUNNING:
            raise VMOperationError("VM is not running")
        self.ssh.open_shell()

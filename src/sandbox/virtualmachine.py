from __future__ import annotations

import shutil
import time

from gguf import Union
from smolagents import AgentLogger, LogLevel

import docker
from docker.client import DockerClient
from docker.types import Mount

from .configs import SandboxVMConfig, VMConfig
from .errors import VMCreationError, VMOperationError
from .ssh import SSHClient, SSHConfig


# ────────────────────────────── VMManager ──────────────────────────────
class VMManager:
    def __init__(
        self,
        config: Union[VMConfig, SandboxVMConfig],
        docker_client: DockerClient = None,
        logger: AgentLogger = None,
        ssh_cfg: SSHConfig = None,
    ):
        self.cfg = config
        self.logger = logger or AgentLogger(level=LogLevel.INFO)
        self.docker = docker_client or docker.from_env()
        self.ssh = SSHClient(ssh_cfg or SSHConfig(port=self.cfg.host_ssh_port), logger=self.logger)
        self.container = None
        self.on_state_change = None

        self._validate_config()
        self._attach_to_existing_container_if_running()

    def _validate_config(self):
        if not self.cfg.base_iso.exists() or not self.cfg.base_data.exists():
            raise VMCreationError("Base ISO or data.img not found")
        for port in (self.cfg.host_vnc_port, self.cfg.host_ssh_port, *self.cfg.extra_ports.values()):
            if not (1 <= port <= 65535):
                raise VMCreationError(f"Invalid port: {port}")

    def _attach_to_existing_container_if_running(self):
        self.logger.log_rule("🧱 Docker Container Check")
        try:
            container = self.docker.containers.get(self.cfg.container_name)
            is_running = container.status == "running"
            self.logger.log(
                f"{'🔄 Reusing running container' if is_running else '🛑 Found existing but stopped container'}: {self.cfg.container_name}",
                level=LogLevel.INFO,
            )
            if is_running:
                self.container = container
        except docker.errors.NotFound:
            self.logger.log(f"🚫 No existing container found: {self.cfg.container_name}", level=LogLevel.DEBUG)
        except Exception as e:
            self.logger.log_error(f"❌ Unexpected error while checking container: {e}")

    def _wait_for_ssh_ready(self, timeout: float = 120, interval: float = 5.0):
        self.logger.log_rule("🔐 SSH Initialization")
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
        self.logger.log(
            f"[bold]📁 Creating container directory:[/bold] {self.cfg.host_container_dir}", level=LogLevel.INFO
        )
        self.cfg.host_container_dir.mkdir(parents=True, exist_ok=True)

        self.logger.log("[bold]📤 Copying VM boot and data images:[/bold]", level=LogLevel.INFO)

        iso_msg = f"[green]📀 boot.iso[/green]  →  {self.cfg.host_container_iso}"
        self.logger.log(iso_msg, level=LogLevel.INFO)
        shutil.copy(self.cfg.base_iso, self.cfg.host_container_iso)

        data_msg = f"[green]💾 data.img[/green]  →  {self.cfg.host_container_data}"
        self.logger.log(data_msg, level=LogLevel.INFO)
        shutil.copy(self.cfg.base_data, self.cfg.host_container_data)

        self.logger.log("\n✅ [bold green]VM base files prepared successfully[/bold green]\n", level=LogLevel.INFO)

    def create_container(self):
        self.logger.log("📦 Creating VM Container", level=LogLevel.INFO)
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
        self.logger.log("✅ Container is now running.", level=LogLevel.INFO)

    def _ensure_image(self):
        self.logger.log(f"❓ Checking for image: {self.cfg.container_image}", level=LogLevel.INFO)
        try:
            self.docker.images.get(self.cfg.container_image)
        except docker.errors.ImageNotFound:
            self.logger.log(f"Pulling image: {self.cfg.container_image}", level=LogLevel.INFO)
            self.docker.images.pull(self.cfg.container_image)

    def cleanup(self, delete_storage=True):
        if self.container:
            self.container.stop()
            self.container.remove(force=True)
            self.logger.log(f"Container {self.cfg.container_name} stopped and removed.", level=LogLevel.INFO)
            self.container = None
        if delete_storage:
            self.logger.log(f"Removing container directory: {self.cfg.host_container_dir}", level=LogLevel.INFO)
            shutil.rmtree(self.cfg.host_container_dir, ignore_errors=True)

    def ssh_shell(self):
        if self.container is None or self.container.status != "running":
            raise VMOperationError("VM is not running")
        self.ssh.open_shell()

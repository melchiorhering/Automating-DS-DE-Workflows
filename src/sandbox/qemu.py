import json
import logging
import os
import select
import shutil
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import paramiko

import docker
from docker.models.containers import Container
from docker.types import Mount

logging.getLogger("paramiko.transport").setLevel(logging.WARNING)


# ---------------------------
# Logger Helper
# ---------------------------
def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ---------------------------
# Custom Exceptions
# ---------------------------
class VMManagerError(Exception):
    pass


class VMCreationError(VMManagerError):
    pass


class VMOperationError(VMManagerError):
    pass


class SSHError(VMManagerError):
    pass


# ---------------------------
# VM State
# ---------------------------
class VMState(Enum):
    """Enum for VM states."""

    INITIALIZING = "initializing"
    CREATING = "creating"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# ---------------------------
# Config Data Classes
# ---------------------------
@dataclass
class VMConfig:
    # ——— Container & VM image ———
    container_image: str = "qemux/qemu"
    container_name: str = "qemu"
    unique_container_name: bool = False

    # ——— VM sizing & debug ———
    vm_boot_image: str = "ubuntu"
    vm_ram: str = "4G"
    vm_cpu_cores: int = 4
    vm_disk_size: str = "16G"
    enable_debug: bool = True
    extra_env: Dict[str, str] = field(default_factory=dict)

    # ——— Networking ———
    vnc_port: int = 8006
    ssh_port: int = 2222
    extra_ports: Dict[str, int] = field(default_factory=dict)  # e.g. {"5432/tcp": 5432}

    # ——— Capabilities & devices ———
    devices: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    restart_policy: str = "always"

    # ——— Directories & mounts ———
    vm_base_dir: Optional[Path] = None  # where to clone the base VM files
    instances_dir: Path = Path("docker/environments")  # where per-instance dirs go
    host_shared_dir: Optional[Path] = None  # base host path for 9p shares
    guest_shared_dir: Path = Path("/shared")  # where the share is mounted in the VM

    # ——— In‑VM runtime env ———
    runtime_env: Dict[str, str] = field(default_factory=dict)

    # ——— Agent‑server deployment ———
    server_host_dir: Optional[Path] = None  # local path with start.sh
    server_guest_dir: Path = Path("/home/user/server")  # target in the VM

    def __post_init__(self):
        self.instances_dir = Path(self.instances_dir).resolve()

        if self.unique_container_name:
            self.container_name = self.unique_name(self.container_name)
        if self.vm_base_dir:
            self.vm_base_dir = Path(self.vm_base_dir).resolve()
        if self.host_shared_dir:
            self.host_shared_dir = Path(self.host_shared_dir).resolve()
        if self.server_host_dir:
            self.server_host_dir = Path(self.server_host_dir).resolve()

    def unique_name(self, base: str) -> str:
        suffix = str(int(time.time()))
        return f"{base}_{suffix}"

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "VMConfig":
        valid = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in config_dict.items() if k in valid}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_file: Union[str, Path]) -> "VMConfig":
        with open(json_file, "r") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def to_json(self, json_file: Union[str, Path]) -> None:
        with open(json_file, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


@dataclass
class SSHConfig:
    hostname: str = "localhost"
    port: int = 2222
    username: str = "user"
    password: str = "password"
    key_filename: Optional[str] = None
    connect_timeout: int = 30
    max_retries: int = 5
    retry_delay: int = 5
    command_timeout: int = 60
    initial_delay: int = 15  # seconds to wait before first SSH attempt

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "SSHConfig":
        valid = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in config_dict.items() if k in valid}
        return cls(**filtered)


# ---------------------------
# Utility Functions
# ---------------------------
def set_writable_dir(path: Union[str, Path], mode: int = 0o777) -> None:
    """Ensure directory exists and is writable by everyone."""
    p = Path(path)
    p.mkdir(exist_ok=True, parents=True)
    os.chmod(str(p), mode)


# ---------------------------
# SSHClient Class
# ---------------------------
class SSHClient:
    def __init__(self, logger: Optional[logging.Logger] = None, config: Optional[SSHConfig] = None) -> None:
        self.logger = logger or setup_logger("SSHClient")
        self.config = config or SSHConfig()
        self.client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._last_used: float = 0
        self._connection_timeout: int = 300  # 5 minutes connection timeout

    def __enter__(self) -> "SSHClient":
        # ensure we have a live connection
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # always clean up
        self.close()

    def _is_connection_active(self) -> bool:
        """Check if the connection is still active and not timed out."""
        if not self.client:
            return False

        try:
            # Check if the connection is active
            tr = self.client.get_transport()
            if not (tr and tr.is_active()):
                return False

            # Check if the connection has timed out
            if time.time() - self._last_used > self._connection_timeout:
                self.logger.info("Connection timed out, will reconnect")
                return False

            # Send keep-alive packet to prevent server timeouts
            tr.send_ignore()
            return True
        except Exception as e:
            self.logger.debug(f"Connection check failed: {e}")
            return False

    def open_shell(self, term: str = "xterm") -> paramiko.Channel:
        chan = self.connect().invoke_shell(term=term)
        chan.settimeout(0.0)

        # pump local → remote
        def _writeall(src, dst):
            while not dst.closed:
                data = src.read(1)
                if not data:
                    break
                dst.send(data)

        threading.Thread(target=_writeall, args=(sys.stdin, chan), daemon=True).start()

        # pump remote → local
        try:
            while True:
                if chan.recv_ready():
                    sys.stdout.write(chan.recv(1024).decode())
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            chan.close()
        return chan

    def connect(self, timeout: Optional[int] = None) -> paramiko.SSHClient:
        """Connect only if needed, reusing existing connections when possible."""
        # If connection exists and is active, reuse it
        if self._is_connection_active():
            self._last_used = time.time()
            return self.client

        # Otherwise, close any existing connection and create a new one
        self.close()

        # First connection attempt gets initial delay
        if self.client is None:
            time.sleep(self.config.initial_delay)

        timeout = timeout or self.config.connect_timeout
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        for attempt in range(1, self.config.max_retries + 1):
            try:
                self.logger.info("SSH connect attempt %d/%d", attempt, self.config.max_retries)
                self.client.connect(
                    hostname=self.config.hostname,
                    port=self.config.port,
                    username=self.config.username,
                    password=self.config.password,
                    key_filename=self.config.key_filename,
                    timeout=timeout,
                )
                self.logger.info("SSH connection established")
                self._last_used = time.time()
                return self.client
            except paramiko.SSHException:
                # only log a short message—no traceback & no exc_info
                self.logger.warning(
                    "SSH connect attempt %d/%d failed; will retry in %ds",
                    attempt,
                    self.config.max_retries,
                    min(2**attempt, 30),
                )
                if attempt == self.config.max_retries:
                    # raise a new, clean exception with no stack‐trace of the inner Paramiko bits
                    raise SSHError(f"SSH connect failed after {self.config.max_retries} attempts")
                time.sleep(min(2**attempt, 30))

        raise SSHError("SSH connect attempts exhausted")

    def exec_command(
        self,
        command: str,
        directory: str = "",
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        timeout = timeout or self.config.command_timeout

        # Modify command if directory is specified
        full_cmd = command
        if directory:
            full_cmd = f"cd {directory} && {command}"

        self.logger.info("Executing SSH command: %s", full_cmd)
        client = self.connect(timeout=timeout)
        self._last_used = time.time()

        # Log environment variables being passed (useful for debugging)
        if env:
            self.logger.debug(f"Passing environment variables: {env}")

        # Use Paramiko's built-in environment parameter
        stdin, stdout, stderr = client.exec_command(
            full_cmd,
            timeout=timeout,
            environment=env,  # This is the key change
        )

        status = stdout.channel.recv_exit_status()
        out = stdout.read().decode()
        err = stderr.read().decode()
        if status != 0:
            self.logger.error("SSH cmd failed (%d): %s", status, err.strip())
        return {"status": status, "stdout": out, "stderr": err}

    def exec_commands(
        self, commands: List[str], directory: str = "", env: Optional[Dict[str, str]] = None, stream: bool = False
    ) -> List[Union[Dict[str, Any], int]]:
        """
        Run multiple commands over SSH.
        If stream=True, yield List[int] or List[Dict].
        """
        results = []
        for cmd in commands:
            if stream:
                code = self.exec_command_stream(cmd, directory=directory, env=env)
                results.append(code)
                if code != 0:
                    break
            else:
                res = self.exec_command(cmd, directory=directory, env=env)
                results.append(res)
                if res["status"] != 0:
                    break
        return results

    def exec_command_stream(
        self,
        command: str,
        directory: str = "",
        env: Optional[Dict[str, str]] = None,
        tail_lines: Optional[int] = 50,
    ) -> int:
        """Execute a long‐running command, streaming stdout/stderr live,
        but only keep the last `tail_lines`."""
        client = self.connect()
        full_cmd = f"cd {directory} && {command}" if directory else command

        stdin, stdout, stderr = client.exec_command(
            full_cmd,
            timeout=None,
            get_pty=True,
            environment=env,
        )

        channel = stdout.channel
        # If tail_lines is set, buffer up to that many lines:
        buffer = deque(maxlen=tail_lines) if tail_lines else None

        while not channel.closed or channel.recv_ready() or channel.recv_stderr_ready():
            r, _, _ = select.select([channel], [], [], 1.0)
            if channel.recv_ready():
                chunk = channel.recv(1024).decode()
                for line in chunk.splitlines():
                    if buffer is not None:
                        buffer.append(("INFO", line))
                    else:
                        self.logger.info(line)
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(1024).decode()
                for line in chunk.splitlines():
                    if buffer is not None:
                        buffer.append(("ERROR", line))
                    else:
                        self.logger.error(line)

        exit_code = channel.recv_exit_status()

        # If we were buffering, now print only the last tail_lines
        if buffer is not None:
            self.logger.info(f"--- Last {len(buffer)} lines of output ---")
            for level, line in buffer:
                if level == "INFO":
                    self.logger.info(line)
                else:
                    self.logger.error(line)

        return exit_code

    def transfer_directory(self, local_path: str, remote_path: str) -> None:
        local_path = str(Path(local_path).resolve())
        if not os.path.isdir(local_path):
            raise VMOperationError(f"Local directory not found: {local_path}")

        sftp = self.get_sftp()
        self.exec_command(f"mkdir -p {remote_path}")
        for root, dirs, files in os.walk(local_path):
            rel = os.path.relpath(root, local_path)
            rel = "" if rel == "." else rel
            target_dir = Path(remote_path, rel).as_posix()
            try:
                sftp.mkdir(target_dir)
            except IOError:
                pass
            for fn in files:
                local_file = os.path.join(root, fn)
                remote_file = f"{target_dir}/{fn}"
                self.logger.debug("SFTP: %s → %s", local_file, remote_file)
                sftp.put(local_file, remote_file)
        self.logger.info("Transferred directory %s → %s", local_path, remote_path)

    def get_sftp(self) -> paramiko.SFTPClient:
        """Get or create an SFTP client using the existing connection."""
        # Refresh the connection if needed
        self.connect()
        self._last_used = time.time()

        if not self._sftp:
            self._sftp = self.client.open_sftp()
        return self._sftp

    def close(self) -> None:
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self.client:
            self.client.close()
            self.client = None
            self.logger.info("SSH connection closed")


# ---------------------------
# Main Class: QemuVMManager
# ---------------------------
class QemuVMManager:
    """Manage QEMU VMs running inside Docker containers."""

    def __init__(
        self,
        config: Optional[VMConfig] = None,
        docker_client: Optional[docker.DockerClient] = None,
        logger: Optional[logging.Logger] = None,
        ssh_config: Optional[SSHConfig] = None,
    ) -> None:
        self.config = config or VMConfig()
        self.client = docker_client or docker.from_env()
        self.logger = logger or setup_logger("QemuVMManager")
        self.ssh_config = ssh_config or SSHConfig(
            hostname="localhost",
            port=self.config.ssh_port,
            username="user",
            password="password",
        )
        self.state: VMState = VMState.INITIALIZING
        self.container: Optional[Container] = None
        self.instance_dir: Optional[str] = None
        self.on_state_change: Optional[Callable[[VMState], None]] = None

        self.ssh_client = None  # Add this line to store a persistent SSH manager
        self._validate_config()

    def _validate_config(self) -> None:
        cfg = self.config

        # must have a base image directory
        if not cfg.vm_base_dir or not cfg.vm_base_dir.is_dir():
            raise VMCreationError(f"vm_base_dir must exist and be a directory (got {cfg.vm_base_dir!r})")

        # ensure instances_dir is writable
        if not os.access(cfg.instances_dir, os.W_OK):
            raise VMCreationError(f"instances_dir is not writable: {cfg.instances_dir!r}")

        # if you plan to share via 9p, host_shared_dir must exist
        if cfg.host_shared_dir and not cfg.host_shared_dir.is_dir():
            raise VMCreationError(f"host_shared_dir must exist if set (got {cfg.host_shared_dir!r})")

        # if you plan to deploy a server, check for start.sh
        if cfg.server_host_dir:
            start_sh = cfg.server_host_dir / "start.sh"
            if not start_sh.is_file():
                raise VMCreationError(f"server_host_dir must contain start.sh (checked {start_sh!r})")

        # port sanity
        ports = [cfg.vnc_port, cfg.ssh_port] + list(cfg.extra_ports.values())
        for p in ports:
            if not (1 <= p <= 65535):
                raise VMCreationError(f"Invalid port number: {p}")

        # caps/devices are strings
        if not all(isinstance(d, str) for d in cfg.devices):
            raise VMCreationError("All devices must be strings")
        if not all(isinstance(c, str) for c in cfg.capabilities):
            raise VMCreationError("All capabilities must be strings")

    def get_ssh_client(self) -> SSHClient:
        """Create or reuse an SSH client instance."""
        if self.ssh_client is None:
            self.ssh_client = SSHClient(logger=self.logger, config=self.ssh_config)
        return self.ssh_client

    def check_ssh_connection(self) -> bool:
        """Check if SSH connection is active and ready.

        Returns:
            bool: True if connection is active, False otherwise
        """
        if not self.ssh_client:
            return False

        return self.ssh_client._is_connection_active()

    def wait_for_ssh_connection(self, timeout: int = 180, retry_interval: int = 5) -> bool:
        """Wait for SSH connection to be available.

        Args:
            timeout: Maximum time to wait in seconds
            retry_interval: Time between connection attempts in seconds

        Returns:
            bool: True if connection was established, False if timed out
        """
        self.logger.info(f"Waiting for SSH connection to be available (timeout: {timeout}s)")

        start_time = time.time()
        attempt = 0
        while time.time() - start_time < timeout:
            attempt += 1
            try:
                # Try to get a connection - close any existing connection first
                ssh_client = self.get_ssh_client()
                ssh_client.close()

                # Try a simple command to verify connection
                result = ssh_client.exec_command("echo 'SSH connection test'")
                if result["status"] == 0:
                    elapsed = time.time() - start_time
                    self.logger.info(f"SSH connection established after {elapsed:.1f}s ({attempt} attempts)")
                    return True

            except Exception as e:
                self.logger.debug(f"SSH connection attempt {attempt} failed: {e}")

            # Only log every few attempts to avoid cluttering logs
            if attempt % 3 == 0:
                elapsed = time.time() - start_time
                self.logger.info(f"Still waiting for SSH... ({elapsed:.1f}s elapsed, {attempt} attempts)")

            time.sleep(retry_interval)

        self.logger.error(f"SSH connection timed out after {timeout}s")
        return False

    def _get_instance_dir(self) -> str:
        inst_dir = (self.config.instances_dir / self.config.container_name).resolve()
        set_writable_dir(inst_dir)
        try:
            shutil.copytree(self.config.vm_base_dir, inst_dir, dirs_exist_ok=True)
        except Exception as e:
            raise VMCreationError(f"Failed to copy base VM: {e}") from e
        return str(inst_dir)

    def _mount_shared_in_guest(self, host_shared: Path) -> None:
        cfg = self.config

        # If no host_shared_dir is set, skip the mount
        if not cfg.host_shared_dir:
            return

        # Get SSH client once instead of creating a new one each time
        ssh_client = self.get_ssh_client()
        mount_point = f"/mnt/{host_shared.name}"

        # Add the mount point to the runtime environment BEFORE executing commands
        # This ensures it's available for all subsequent commands including server deployment
        self.config.runtime_env["SCREENSHOTS_PATH"] = mount_point
        self.logger.info(f"Set SCREENSHOTS_PATH={mount_point} in runtime environment")

        cmds = [
            f"echo '{ssh_client.config.password}' | sudo -S mkdir -p {mount_point}",
            f"echo '{ssh_client.config.password}' | sudo -S mount -t 9p -o trans=virtio {cfg.guest_shared_dir.name} {mount_point}",
        ]

        # Execute commands in sequence, each waiting for completion
        results = ssh_client.exec_commands(cmds)
        if any(r["status"] != 0 for r in results):
            self.logger.error("Failed to mount shared directory in guest")

    def _deploy_server(self) -> None:
        cfg = self.config
        if not cfg.server_host_dir:
            return

        sshc = self.get_ssh_client()
        sshc.transfer_directory(cfg.server_host_dir.as_posix(), cfg.server_guest_dir.as_posix())

        # Log the runtime environment for debugging
        self.logger.info(f"Deploying server with runtime environment: {self.config.runtime_env}")

        # Verify that SCREENSHOTS_PATH is set (critical for your script)
        if "SCREENSHOTS_PATH" not in self.config.runtime_env:
            self.logger.warning("SCREENSHOTS_PATH not set in runtime environment!")
            if self.config.host_shared_dir:
                # Try to derive it from the mount point if not explicitly set
                name = Path(self.instance_dir).name if self.instance_dir else "default"
                mount_point = f"/mnt/{name}"
                self.logger.info(f"Using derived SCREENSHOTS_PATH={mount_point}")
                self.config.runtime_env["SCREENSHOTS_PATH"] = mount_point

        # Make start.sh executable and run it with the runtime environment
        cmds = [f"chmod +x {cfg.server_guest_dir}/start.sh", f"cd {cfg.server_guest_dir} && ./start.sh"]
        # stream logs live
        exit_codes = sshc.exec_commands(cmds, stream=True, env=self.config.runtime_env)
        if any(code != 0 for code in exit_codes):
            raise VMOperationError(f"Server startup failed with codes {exit_codes}")

        self.logger.info("Server started in guest at %s", cfg.server_guest_dir)

    def _set_state(self, new_state: VMState) -> None:
        old = self.state
        self.state = new_state
        self.logger.info("VM state: %s -> %s", old.value, new_state.value)
        if self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception as e:
                self.logger.error("State callback error: %s", e)

    def prepare_container_config(self) -> Dict[str, Any]:
        """Prepare Docker container configuration without creating it."""
        cfg = self.config
        self.instance_dir = self._get_instance_dir()
        name = Path(self.instance_dir).name
        self.logger.info("Preparing container configuration for %s", name)

        mounts = [Mount("/storage", self.instance_dir, type="bind")]

        if cfg.host_shared_dir:
            host_shared = (cfg.host_shared_dir / name).resolve()
            set_writable_dir(host_shared)
            mounts.append(Mount(cfg.guest_shared_dir.as_posix(), host_shared.as_posix(), type="bind"))

        if not self.ensure_image_exists(cfg.container_image):
            raise VMCreationError(f"Image {cfg.container_image} missing")

        vm_env = {
            "BOOT": cfg.vm_boot_image,
            "DEBUG": "Y" if cfg.enable_debug else "N",
            "RAM_SIZE": cfg.vm_ram,
            "CPU_CORES": str(cfg.vm_cpu_cores),
            "DISK_SIZE": cfg.vm_disk_size,
            **cfg.extra_env,
        }

        ports = {
            "8006/tcp": cfg.vnc_port,
            "22/tcp": cfg.ssh_port,
            **cfg.extra_ports,
        }

        return {
            "image": cfg.container_image,
            "name": name,
            "environment": vm_env,
            "devices": cfg.devices,
            "cap_add": cfg.capabilities,
            "ports": ports,
            "mounts": mounts,
            "restart_policy": {"Name": cfg.restart_policy},
            "detach": True,
        }

    def get_container(self, name: Optional[str] = None) -> Optional[Container]:
        """Get a container by name if it exists.

        Args:
            name: Name of the container to retrieve (uses instance name if None)

        Returns:
            Container object if found, None otherwise
        """
        container_name = name or (Path(self.instance_dir).name if self.instance_dir else self.config.container_name)

        try:
            return self.client.containers.get(container_name)
        except docker.errors.NotFound:
            return None
        except Exception as e:
            self.logger.error(f"Error getting container {container_name}: {e}")
            return None

    def create_container(
        self, start_immediately: bool = False, reuse_existing: bool = True, recreate_if_exists: bool = False
    ) -> Container:
        """Create Docker container with prepared configuration or reuse existing.

        Args:
            start_immediately: Whether to start the container after creation
            reuse_existing: Whether to reuse existing container if it exists
            recreate_if_exists: Whether to recreate container if it exists (overrides reuse_existing)

        Returns:
            Container object
        """
        self._set_state(VMState.CREATING)

        try:
            container_config = self.prepare_container_config()
            name = container_config["name"]

            # Check if container already exists
            existing_container = self.get_container(name)

            if existing_container:
                self.logger.info(f"Container {name} already exists")

                if recreate_if_exists:
                    self.logger.info(f"Recreating container {name}")
                    existing_container.remove(force=True)
                    container = self.client.containers.create(**container_config)
                elif reuse_existing:
                    self.logger.info(f"Reusing existing container {name}")
                    container = existing_container
                else:
                    raise VMCreationError(f"Container {name} already exists and reuse_existing=False")
            else:
                # Container doesn't exist, create a new one
                container = self.client.containers.create(**container_config)
                self.logger.info(f"Created new container {name}")

            self.container = container

            if start_immediately:
                self.start_container()

            return container

        except Exception as e:
            self._set_state(VMState.ERROR)
            self.logger.error(f"Error creating container: {e}")
            raise VMCreationError(f"Create failed: {e}") from e

    def start_container(self) -> None:
        """Start the container and wait for SSH to be available."""
        if not self.container:
            raise VMOperationError("No container available to start")

        self._set_state(VMState.STARTING)

        try:
            self.container.start()
            self.logger.info("Container started, waiting for VM to boot...")

            # Wait for SSH connection to be available before proceeding
            if not self.wait_for_ssh_connection():
                self._set_state(VMState.ERROR)
                raise VMOperationError("Failed to establish SSH connection to VM after timeout")

            self._set_state(VMState.RUNNING)

        except Exception as e:
            self._set_state(VMState.ERROR)
            self.logger.error("Failed to start container: %s", e)
            raise VMOperationError(f"Start failed: {e}") from e

    def get_or_create_container(
        self, start_if_stopped: bool = True, recreate: bool = False, setup_if_needed: bool = True
    ) -> Container:
        """Get existing container or create a new one, ensuring the guest VM is fully set up.

        Args:
            start_if_stopped: Whether to start the container if it exists but is stopped
            recreate: Whether to recreate the container if it exists
            setup_if_needed: Whether to set up the guest VM if the container is started

        Returns:
            Container object
        """
        # Prepare the config to ensure instance_dir is set
        if not self.instance_dir:
            _ = self.prepare_container_config()

        name = Path(self.instance_dir).name

        # Try to get existing container
        container = self.get_container(name)
        vm_was_running = False

        if container:
            self.container = container

            if recreate:
                self.logger.info(f"Recreating container {name}")
                container.remove(force=True)
                container = self.create_container()
                # New container always needs to be started and set up
                self.start_container()
                if setup_if_needed:
                    self.setup_guest_vm()
                return container

            self.logger.info(f"Reusing container {name}")
            vm_was_running = container.status == "running"

            if start_if_stopped and not vm_was_running:
                self.logger.info(f"Starting stopped container {name}")
                self.start_container()
                # Container was stopped, so we need to set up the guest VM
                if setup_if_needed:
                    self.setup_guest_vm()
            elif vm_was_running:
                # Container was already running, update our state
                self._set_state(VMState.RUNNING)

                # Check if VM is properly set up by testing SSH connection
                if setup_if_needed and self.check_server_running():
                    self.logger.info("VM is already running with server")
                elif setup_if_needed:
                    self.logger.info("VM is running but server needs setup")
                    self.setup_guest_vm()

            return container

        # Container doesn't exist, create a new one
        container = self.create_container()

        if start_if_stopped:
            self.start_container()
            if setup_if_needed:
                self.setup_guest_vm()

        return container

    def check_server_running(self) -> bool:
        """Check if the server is running in the guest VM.

        Returns:
            bool: True if server is running, False otherwise
        """
        if not self.check_ssh_connection():
            return False

        try:
            sshc = self.get_ssh_client()
            # Simple check to see if server process is running
            result = sshc.exec_command("pgrep -f 'uv run main.py'", directory="/home/user/server")
            return result["status"] == 0
        except Exception as e:
            self.logger.debug(f"Failed to check if server is running: {e}")
            return False

    def setup_guest_vm(self) -> None:
        """Configure guest VM after it's running and SSH is available."""
        if self.state != VMState.RUNNING:
            raise VMOperationError("VM must be in RUNNING state to setup guest")

        try:
            # Set up the environment for the guest VM
            if self.config.host_shared_dir:
                name = Path(self.instance_dir).name
                host_shared = (self.config.host_shared_dir / name).resolve()
                self._mount_shared_in_guest(host_shared)

            # Deploy the server if configured
            self._deploy_server()

            self.logger.info("Guest VM setup completed successfully")

        except Exception as e:
            self.logger.error("Failed to setup guest VM: %s", e)
            raise VMOperationError(f"Guest setup failed: {e}") from e

    def create_and_setup_vm(self) -> Container:
        """Convenience method to create, start and setup VM in one go."""
        container = self.create_container(start_immediately=False)
        self.start_container()
        self.setup_guest_vm()
        return container

    def ensure_image_exists(self, image_name: Optional[str] = None) -> bool:
        image_name = image_name or self.config.container_image
        try:
            self.client.images.get(image_name)
            return True
        except docker.errors.ImageNotFound:
            self.logger.info("Pulling image %s", image_name)
            self.client.api.pull(image_name, stream=True)
            return True
        except Exception:
            return False

    def cleanup_vm(self, delete_storage: bool = True) -> None:
        if self.container:
            self.logger.info("Stopping/removing container %s", self.config.container_name)
            try:
                self.container.stop(timeout=30)
                self.container.remove()
            except Exception as e:
                self.logger.error("Cleanup error: %s", e)
            self.container = None
        if delete_storage and self.instance_dir:
            try:
                shutil.rmtree(self.instance_dir)
                self.logger.info("Deleted storage %s", self.instance_dir)
            except Exception as e:
                self.logger.error("Failed to delete storage %s: %s", self.instance_dir, e)
            self.instance_dir = None

    def stop_and_cleanup(self) -> None:
        """Stop the VM and clean up resources including SSH connections."""
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None
        self.cleanup_vm(delete_storage=True)
        self._set_state(VMState.STOPPED)

import base64
import json
import logging
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import paramiko

import docker
from docker.models.containers import Container
from docker.types import Mount


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
    CREATED = "created"
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
    """Configuration for the QEMU VM.

    The fields prefixed with 'qemu_' are used to configure the QEMU container (host),
    while 'vm_runtime_env' contains environment variables for the guest VM.
    """

    image: str = "qemux/qemu"
    container_name: str = "qemu"
    # QEMU container configuration
    qemu_boot: str = "ubuntu"
    qemu_ram_size: str = "4G"
    qemu_cpu_cores: str = "4"
    qemu_disk_size: str = "16G"
    qemu_debug: str = "Y"
    qemu_extra_env: Dict[str, str] = field(default_factory=dict)

    # Container ports and restart policy
    novnc_port: int = 8006
    ssh_port: int = 2222
    restart_policy: str = "always"

    extra_devices: List[str] = field(default_factory=list)
    extra_caps: List[str] = field(default_factory=list)

    # Guest VM settings
    vm_runtime_env: Dict[str, str] = field(default_factory=dict)

    # Shared storage options and directory settings
    base_vm_path: Optional[str] = None
    shared_path: Optional[str] = None
    env_dir: str = os.path.abspath("docker/environments")

    def __post_init__(self):
        # Automatically convert base_vm_path and env_dir to absolute paths if provided
        if self.base_vm_path:
            path = Path(self.base_vm_path)
            path.exists()
            self.base_vm_path = path
        if self.env_dir:
            path = Path(self.env_dir)
            path.exists()
            self.env_dir = path

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "VMConfig":
        valid_keys = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in config_dict.items() if k in valid_keys}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_file: Union[str, Path]) -> "VMConfig":
        with open(json_file, "r") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and not callable(v)}

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

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "SSHConfig":
        valid_keys = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in config_dict.items() if k in valid_keys}
        return cls(**filtered)


# ---------------------------
# Utility Functions
# ---------------------------
def set_writable_dir(path: Union[str, Path], mode: int = 0o777) -> None:
    p = Path(path)
    p.mkdir(exist_ok=True, parents=True)
    os.chmod(str(p), mode)


def update_remote_env_file(
    exec_command: Callable[[str], Dict[str, Any]],
    remote_file: str,
    env: Dict[str, str],
    sudo_password: str,
) -> bool:
    """Updates a remote environment file by encoding the content to avoid quoting issues."""
    env_lines = [f'export {key}="{value}"' for key, value in env.items()]
    content = "\n".join(env_lines)
    encoded = base64.b64encode(content.encode()).decode()
    # The command decodes the base64-encoded string directly on the target host.
    cmd = f'echo "{sudo_password}" | sudo -S sh -c "echo {encoded} | base64 --decode > {remote_file}"'
    result = exec_command(cmd)
    return result.get("status", -1) == 0


# ---------------------------
# SSHManager Class
# ---------------------------
class SSHManager:
    """Manages SSH connections and operations."""

    def __init__(self, logger: Optional[logging.Logger] = None, config: Optional[SSHConfig] = None) -> None:
        self.logger = logger or setup_logger("SSHManager")
        self.config = config or SSHConfig()
        self.client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    def connect(self, timeout: Optional[int] = None) -> paramiko.SSHClient:
        timeout = timeout or self.config.connect_timeout
        # Check if connection is still alive
        if self.client is not None:
            try:
                transport = self.client.get_transport()
                if transport and transport.is_active():
                    transport.send_ignore()
                    return self.client
                else:
                    self.close()
            except Exception:
                self.close()

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        max_attempts = self.config.max_retries

        for attempt in range(1, max_attempts + 1):
            try:
                self.logger.info(f"SSH connection attempt {attempt}/{max_attempts}...")
                self.client.connect(
                    hostname=self.config.hostname,
                    port=self.config.port,
                    username=self.config.username,
                    password=self.config.password,
                    key_filename=self.config.key_filename,
                    timeout=timeout,
                )
                self.logger.info("SSH connection established")
                return self.client
            except Exception as e:
                if attempt == max_attempts:
                    self.logger.error(f"SSH connection failed: {e}")
                    raise SSHError(f"Failed to establish SSH connection: {e}") from e
                else:
                    wait_time = min(2**attempt, 30)
                    self.logger.info(f"Connection attempt failed: {e}. Retrying in {wait_time}s")
                    time.sleep(wait_time)
        # This point should not be reached
        raise SSHError("SSH connection attempts exhausted without success.")

    def check_connection(self) -> bool:
        """Check if an SSH connection can be established."""
        try:
            with self.ssh_connection():
                return True
        except Exception as e:
            self.logger.error(f"SSH connection check failed: {e}")
            return False

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

    @contextmanager
    def ssh_connection(self):
        """Context manager to ensure connection and proper closure after usage."""
        client = self.connect()
        try:
            yield client
        finally:
            self.close()

    def get_sftp(self) -> paramiko.SFTPClient:
        """Return an SFTP client, reusing or opening as needed."""
        if not self._sftp:
            self._sftp = self.connect().open_sftp()
        return self._sftp

    def exec_command(self, command: str, directory: str = "", timeout: Optional[int] = None) -> Dict[str, Any]:
        timeout = timeout or self.config.command_timeout
        try:
            client = self.connect(timeout=timeout)
            full_command = f"cd {directory} && {command}" if directory else command
            self.logger.info(f"Executing command: {full_command}")
            stdin, stdout, stderr = client.exec_command(full_command, timeout=timeout)
            exit_status = stdout.channel.recv_exit_status()
            stdout_str = stdout.read().decode("utf-8")
            stderr_str = stderr.read().decode("utf-8")
            if exit_status == 0:
                self.logger.info("Command executed successfully")
            else:
                self.logger.error(f"Command failed with exit status {exit_status}: {stderr_str.strip()}")
            return {"status": exit_status, "stdout": stdout_str, "stderr": stderr_str}
        except Exception as e:
            self.logger.error(f"Error executing command: {e}")
            return {"status": -1, "stdout": "", "stderr": str(e)}

    def transfer_directory(self, local_path: str, remote_path: str, skip_items: Optional[List[str]] = None) -> bool:
        skip_items = skip_items or []
        local_path = str(Path(local_path).resolve())
        if not os.path.exists(local_path):
            self.logger.error(f"Local directory '{local_path}' not found")
            return False
        sftp = self.get_sftp()
        self.exec_command(f"mkdir -p {remote_path}")
        for root, dirs, files in os.walk(local_path):
            # Filter out directories containing skip items
            dirs[:] = [d for d in dirs if not any(skip in os.path.join(root, d) for skip in skip_items)]
            rel_path = os.path.relpath(root, local_path)
            rel_path = "" if rel_path == "." else rel_path
            remote_dir = str(Path(remote_path, rel_path)).replace("\\", "/")
            if rel_path:
                try:
                    sftp.mkdir(remote_dir)
                except IOError:
                    # Directory might already exist
                    pass
            for file in files:
                local_file = os.path.join(root, file)
                if any(skip in local_file for skip in skip_items):
                    self.logger.debug(f"Skipping file: {local_file}")
                    continue
                remote_file = str(Path(remote_dir, file)).replace("\\", "/")
                try:
                    sftp.put(local_file, remote_file)
                    self.logger.debug(f"Uploaded {local_file} -> {remote_file}")
                except Exception as e:
                    self.logger.error(f"Failed to upload {local_file} to {remote_file}: {e}")
        self.logger.info(f"Transferred {local_path} to {remote_path} (skipped: {skip_items})")
        return True


# ---------------------------
# Main Class: QemuVMManager
# ---------------------------
class QemuVMManager:
    """Manages QEMU virtual machines in Docker containers."""

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
        # Use provided SSH config or create a default based on self.config.
        self.ssh_config = ssh_config or SSHConfig(
            hostname="localhost",
            port=self.config.ssh_port,
            username="user",
            password="password",
            connect_timeout=30,
            max_retries=5,
            retry_delay=5,
            command_timeout=60,
        )
        self.state: VMState = VMState.CREATED
        self.container: Optional[Container] = None
        self.instance_dir: Optional[str] = None
        self.on_state_change: Optional[Callable[[VMState], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

        self._validate_config()

    def create_ssh_manager(self) -> SSHManager:
        return SSHManager(logger=self.logger, config=self.ssh_config)

    def _validate_config(self) -> None:
        if not self.config.container_name:
            raise ValueError("Container name cannot be empty")
        if not self.config.base_vm_path:
            self.logger.error("Base VM directory does not exist!")
            raise VMCreationError("Base VM directory does not exist!")

    def register_callbacks(
        self,
        on_state_change: Optional[Callable[[VMState], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.on_state_change = on_state_change
        self.on_error = on_error

    def _set_state(self, new_state: VMState) -> None:
        old_state = self.state
        self.state = new_state
        self.logger.info(f"VM state changed: {old_state.value} -> {new_state.value}")
        if self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception as e:
                self.logger.error(f"State change callback error: {e}")

    def get_container_status(self) -> Dict[str, Any]:
        if not self.container:
            self.logger.warning("No container associated with this VM manager.")
            return {}
        try:
            self.container.reload()
            return self.container.attrs
        except Exception as e:
            self.logger.error(f"Failed to get container status: {e}")
            return {}

    def container_exists(self, container_name: Optional[str] = None) -> bool:
        name = container_name or self.config.container_name
        try:
            containers = self.client.containers.list(all=True, filters={"name": name})
            return any(c.name == name for c in containers)
        except Exception as e:
            self.logger.error(f"Error checking container existence: {e}")
            return False

    def _get_instance_dir(self) -> str:
        unique_name = f"{self.config.container_name}_{int(time.time())}"
        instance_dir = (Path(self.config.env_dir) / unique_name).resolve()
        set_writable_dir(instance_dir)
        try:
            shutil.copytree(str(self.config.base_vm_path), instance_dir, dirs_exist_ok=True)
        except Exception as e:
            self.logger.error(f"Error copying base VM directory: {e}")
            raise VMCreationError(f"Failed to setup instance storage: {e}") from e
        return str(instance_dir)

    def ensure_image_exists(self, image_name: Optional[str] = None) -> bool:
        image_name = image_name or self.config.image
        try:
            self.logger.info(f"Checking image {image_name} locally...")
            try:
                self.client.images.get(image_name)
                self.logger.info(f"Image {image_name} exists.")
                return True
            except docker.errors.ImageNotFound:
                self.logger.info(f"Image {image_name} not found. Pulling from registry...")
                for line in self.client.api.pull(image_name, stream=True, decode=True):
                    if "status" in line:
                        self.logger.info(f"Pulling {image_name}: {line['status']}")
                self.client.images.get(image_name)
                self.logger.info(f"Image {image_name} pulled successfully.")
                return True
        except Exception as e:
            self.logger.error(f"Error ensuring image exists: {e}")
            return False

    def create_container(self, start_container: bool = True) -> Container:
        self._set_state(VMState.CREATED)
        try:
            cfg = self.config

            # 1) Prepare per-instance storage
            self.instance_dir = self._get_instance_dir()
            unique_tag = Path(self.instance_dir).name  # e.g. "qemu_1678831234"
            self.logger.info(f"Creating container {cfg.container_name} with storage at {self.instance_dir}")

            # 2) Mounts: main disk + optional shared folder
            mounts: List[Mount] = [Mount(target="/storage", source=self.instance_dir, type="bind")]

            if cfg.shared_path:
                # create a per-VM subfolder named by the unique_tag
                host_shared = Path(cfg.shared_path) / unique_tag
                set_writable_dir(host_shared)

                self.logger.info(f"Binding host {host_shared} → container /shared {host_shared}")
                # Needs to be absolute path
                mounts.append(Mount(target="/shared", source=str(host_shared.resolve()), type="bind"))

            else:
                self.logger.warning("No shared_path configured; skipping shared mount.")

            # 3) Ensure QEMU image is present
            if not self.ensure_image_exists(cfg.image):
                raise VMCreationError(f"Image {cfg.image} does not exist")

            # 4) Environment & ports
            environment = {
                "BOOT": cfg.qemu_boot,
                "DEBUG": cfg.qemu_debug,
                "RAM_SIZE": cfg.qemu_ram_size,
                "CPU_CORES": cfg.qemu_cpu_cores,
                "DISK_SIZE": cfg.qemu_disk_size,
                "DISPLAY": ":0",
                **cfg.qemu_extra_env,
            }
            ports = {"8006/tcp": cfg.novnc_port, "22/tcp": cfg.ssh_port}
            devices = [d for d in ("/dev/kvm", "/dev/net/tun") if os.path.exists(d)]
            cap_add = ["NET_ADMIN"] + cfg.extra_caps

            # 5) Create container
            container = self.client.containers.create(
                image=cfg.image,
                name=cfg.container_name,
                environment=environment,
                devices=devices,
                cap_add=cap_add,
                ports=ports,
                mounts=mounts,
                restart_policy={"Name": cfg.restart_policy},
                detach=True,
            )
            self.container = container
            self.logger.info(f"Container {cfg.container_name} created")

            if start_container:
                self._set_state(VMState.STARTING)
                container.start()
                self.logger.info(f"Container {cfg.container_name} started")
                self._set_state(VMState.RUNNING)

                # 6) Inside the guest VM, sudo‑mount the Virtio‑9p share
                mount_point = f"/mnt/{unique_tag}"
                sudo_pass = self.ssh_config.password.replace("'", r"'\''")
                cmd = (  # make the mount point
                    f"echo '{sudo_pass}' | sudo -S mkdir -p {mount_point} && "
                    # mount via 9p
                    f"echo '{sudo_pass}' | sudo -S mount -t 9p -o trans=virtio shared {mount_point}"
                )
                rc = self.exec_command(cmd)
                if rc["status"] != 0:
                    self.logger.error(f"Failed to sudo‑mount 9p: {rc['stderr'].strip()}")

            else:
                self._set_state(VMState.STOPPED)

            return container

        except Exception as e:
            self.logger.error(f"Error creating container: {e}")
            self._set_state(VMState.ERROR)
            if self.on_error:
                self.on_error(e)
            raise VMCreationError(f"Container creation failed: {e}") from e

    def get_or_create_container(self, start_container: bool = True, recreate_if_exists: bool = False) -> Container:
        name = self.config.container_name
        if self.container_exists(name):
            self.logger.info(f"Container {name} already exists.")
            container = self.client.containers.get(name)
            self.container = container

            if recreate_if_exists:
                self.logger.info("Recreate-if-exists is True; removing old container before creation.")
                self.cleanup_vm(delete_storage=False)
                return self.create_container(start_container=start_container)

            # Otherwise, start or return the existing container.
            if start_container and container.status != "running":
                self._set_state(VMState.STARTING)
                container.start()
                self.logger.info(f"Started container {name}")
                self._set_state(VMState.RUNNING)
            elif container.status == "running":
                self._set_state(VMState.RUNNING)
            else:
                self._set_state(VMState.STOPPED)
            return container
        else:
            return self.create_container(start_container=start_container)

    def wait_for_ssh(
        self,
        hostname: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        max_attempts: int = 10,
        initial_delay: int = 15,
    ) -> bool:
        """
        A simpler method that uses SSHManager.check_connection() in a loop.
        Allows temporary override of SSH parameters.
        """
        ssh_manager = self.create_ssh_manager()
        if hostname:
            ssh_manager.config.hostname = hostname
        if port:
            ssh_manager.config.port = port
        if username:
            ssh_manager.config.username = username
        if password:
            ssh_manager.config.password = password
        if key_filename:
            ssh_manager.config.key_filename = key_filename

        self.logger.info(f"Waiting for SSH service at {ssh_manager.config.hostname}:{ssh_manager.config.port}...")
        time.sleep(initial_delay)  # Initial delay for VM boot completion

        for attempt in range(1, max_attempts + 1):
            if ssh_manager.check_connection():
                self.logger.info("SSH is available.")
                return True
            wait_time = min(2**attempt, 60)
            self.logger.info(f"SSH not ready (attempt {attempt}/{max_attempts}). Waiting {wait_time}s.")
            time.sleep(wait_time)

        self.logger.error(f"Timed out waiting for SSH at {ssh_manager.config.hostname}:{ssh_manager.config.port}")
        return False

    def exec_command(self, command: str, directory: str = "", timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        For convenience, delegate to SSHManager's exec_command.
        """
        ssh_manager = self.create_ssh_manager()
        return ssh_manager.exec_command(command, directory=directory, timeout=timeout)

    def ssh_client_connect(self) -> paramiko.SSHClient:
        """
        Exposes the raw SSHClient for advanced use cases.
        """
        ssh_manager = self.create_ssh_manager()
        return ssh_manager.connect()

    def update_runtime_env_in_vm(self, env: Optional[Dict[str, str]] = None) -> bool:
        """
        Updates the guest VM environment file.
        If no environment is provided, uses vm_runtime_env from the configuration.
        """
        remote_env_file = "/home/user/.vm_env"
        env = env or self.config.vm_runtime_env
        self.logger.info(f"Updating VM runtime environment file {remote_env_file} with: {env}")
        ssh_manager = self.create_ssh_manager()
        return update_remote_env_file(ssh_manager.exec_command, remote_env_file, env, ssh_manager.config.password)

    def cleanup_vm(self, delete_storage: bool = True) -> None:
        """Clean up the running VM by stopping and removing the container,
        and optionally deleting the associated instance storage directories
        in the env_dir.
        """
        if self.container:
            try:
                self.logger.info("Stopping container...")
                self.container.stop(timeout=30)
                self.logger.info("Removing container...")
                self.container.remove()
                self.logger.info("Container removed.")
                # Set container to None to avoid accidental reuse
                self.container = None
            except Exception as e:
                self.logger.error(f"Error during VM cleanup: {e}")
        else:
            self.logger.warning("No container was found; skipping container cleanup.")

        # Remove instance storage directories created in env_dir
        if delete_storage:
            env_dir = self.config.env_dir
            for item in os.listdir(env_dir):
                item_path = os.path.join(env_dir, item)
                # Check if the folder was created for this container (based on naming convention)
                if item.startswith(self.config.container_name) and os.path.isdir(item_path):
                    try:
                        self.logger.info(f"Deleting instance storage directory: {item_path}")
                        shutil.rmtree(item_path)
                        self.logger.info(f"Deleted instance storage: {item_path}")
                    except Exception as e:
                        self.logger.error(f"Error deleting instance storage {item_path}: {e}")

    def stop_and_cleanup(self) -> None:
        """Convenience method that stops the running container and cleans up the storage files."""
        self.logger.info("Stopping VM and performing cleanup...")
        self.cleanup_vm(delete_storage=True)
        self._set_state(VMState.STOPPED)
        self.logger.info("VM and associated storage have been cleaned up.")

"""
QEMU Virtual Machine Manager & SSH Manager
=========================================
This module provides classes to manage QEMU virtual machines in Docker containers,
with SSH capabilities for remote access and file transfer.
"""

import json
import logging
import os
import shutil
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import paramiko
import urllib3

import docker
from docker.models.containers import Container
from docker.types import Mount


# ---------------------------
# Custom Exceptions
# ---------------------------
class VMManagerError(Exception):
    """Base exception class for VM Manager errors."""

    pass


class VMCreationError(VMManagerError):
    """Error during VM creation."""

    pass


class VMOperationError(VMManagerError):
    """Error during VM operations."""

    pass


class SSHError(VMManagerError):
    """Error during SSH operations."""

    pass


# ---------------------------
# VM State Tracking
# ---------------------------
class VMState(Enum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# ---------------------------
# Dataclass Configurations
# ---------------------------
@dataclass
class VMConfig:
    image: str = "qemux/qemu"
    container_name: str = "qemu"
    # The storage_path is not directly used now (it will be generated from base_vm_dir).
    storage_path: str = ""
    shared_path: Optional[str] = None

    boot: str = "ubuntu"
    ram_size: str = "4G"
    cpu_cores: str = "4"
    disk_size: str = "16G"

    novnc_port: int = 8006
    ssh_port: int = 2222

    restart_policy: str = "always"
    mount_tag: str = "shared"
    debug: str = "Y"

    qemu_args: str = ""
    extra_devices: List[str] = field(default_factory=list)
    extra_caps: List[str] = field(default_factory=list)
    extra_env: Dict[str, str] = field(default_factory=dict)

    # New fields for the base image template and environments directory
    base_vm_dir: str = os.path.abspath("src/docker/vms/ubuntu-noble")
    env_dir: str = os.path.abspath("src/docker/environments")

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "VMConfig":
        valid_keys = {f.name for f in fields(cls)}
        filtered_dict = {k: v for k, v in config_dict.items() if k in valid_keys}
        return cls(**filtered_dict)

    @classmethod
    def from_json(cls, json_file: Union[str, Path]) -> "VMConfig":
        with open(json_file, "r") as f:
            config_dict = json.load(f)
        return cls.from_dict(config_dict)

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
        filtered_dict = {k: v for k, v in config_dict.items() if k in valid_keys}
        return cls(**filtered_dict)


# ---------------------------
# Helper Functions
# ---------------------------
def set_writable_dir(path: str, mode: int = 0o777) -> None:
    """Ensure that the directory exists and is writable."""
    os.makedirs(path, exist_ok=True)
    os.chmod(path, mode)


def create_http_client(retries=3, connect_timeout=30.0, read_timeout=60.0, maxsize=10):
    return urllib3.PoolManager(
        retries=urllib3.Retry(retries, redirect=2, backoff_factor=0.5),
        timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout),
        maxsize=maxsize,
    )


# Global HTTP client for reuse
http = create_http_client()


def is_url(path: str) -> bool:
    if not isinstance(path, str):
        return False
    try:
        result = urllib.parse.urlparse(path)
        return all([result.scheme, result.netloc]) and result.scheme in ["http", "https", "ftp"]
    except Exception:
        return False


# ---------------------------
# Main Classes
# ---------------------------
class QemuVMManager:
    """
    Manages QEMU virtual machines in Docker containers.
    """

    def __init__(
        self,
        config: Optional[VMConfig] = None,
        docker_client: Optional[docker.DockerClient] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or VMConfig()
        self.client = docker_client or docker.from_env()
        self.logger = logger or self._setup_logger()
        self.state = VMState.CREATED
        self.container = None
        self.on_state_change: Optional[Callable[[VMState], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        self._validate_config()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("QemuVMManager")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _validate_config(self) -> None:
        if not self.config.container_name:
            raise ValueError("Container name cannot be empty")
        # Note: storage_path will be generated uniquely from base_vm_dir, so we ignore any preset value.
        if self.config.shared_path:
            self.config.shared_path = os.path.abspath(self.config.shared_path)

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
                self.logger.error(f"Error in state change callback: {e}")

    def get_container_status(self) -> Dict[str, Any]:
        """
        Retrieve the current status and attributes of the container.

        Returns:
            A dictionary of container attributes, or an empty dict if no container exists.
        """
        if not self.container:
            self.logger.warning("No container is currently associated with this VM manager.")
            return {}
        try:
            self.container.reload()
            return self.container.attrs
        except Exception as e:
            self.logger.error(f"Failed to get container status: {e}")
            return {}

    def container_exists(self, container_name: Optional[str] = None) -> bool:
        container_name = container_name or self.config.container_name
        try:
            containers = self.client.containers.list(all=True, filters={"name": container_name})
            return any(c.name == container_name for c in containers)
        except Exception as e:
            self.logger.error(f"Error checking container existence: {e}")
            return False

    def _get_instance_dir(self) -> str:
        """
        Create a new, unique instance directory for this VM by copying the base VM image.
        The directory name is based on the container name and the current timestamp,
        and it is created inside the directory specified by self.config.env_dir.
        """
        unique_name = f"{self.config.container_name}{int(time.time())}"
        instance_dir = os.path.join(self.config.env_dir, unique_name)
        os.makedirs(instance_dir, exist_ok=True)
        base_dir = self.config.base_vm_dir
        if os.path.exists(base_dir):
            try:
                shutil.copytree(base_dir, instance_dir, dirs_exist_ok=True)
            except Exception as e:
                self.logger.error(f"Failed to copy base VM image: {e}")
                raise VMCreationError(f"Failed to copy base VM image: {e}") from e
        else:
            self.logger.error("Base VM directory does not exist!")
            raise VMCreationError("Base VM directory does not exist!")
        return instance_dir

    def create_container(self, start_container: bool = True) -> Container:
        self._set_state(VMState.CREATED)
        try:
            cfg = self.config
            container_name = cfg.container_name
            # Generate a unique storage directory for this container.
            storage_path = self._get_instance_dir()
            self.logger.info(f"Creating container {container_name} with unique storage at {storage_path}")

            mounts = [Mount(target="/storage", source=storage_path, type="bind")]
            qemu_args = cfg.qemu_args

            if cfg.shared_path:
                shared_path = os.path.abspath(cfg.shared_path)
                self.logger.info(f"Setting up shared path at {shared_path} with mount tag '{cfg.mount_tag}'")
                set_writable_dir(shared_path)
                mounts.append(Mount(target="/shared", source=shared_path, type="bind"))
                if not qemu_args or "-virtfs" not in qemu_args:
                    virtfs_args = (
                        f"-virtfs local,path=/shared,mount_tag={cfg.mount_tag},security_model=mapped,id={cfg.mount_tag}"
                    )
                    qemu_args = f"{qemu_args} {virtfs_args}" if qemu_args else virtfs_args

            if not self.ensure_image_exists(cfg.image):
                raise VMCreationError(f"Failed to ensure image {cfg.image} exists")

            environment = {
                "BOOT": cfg.boot,
                "DEBUG": cfg.debug,
                "RAM_SIZE": cfg.ram_size,
                "CPU_CORES": cfg.cpu_cores,
                "DISK_SIZE": cfg.disk_size,
                "DISPLAY": ":0",
                "XAUTHORITY": "/run/user/1000/.mutter-Xwaylandauth.*",
            }
            environment.update(cfg.extra_env)
            if qemu_args:
                environment["QEMU_ARGS"] = qemu_args

            ports = {"8006/tcp": cfg.novnc_port, "22/tcp": cfg.ssh_port}
            devices = list(cfg.extra_devices)
            for device in ["/dev/kvm", "/dev/net/tun"]:
                if os.path.exists(device):
                    devices.append(device)
                else:
                    self.logger.warning(f"Device {device} not found")
                    if device == "/dev/kvm":
                        self.logger.warning("KVM acceleration not available")
            cap_add = ["NET_ADMIN"] + list(cfg.extra_caps)

            container = self.client.containers.create(
                image=cfg.image,
                name=container_name,
                environment=environment,
                devices=devices,
                cap_add=cap_add,
                ports=ports,
                mounts=mounts,
                restart_policy={"Name": cfg.restart_policy},
                detach=True,
            )

            self.container = container
            self.logger.info(f"Container {container_name} created")
            if start_container:
                self._set_state(VMState.STARTING)
                container.start()
                self.logger.info(f"Container {container_name} started")
                self._set_state(VMState.RUNNING)
                if cfg.shared_path:
                    self.logger.info(f"9p file sharing enabled with mount tag '{cfg.mount_tag}'")
                    self.logger.info(
                        f"In the VM, use: sudo mount -t 9p -o trans=virtio {cfg.mount_tag} /mnt/{cfg.mount_tag}"
                    )
            else:
                self.logger.info(f"Container {container_name} created but not started")
                self._set_state(VMState.STOPPED)
            return container
        except Exception as e:
            self.logger.error(f"Error creating container: {e}")
            self._set_state(VMState.ERROR)
            if self.on_error:
                self.on_error(e)
            raise VMCreationError(f"Failed to create container: {e}") from e

    def get_or_create_container(self, start_container: bool = True, recreate_if_exists: bool = False) -> Container:
        container_name = self.config.container_name
        if self.container_exists(container_name):
            if recreate_if_exists:
                self.logger.info(f"Container {container_name} exists, removing it")
                try:
                    container = self.client.containers.get(container_name)
                    if container.status == "running":
                        container.stop()
                    container.remove()
                    self.logger.info(f"Container {container_name} removed")
                except Exception as e:
                    self.logger.error(f"Error removing container: {e}")
                    if self.on_error:
                        self.on_error(e)
                    raise VMOperationError(f"Failed to remove container: {e}") from e
            else:
                self.logger.info(f"Container {container_name} exists, returning it")
                container = self.client.containers.get(container_name)
                self.container = container
                if start_container and container.status != "running":
                    self._set_state(VMState.STARTING)
                    container.start()
                    self.logger.info(f"Started container {container_name}")
                    self._set_state(VMState.RUNNING)
                elif container.status == "running":
                    self._set_state(VMState.RUNNING)
                else:
                    self._set_state(VMState.STOPPED)
                return container
        return self.create_container(start_container=start_container)

    def ensure_image_exists(self, image_name: Optional[str] = None) -> bool:
        image_name = image_name or self.config.image
        try:
            self.logger.info(f"Checking for image {image_name} locally...")
            try:
                self.client.images.get(image_name)
                self.logger.info(f"Image {image_name} exists locally")
                return True
            except docker.errors.ImageNotFound:
                self.logger.info(f"Image {image_name} not found locally; pulling from registry...")
                pull_output = self.client.api.pull(image_name, stream=True, decode=True)
                for line in pull_output:
                    if "progress" in line:
                        self.logger.info(f"Pulling {image_name}: {line.get('status', '')} {line.get('progress', '')}")
                    elif "status" in line:
                        self.logger.info(f"Pulling {image_name}: {line['status']}")
                self.client.images.get(image_name)
                self.logger.info(f"Successfully pulled image {image_name}")
                return True
        except Exception as e:
            self.logger.error(f"Error ensuring image exists: {e}")
            return False

    def configure_9p_in_vm(
        self, ssh_manager: "SSHManager", mount_tag: Optional[str] = None, mount_point: Optional[str] = None
    ) -> bool:
        mount_tag = mount_tag or self.config.mount_tag
        try:
            if not mount_point:
                mount_point = f"/mnt/{mount_tag}"
            result = ssh_manager.exec_command(f"mkdir -p {mount_point}")
            if result["status"] != 0:
                result = ssh_manager.exec_command(
                    f"echo '{ssh_manager.config.password}' | sudo -S mkdir -p {mount_point}"
                )
                if result["status"] != 0:
                    self.logger.error(f"Failed to create mount point: {result['stderr']}")
                    return False
            ssh_manager.exec_command(f"echo '{ssh_manager.config.password}' | sudo -S chmod 777 {mount_point}")
            mount_cmd = (
                f"echo '{ssh_manager.config.password}' | sudo -S mount -t 9p -o trans=virtio,version=9p2000.L "
                f"{mount_tag} {mount_point}"
            )
            result = ssh_manager.exec_command(mount_cmd)
            if result["status"] != 0:
                self.logger.warning("Initial 9p mount failed; attempting to load 9p modules...")
                ssh_manager.exec_command(f"echo '{ssh_manager.config.password}' | sudo -S modprobe 9pnet")
                ssh_manager.exec_command(f"echo '{ssh_manager.config.password}' | sudo -S modprobe 9pnet_virtio")
                result = ssh_manager.exec_command(mount_cmd)
                if result["status"] != 0:
                    self.logger.error(f"Failed to mount 9p filesystem: {result['stderr']}")
                    return False
            self.logger.info(f"9p filesystem mounted at {mount_point}")
            check_cmd = f"grep -q '{mount_tag} {mount_point}' /etc/fstab"
            result = ssh_manager.exec_command(check_cmd)
            if result["status"] != 0:
                fstab_entry = f"{mount_tag} {mount_point} 9p trans=virtio,version=9p2000.L,rw 0 0"
                add_cmd = f"echo '{fstab_entry}' | echo '{ssh_manager.config.password}' | sudo -S tee -a /etc/fstab"
                result = ssh_manager.exec_command(add_cmd)
                if result["status"] != 0:
                    self.logger.error(f"Failed to add 9p entry to fstab: {result['stderr']}")
                    return False
                self.logger.info("9p mount entry added to fstab")
            else:
                self.logger.info("9p mount already present in fstab")
            return True
        except Exception as e:
            self.logger.error(f"Error configuring 9p in VM: {e}")
            return False

    def setup_screenshot_directory(
        self, ssh_manager: "SSHManager", mount_tag: Optional[str] = None, vm_id: Optional[str] = None
    ) -> str:
        mount_tag = mount_tag or self.config.mount_tag
        try:
            if vm_id is None:
                result = ssh_manager.exec_command("hostname")
                vm_id = result["stdout"].strip() if result["status"] == 0 else f"vm_{int(time.time())}"
            mount_point = f"/mnt/{mount_tag}"
            screenshot_dir = f"{mount_point}/{vm_id}/screenshots"
            result = ssh_manager.exec_command(f"mkdir -p {mount_point}/{vm_id}")
            if result["status"] != 0:
                result = ssh_manager.exec_command(
                    f"echo '{ssh_manager.config.password}' | sudo -S mkdir -p {mount_point}/{vm_id}"
                )
                if result["status"] != 0:
                    self.logger.error(f"Failed to create VM directory: {result['stderr']}")
                    return ""
            ssh_manager.exec_command(f"echo '{ssh_manager.config.password}' | sudo -S chmod 777 {mount_point}/{vm_id}")
            result = ssh_manager.exec_command(f"mkdir -p {screenshot_dir}")
            if result["status"] != 0:
                self.logger.error(f"Failed to create screenshot directory: {result['stderr']}")
                return ""
            ssh_manager.exec_command(f"chmod 777 {screenshot_dir}")
            self.logger.info(f"Screenshot directory set up at {screenshot_dir}")
            return screenshot_dir
        except Exception as e:
            self.logger.error(f"Error setting up screenshot directory: {e}")
            return ""

    def wait_for_ssh(
        self,
        hostname: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        max_attempts: int = 10,
        initial_delay: int = 10,
    ) -> bool:
        hostname = hostname or "localhost"
        port = port or self.config.ssh_port
        username = username or "user"
        password = password or "password"
        self.logger.info(f"Waiting for SSH service at {hostname}:{port}...")
        time.sleep(initial_delay)
        for attempt in range(1, max_attempts + 1):
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=hostname,
                    port=port,
                    username=username,
                    password=password,
                    key_filename=key_filename,
                    timeout=10,
                )
                client.close()
                self.logger.info("SSH is available")
                return True
            except Exception as e:
                wait_time = min(2**attempt, 60)
                self.logger.info(f"SSH not ready (attempt {attempt}/{max_attempts}): {e}. Waiting {wait_time}s.")
                time.sleep(wait_time)
        self.logger.error(f"Timed out waiting for SSH at {hostname}:{port}")
        return False

    def exec_command(self, command: str, directory: str = "", timeout: Optional[int] = None) -> Dict[str, Any]:
        timeout = timeout or self.config.command_timeout
        try:
            client = self.ssh_client_connect()
            full_command = f"cd {directory} && {command}" if directory else command
            self.logger.info(f"Executing command: {full_command}")
            stdin, stdout, stderr = client.exec_command(full_command, timeout=timeout)
            exit_status = stdout.channel.recv_exit_status()
            stdout_str = stdout.read().decode("utf-8")
            stderr_str = stderr.read().decode("utf-8")
            if exit_status == 0:
                self.logger.info("Command executed successfully")
            else:
                self.logger.error(f"Command failed with status {exit_status}: {stderr_str.strip()}")
            return {"status": exit_status, "stdout": stdout_str, "stderr": stderr_str}
        except Exception as e:
            self.logger.error(f"Error executing command: {e}")
            return {"status": -1, "stdout": "", "stderr": str(e)}

    def ssh_client_connect(self) -> paramiko.SSHClient:
        """Helper to create and return a fresh SSH client using SSHManager's config."""
        sshm = SSHManager(
            logger=self.logger,
            config=SSHConfig(hostname="localhost", port=self.config.ssh_port, username="user", password="password"),
        )
        return sshm.connect()

    # ---------------------------
    # Added Missing Methods
    # ---------------------------
    def create_ssh_manager(self) -> "SSHManager":
        """
        Create and return an SSHManager instance using the current configuration.
        """
        ssh_config = SSHConfig(
            hostname="localhost",
            port=self.config.ssh_port,
            username="user",
            password="password",
        )
        return SSHManager(logger=self.logger, config=ssh_config)

    def cleanup_vm(self, delete_storage: bool = False) -> None:
        """
        Stop and remove the container and optionally delete storage.

        Args:
            delete_storage: Whether to delete the storage directory.
        """
        if self.container:
            try:
                self.logger.info("Stopping container...")
                self.container.stop(timeout=30)
                self.logger.info("Removing container...")
                self.container.remove()
                self.logger.info("Container removed.")
            except Exception as e:
                self.logger.error(f"Error during VM cleanup: {e}")
            if delete_storage and self.config.storage_path:
                try:
                    if os.path.exists(self.config.storage_path):
                        self.logger.info(f"Deleting storage at {self.config.storage_path}...")
                        shutil.rmtree(self.config.storage_path)
                        self.logger.info("Storage deleted.")
                except Exception as e:
                    self.logger.error(f"Error deleting storage: {e}")


class SSHManager:
    """
    Manages SSH connections and operations.
    """

    def __init__(self, logger: Optional[logging.Logger] = None, config: Optional[SSHConfig] = None):
        self.logger = logger or self._setup_logger()
        self.config = config or SSHConfig()
        self.client = None
        self._sftp = None

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("SSHManager")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def connect(self, timeout: Optional[int] = None) -> paramiko.SSHClient:
        timeout = timeout or self.config.connect_timeout
        if self.client is not None:
            try:
                transport = self.client.get_transport()
                if transport and transport.is_active():
                    transport.send_ignore()
                    return self.client
                else:
                    self.close()
            except:
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

    def close(self):
        if self._sftp:
            try:
                self._sftp.close()
            except:
                pass
            self._sftp = None
        if self.client:
            self.client.close()
            self.client = None
            self.logger.info("SSH connection closed")

    @contextmanager
    def ssh_connection(self):
        try:
            client = self.connect()
            yield client
        finally:
            self.close()

    def get_sftp(self):
        if not self._sftp:
            client = self.connect()
            self._sftp = client.open_sftp()
        return self._sftp

    def transfer_directory(self, local_path: str, remote_path: str, skip_items: List[str] = None) -> bool:
        try:
            self.connect()
            local_path = os.path.abspath(local_path)
            skip_items = skip_items or []
            if not os.path.exists(local_path):
                self.logger.error(f"Local directory '{local_path}' not found")
                return False
            sftp = self.get_sftp()
            self.exec_command(f"mkdir -p {remote_path}")
            for root, dirs, files in os.walk(local_path):
                dirs[:] = [d for d in dirs if not any(skip in os.path.join(root, d) for skip in skip_items)]
                rel_path = os.path.relpath(root, local_path)
                rel_path = "" if rel_path == "." else rel_path
                remote_dir = os.path.join(remote_path, rel_path).replace("\\", "/")
                if rel_path:
                    try:
                        sftp.mkdir(remote_dir)
                    except IOError:
                        pass
                for file in files:
                    local_file = os.path.join(root, file)
                    if any(skip in local_file for skip in skip_items):
                        self.logger.debug(f"Skipping file: {local_file}")
                        continue
                    remote_file = os.path.join(remote_dir, file).replace("\\", "/")
                    sftp.put(local_file, remote_file)
                    self.logger.debug(f"Uploaded {local_file} -> {remote_file}")
            self.logger.info(f"Transferred {local_path} to {remote_path} (skipped: {skip_items})")
            return True
        except Exception as e:
            self.logger.error(f"Error transferring directory: {e}")
            return False

    def exec_command(self, command: str, directory: str = "", timeout: Optional[int] = None) -> Dict[str, Any]:
        timeout = timeout or self.config.command_timeout
        try:
            client = self.connect()
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

    def exec_template(self, template_name: str, **kwargs) -> Dict[str, Any]:
        templates = {
            "install_package": "apt-get update && apt-get install -y {package}",
            "create_user": "useradd -m {username}",
            "check_process": "ps aux | grep {process_name} | grep -v grep",
            "install_pip_package": "pip install {package}",
            "systemd_status": "systemctl status {service}",
            "file_permission": "chmod {mode} {file}",
        }
        template = templates.get(template_name)
        if not template:
            raise ValueError(f"Unknown template: {template_name}")
        command = template.format(**kwargs)
        return self.exec_command(command)

    def check_connection(self) -> bool:
        try:
            with self.ssh_connection():
                return True
        except Exception as e:
            self.logger.error(f"SSH connection check failed: {e}")
            return False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

# src/environment.py
"""
Environment Management for VM Containers with Dataclass Configuration

This module provides classes for managing Docker-based VM environments,
using dataclasses for configuration management.
"""

import dataclasses
import hashlib
import logging
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.parse
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

import paramiko
import psutil
import urllib3
from filelock import FileLock
from tqdm import tqdm

import docker

# BASED ON THE FOLLOWING DOCKER-COMPOSE FILE FOR RUNNING THE VM
# ```yaml
# THIS DOCKER-COMPOSE FILE IS FOR RUNNING MANUALLY TO SETUP THE INITIAL ENVIRONMENT
# Global VM resource settings
# Configuration can be found here: https://github.com/qemus/qemu
# services:
#   qemu:
#     image: qemux/qemu
#     container_name: qemu
#     environment:
#       BOOT: "ubuntu" # Default boot image
#       # BOOT: "https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/main/Ubuntu.qcow2.zip" # Downloads the Spider2-V image
#       DEBUG: "Y"
#       RAM_SIZE: "4G"
#       CPU_CORES: "4"
#       DISK_SIZE: "16G"
#     devices:
#       - /dev/kvm
#       - /dev/net/tun
#     cap_add:
#       - NET_ADMIN
#     ports:
#       - 8006:8006 # NoVNC
#       - 2222:22 # SSH
#     volumes:
#       - ${ROOT_DIR}/src/docker/vms/ubuntu:/storage
#     restart: always
#     stop_grace_period: 2m
# ```


@dataclasses.dataclass
class VMConfig:
    """Configuration settings for VM and container management."""

    # Paths and directories
    initial_vm_dir: str | Path = "docker/vms/ubuntu"  # Path to the initial vm dir, used for creating new images
    vm_environments: str | Path = "docker/environments"  # Path to the environments directory
    server_dir: str | Path = "server"  # Path to the interaction server directory
    vms_dir: str | Path = "docker/vms/"  # Base directory for virtuals machines

    # VM settings
    vm_os: str = os.getenv("VM_OS", "ubuntu")
    vm_disk_size: str = os.getenv("VM_DISK_SIZE", "16G")
    vm_ram_size: str = os.getenv("VM_RAM_SIZE", "4G")
    vm_cpu_cores: str = os.getenv("VM_CPU_CORES", "4")
    vm_debug: str = os.getenv("VM_DEBUG", "Y")
    # vm_boot_mode: str = os.getenv("BOOT_MODE", "uefi")  # uefi or legacy
    # vm_disk_type: str = os.getenv("DISK_TYPE", "scsi")  # scsi, blk, or ide

    # Timeouts
    download_timeout: int = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))
    vm_ready_timeout: int = int(os.getenv("VM_READY_TIMEOUT", "300"))
    container_stop_timeout: int = int(os.getenv("CONTAINER_STOP_TIMEOUT", "5"))

    # INITIAL PORT RANGES: EXPOSED BY THE CONTAINER, HAVE A LOOK AT THE INITIAL DOCKER-COMPOSE FILE
    # Default ports - using higher ports to avoid common conflicts
    default_port_ranges: Dict[str, int] = dataclasses.field(default_factory=lambda: {"vnc": 8006, "ssh": 2222})

    # Docker image settings
    # Image for using the QEMU VM setup
    default_vm_image: str = "qemux/qemu"
    default_hardlink_threshold: int = 100 * 1024 * 1024  # 100MB

    def get_lock_file_path(self) -> Path:
        """Get the appropriate path for lock files based on platform."""
        temp_dir = Path(os.getenv("TEMP") if platform.system() == "Windows" else "/tmp")
        return temp_dir / "docker_port_allocation.lck"

    @staticmethod
    def is_kvm_available() -> bool:
        """Check if KVM is available."""
        return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


# Custom exceptions
class SandboxError(Exception):
    """Base class for all sandbox-related exceptions."""

    pass


class PortAllocationError(SandboxError):
    """Raised when unable to find or allocate an available port."""

    pass


class VMImageError(SandboxError):
    """Raised when there's an error with VM image handling."""

    pass


class ContainerError(SandboxError):
    """Raised when there's an error with container operations."""

    pass


class NetworkError(SandboxError):
    """Raised when there's a network-related error."""

    pass


# Common utilities
def create_http_client(retries=3, connect_timeout=30.0, read_timeout=60.0, maxsize=10):
    """Create a properly configured HTTP client."""
    return urllib3.PoolManager(
        retries=urllib3.Retry(retries, redirect=2, backoff_factor=0.5),
        timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout),
        maxsize=maxsize,
    )


# Global HTTP client for reuse
http = create_http_client()


def is_url(path):
    """
    Check if the provided path is a valid URL with an allowed scheme.

    Args:
        path: The path or URL to check

    Returns:
        bool: True if it's a valid URL, False otherwise
    """
    if not isinstance(path, str):
        return False

    try:
        result = urllib.parse.urlparse(path)
        return all([result.scheme, result.netloc]) and result.scheme in ["http", "https", "ftp"]
    except Exception:
        return False


def verify_file_checksum(file_path, expected_checksum, logger):
    """
    Verify file integrity using SHA256 checksum.

    Args:
        file_path: Path to the file to verify
        expected_checksum: Expected SHA256 checksum
        logger: Logger instance

    Returns:
        bool: True if checksum matches, False otherwise
    """
    if not os.path.exists(file_path):
        logger.warning(f"File not found for checksum verification: {file_path}")
        return False

    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            # Read and update hash in chunks for memory efficiency
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)

        file_hash = sha256_hash.hexdigest()

        if file_hash != expected_checksum:
            logger.warning(f"Checksum verification failed for {file_path}")
            return False

        logger.info(f"Checksum verified for {file_path}")
        return True
    except Exception as e:
        logger.error(f"Error during checksum verification: {e}")
        return False


@contextmanager
def acquire_port_lock(lock_file):
    """
    Context manager for safely acquiring a file lock for port allocation.

    Args:
        lock_file: Path to the lock file

    Yields:
        None when lock is acquired

    Raises:
        PortAllocationError: If lock cannot be acquired
    """
    lock = FileLock(str(lock_file), timeout=10)
    try:
        lock.acquire()
        yield
    except Exception as e:
        raise PortAllocationError(f"Failed to acquire port allocation lock: {e}") from e
    finally:
        if lock.is_locked:
            lock.release()


class NetworkManager:
    """Manages network resources and port allocation."""

    def __init__(
        self,
        docker_client: docker.client,
        logger: logging.Logger,
        config: VMConfig,
    ):
        """
        Initialize the network manager.

        Args:
            docker_client: Docker client instance
            logger: Logger instancec
            config: VMConfig instance
            start_port_ranges: Dict with starting ports for different services
        """
        self.docker_client = docker_client
        self.logger = logger
        self.config = config

        # Initialize with higher port numbers to avoid conflicts with system services
        self.start_port_ranges = config.default_port_ranges

        self.lock_file = config.get_lock_file_path()

    def find_available_port(self, start_port, max_retries=100):
        """
        Find the next available port starting from start_port.

        Args:
            start_port: Starting port number to check from
            max_retries: Maximum number of ports to check before giving up

        Returns:
            int: Available port number

        Raises:
            PortAllocationError: If no available port is found
        """
        # Get system ports
        try:
            system_ports = {
                conn.laddr.port
                for conn in psutil.net_connections()
                if hasattr(conn, "laddr") and hasattr(conn.laddr, "port")
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess) as e:
            self.logger.warning(f"Limited access to network connections, port detection may be incomplete: {e}")
            system_ports = set()

        # Get Docker container ports
        docker_ports = set()
        try:
            for container in self.docker_client.containers.list():
                ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
                if ports:
                    for port_mappings in ports.values():
                        if port_mappings:
                            docker_ports.update(int(p["HostPort"]) for p in port_mappings if p and "HostPort" in p)
        except Exception as e:
            self.logger.warning(f"Error retrieving Docker ports: {e}")

        # Combine all used ports
        used_ports = system_ports | docker_ports

        # Log used ports in port range (for debugging)
        used_in_range = [p for p in used_ports if start_port <= p <= start_port + 100]
        if used_in_range:
            self.logger.debug(f"Used ports in search range: {used_in_range}")

        # Find an available port with a retry limit
        port = start_port
        retries = 0

        while retries < max_retries:
            if port not in used_ports:
                # Double-check port availability with a direct socket test
                if self._test_port_availability(port):
                    return port
                else:
                    self.logger.debug(f"Port {port} appears free but failed socket test")

            port += 1
            retries += 1

            # Avoid well-known ports
            if port < 1024:
                port = 1024

        raise PortAllocationError(f"No available ports found after {max_retries} attempts starting from {start_port}")

    def _test_port_availability(self, port):
        """
        Test if a port is truly available by attempting to bind a socket.

        Args:
            port: Port number to test

        Returns:
            bool: True if port is available, False otherwise
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = False

        try:
            # Try to bind to the port
            sock.bind(("0.0.0.0", port))
            result = True
        except OSError:
            # Port is in use
            result = False
        finally:
            sock.close()

        return result

    def allocate_ports(self):
        """
        Safely allocate all required ports for a VM container.

        Returns:
            dict: Dictionary with allocated ports

        Raises:
            PortAllocationError: If port allocation fails
        """
        with acquire_port_lock(self.lock_file):
            try:
                ports = {}
                for name, start_port in self.start_port_ranges.items():
                    # Try to allocate the port, with retries
                    try:
                        allocated_port = self.find_available_port(start_port)
                        ports[name] = allocated_port
                        # Update starting port for next allocation
                        self.start_port_ranges[name] = allocated_port + 1
                    except PortAllocationError as e:
                        # If we can't find a port starting from the default,
                        # try with a higher range
                        self.logger.warning(f"Port allocation failed for {name}: {e}")
                        new_start = start_port + 1000  # Try a much higher range
                        self.logger.info(f"Retrying with higher port range starting at {new_start}")
                        allocated_port = self.find_available_port(new_start)
                        ports[name] = allocated_port
                        self.start_port_ranges[name] = allocated_port + 1

                self.logger.info(f"Allocated ports: {ports}")
                return ports
            except Exception as e:
                self.logger.error(f"Port allocation failed: {e}")
                raise PortAllocationError(f"Failed to allocate ports: {e}") from e

    def check_websocket_server(self, server_port, container, retry_count, max_retries):
        """
        Check if WebSocket server is available on the specified port.

        Args:
            server_port: Port number to check
            container: Docker container instance
            retry_count: Current retry count
            max_retries: Maximum number of retries

        Returns:
            tuple: (updated_retry_count, success_status)

        Raises:
            NetworkError: If container health check fails
        """
        status_http = create_http_client(retries=1, connect_timeout=5.0, read_timeout=5.0)

        try:
            # Try to perform a WebSocket handshake by sending an HTTP request with upgrade headers
            headers = {
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",  # Dummy key
                "Sec-WebSocket-Version": "13",
            }

            response = status_http.request(
                "GET",
                f"http://localhost:{server_port}",
                headers=headers,
                timeout=urllib3.Timeout(connect=5.0, read=5.0),
            )

            # WebSocket handshake should return 101 Switching Protocols
            # But we'll also accept any 2xx or 3xx response
            if response.status < 400:
                self.logger.debug(f"WebSocket server responded with status: {response.status}")
                return 0, True  # Reset retry count on success

            self.logger.debug(f"WebSocket server check returned status: {response.status}")
            return retry_count, False
        except Exception as e:
            self.logger.debug(f"WebSocket server check failed: {e}")
            retry_count += 1

            # If we've had too many consecutive failures, check if container is still running
            if retry_count > max_retries:
                try:
                    container.reload()
                    if container.status != "running":
                        raise NetworkError(
                            f"Container stopped running during VM initialization. Status: {container.status}"
                        )
                except Exception as container_error:
                    self.logger.error(f"Container health check failed: {container_error}")
                    raise NetworkError(f"Container health check failed: {container_error}") from container_error

            return retry_count, False
        finally:
            status_http.clear()

    def wait_for_vm_ready(self, server_port, container, timeout=None):
        """
        Wait for VM to be ready by checking WebSocket server availability.

        Args:
            server_port: Port number to check
            container: Docker container instance
            timeout: Maximum time to wait in seconds

        Returns:
            bool: True if VM is ready

        Raises:
            TimeoutError: If VM is not ready within the timeout period
            NetworkError: If container health check fails
        """
        timeout = timeout or self.config.vm_ready_timeout

        start_time = time.time()
        last_log_time = start_time
        retry_count = 0
        max_retries = 3  # Number of consecutive failures to tolerate

        try:
            while time.time() - start_time < timeout:
                current_time = time.time()
                retry_count, success = self.check_websocket_server(server_port, container, retry_count, max_retries)

                if success:
                    self.logger.info(f"VM WebSocket server is ready! Total wait time: {current_time - start_time:.2f}s")
                    return True

                if current_time - last_log_time >= 10:
                    self.logger.info(
                        f"Checking if WebSocket server is ready... ({current_time - start_time:.2f}s elapsed)"
                    )
                    last_log_time = current_time

                time.sleep(1)

            raise TimeoutError(f"WebSocket server failed to become ready within {timeout}s timeout period")
        except Exception as e:
            self.logger.error(f"Error waiting for WebSocket server to be ready: {e}")
            if isinstance(e, TimeoutError):
                raise
            raise NetworkError(f"Error waiting for WebSocket server: {e}") from e


class DockerManager:
    """Manages Docker image and container operations."""

    def __init__(self, docker_client: docker.client, logger: logging.Logger, config: VMConfig):
        """
        Initialize the Docker manager.

        Args:
            docker_client: Docker client instance
            logger: Logger instance
            config: VMConfig instance
        """
        self.docker_client = docker_client
        self.logger = logger
        self.config = config

    def ensure_image_exists(self, image_name):
        """
        Check if a Docker image exists locally, pull if not.

        Args:
            image_name: Name of the image to check

        Raises:
            ContainerError: If image pull fails
        """
        try:
            self.docker_client.images.get(image_name)
            self.logger.info(f"Image {image_name} found locally")
        except docker.errors.ImageNotFound:
            self.logger.info(f"Image {image_name} not found locally, pulling...")
            try:
                self.docker_client.images.pull(image_name)
                self.logger.info(f"Successfully pulled image: {image_name}")
            except Exception as e:
                self.logger.error(f"Error pulling image {image_name}: {e}")
                raise ContainerError(f"Failed to pull Docker image: {e}") from e

    def create_container(self, name, image_name, environment, volumes, ports, caps=None, devices=None, shm_size="512m"):
        """
        Create a new Docker container.

        Args:
            name: Name for the container
            image_name: Docker image name
            environment: Environment variables
            volumes: Volume mappings
            ports: Port mappings
            caps: Linux capabilities to add
            devices: Devices to expose
            shm_size: Shared memory size

        Returns:
            docker.models.containers.Container: Created container

        Raises:
            ContainerError: If container creation fails
        """
        try:
            self.logger.info(f"Creating container '{name}' from image '{image_name}'")

            # Ensure image exists
            self.ensure_image_exists(image_name)

            # Create the container without starting it
            container = self.docker_client.containers.create(
                image=image_name,
                name=name,
                environment=environment,
                cap_add=caps or ["NET_ADMIN"],
                devices=devices or ["/dev/kvm", "/dev/net/tun"] if VMConfig.is_kvm_available() else None,
                volumes=volumes,
                ports=ports,
                shm_size=shm_size,
                detach=True,
            )

            self.logger.info(f"Successfully created container: {name}")
            return container

        except Exception as e:
            self.logger.error(f"Error creating container: {e}")
            raise ContainerError(f"Failed to create container: {e}") from e

    def get_existing_container(self, name):
        """
        Get an existing container by name.

        Args:
            name: Container name

        Returns:
            docker.models.containers.Container or None: Container if found
        """
        try:
            return self.docker_client.containers.get(name)
        except docker.errors.NotFound:
            return None

    def start_container(self, container):
        """
        Start a container and verify it's running.

        Args:
            container: Docker container instance

        Raises:
            ContainerError: If container cannot be started
        """
        try:
            container.start()
            # Verify container started successfully
            container.reload()
            if container.status != "running":
                raise ContainerError(f"Container could not be started, status: {container.status}")
            self.logger.info(f"Container started: {container.name}")
        except Exception as e:
            self.logger.error(f"Error starting container: {e}")
            raise ContainerError(f"Failed to start container: {e}") from e

    def stop_container(self, container, timeout=None, remove=False):
        """
        Stop a container.

        Args:
            container: Docker container instance
            timeout: Timeout for stopping in seconds
            remove: Whether to remove the container after stopping

        Returns:
            bool: True if successful

        Raises:
            ContainerError: If container cannot be stopped
        """
        if not container:
            return True  # Nothing to stop

        timeout = timeout or self.config.container_stop_timeout

        try:
            container.stop(timeout=timeout)
            self.logger.info(f"Stopped container: {container.name}")

            # Add a small delay to ensure container has fully stopped
            time.sleep(0.5)

            if remove:
                container.remove()
                self.logger.info(f"Removed container: {container.name}")

            return True
        except Exception as e:
            self.logger.error(f"Error stopping container: {e}")
            raise ContainerError(f"Failed to stop container: {e}") from e

    def remove_container(self, container, force=True):
        """
        Remove a container.

        Args:
            container: Docker container instance
            force: Whether to force removal

        Returns:
            bool: True if successful

        Raises:
            ContainerError: If container cannot be removed
        """
        if not container:
            return True  # Nothing to remove

        try:
            container.remove(force=force, v=True)  # v=True to remove volumes
            self.logger.info(f"Removed container: {container.name}")
            return True
        except docker.errors.NotFound:
            self.logger.info(f"Container already removed: {container.name}")
            return True
        except Exception as e:
            self.logger.error(f"Error removing container: {e}")
            raise ContainerError(f"Failed to remove container: {e}") from e


class FileManager:
    """Manages file operations including optimized copying and downloads."""

    def __init__(self, logger: logging.Logger, config: VMConfig):
        """
        Initialize the file manager.

        Args:
            logger: Logger instance
            config: VMConfig instance
        """
        self.logger = logger
        self.config = config

    def optimized_copy(self, src_dir, dst_dir, hardlink_threshold=None):
        """
        Perform an optimized copy of files using hardlinks when possible.

        Args:
            src_dir: Source directory
            dst_dir: Destination directory
            hardlink_threshold: Size threshold for using hardlinks (bytes)

        Returns:
            bool: True if successful

        Raises:
            VMImageError: If copy fails
        """
        if not os.path.exists(src_dir):
            raise VMImageError(f"Source directory not found: {src_dir}")

        hardlink_threshold = hardlink_threshold or self.config.default_hardlink_threshold
        self.logger.info(f"Starting optimized copy from {src_dir} to {dst_dir}")

        try:
            # Get list of files to copy
            items = os.listdir(src_dir)
            total_items = len(items)

            with tqdm(total=total_items, desc="Copying VM files", unit="files") as pbar:
                for item in items:
                    src_path = os.path.join(src_dir, item)
                    dst_path = os.path.join(dst_dir, item)

                    # Skip if file/directory already exists
                    if os.path.exists(dst_path):
                        pbar.update(1)
                        continue

                    if os.path.isfile(src_path):
                        # Try to create hardlink first (much faster than copy)
                        try:
                            file_size = os.path.getsize(src_path)
                            if file_size > hardlink_threshold:
                                self.logger.debug(
                                    f"Creating hardlink for large file: {item} ({file_size / (1024 * 1024):.2f} MB)"
                                )
                                # For large files, use hardlinks if possible
                                os.link(src_path, dst_path)
                            else:
                                # For smaller files, do a regular copy
                                shutil.copy2(src_path, dst_path)
                        except OSError:
                            # Fallback to regular copy if hardlink fails
                            self.logger.debug(f"Hardlink failed for {item}, using regular copy")
                            shutil.copy2(src_path, dst_path)
                    elif os.path.isdir(src_path):
                        # For directories, we need to do a recursive copy
                        os.makedirs(dst_path, exist_ok=True)
                        # Recursive copy, but handle each file individually
                        for root, dirs, files in os.walk(src_path):
                            # Create all subdirectories
                            for d in dirs:
                                src_subdir = os.path.join(root, d)
                                rel_path = os.path.relpath(src_subdir, src_path)
                                dst_subdir = os.path.join(dst_path, rel_path)
                                os.makedirs(dst_subdir, exist_ok=True)

                            # Copy all files, using hardlinks for large files
                            for f in files:
                                src_file = os.path.join(root, f)
                                rel_path = os.path.relpath(src_file, src_path)
                                dst_file = os.path.join(dst_path, rel_path)

                                # Create parent directories if they don't exist
                                os.makedirs(os.path.dirname(dst_file), exist_ok=True)

                                try:
                                    # Check file size
                                    file_size = os.path.getsize(src_file)
                                    if file_size > hardlink_threshold:
                                        # Use hardlink for large files
                                        os.link(src_file, dst_file)
                                    else:
                                        # Regular copy for smaller files
                                        shutil.copy2(src_file, dst_file)
                                except OSError:
                                    # Fallback to regular copy
                                    shutil.copy2(src_file, dst_file)

                    pbar.update(1)

            self.logger.info(f"Optimized copy completed: {total_items} items processed")
            return True

        except Exception as e:
            self.logger.error(f"Error during optimized copy: {e}")
            raise VMImageError(f"Failed to copy files: {e}") from e

    def download_file(self, url, destination_dir, retries=3):
        """
        Download a file with resume capability.

        Args:
            url: URL to download from
            destination_dir: Directory to save the file
            retries: Number of retry attempts

        Returns:
            str: Path to the downloaded file

        Raises:
            VMImageError: If download fails
        """
        if not is_url(url):
            raise VMImageError(f"Invalid URL: {url}")

        try:
            # Prepare for download
            downloaded_file_name = url.split("/")[-1]
            downloaded_file_path = os.path.join(destination_dir, downloaded_file_name)
            os.makedirs(destination_dir, exist_ok=True)

            downloaded_size = 0
            retry_count = 0

            while retry_count <= retries:
                headers = {}
                if os.path.exists(downloaded_file_path):
                    downloaded_size = os.path.getsize(downloaded_file_path)
                    headers["Range"] = f"bytes={downloaded_size}-"

                response = None
                try:
                    # Try to download the file
                    response = http.request(
                        "GET",
                        url,
                        headers=headers,
                        preload_content=False,  # Don't read data into memory yet
                    )

                    # Handle Range Not Satisfiable (416) case
                    if response.status == 416:
                        self.logger.info("File already fully downloaded.")
                        break

                    # Check for errors
                    if response.status >= 400:
                        raise VMImageError(f"HTTP Error: {response.status} for URL: {url}")

                    # Get content length for progress tracking
                    total_size = int(response.headers.get("content-length", 0))

                    with open(downloaded_file_path, "ab") as file:
                        with tqdm(
                            desc=f"Downloading {downloaded_file_name}",
                            total=total_size,
                            unit="iB",
                            unit_scale=True,
                            unit_divisor=1024,
                            initial=downloaded_size,
                            ascii=True,
                        ) as progress_bar:
                            try:
                                for chunk in response.stream(8192):
                                    size = file.write(chunk)
                                    progress_bar.update(size)
                                self.logger.info("Download completed successfully.")
                                # Break out of the retry loop on success
                                break
                            except Exception as e:
                                self.logger.error(f"Download error: {e}")
                                retry_count += 1
                                if retry_count <= retries:
                                    time.sleep(5)
                                    self.logger.info(f"Retrying download (attempt {retry_count}/{retries})...")
                                else:
                                    raise VMImageError(f"Download failed after {retries} attempts: {e}") from e
                except Exception as e:
                    self.logger.error(f"Error during download: {e}")
                    retry_count += 1
                    if retry_count <= retries:
                        time.sleep(5)
                        self.logger.info(f"Retrying download (attempt {retry_count}/{retries})...")
                    else:
                        raise VMImageError(f"Failed to download file after {retries} attempts: {e}") from e
                finally:
                    # Make sure to release resources even if an exception occurs
                    if response is not None:
                        response.release_conn()

            return downloaded_file_path

        except Exception as e:
            self.logger.error(f"Error downloading file: {e}")
            raise VMImageError(f"Failed to download file: {e}") from e

    def extract_archive(self, archive_path, extract_dir=None):
        """
        Extract a zip or tar archive.

        Args:
            archive_path: Path to the archive file
            extract_dir: Directory to extract to (defaults to archive's directory)

        Returns:
            str: Path to the extracted contents

        Raises:
            VMImageError: If extraction fails
        """
        extract_dir = extract_dir or os.path.dirname(archive_path)

        # List of supported disk image formats
        disk_formats = [".qcow2", ".vmdk", ".vhd", ".vhdx", ".vdi", ".img", ".raw", ".iso"]
        compression_formats = [".gz", ".xz", ".zip", ".tar", ".tar.gz", ".tgz"]

        # If this is a standard disk image format, we don't need to extract it
        if any(archive_path.lower().endswith(ext) for ext in disk_formats):
            self.logger.info(f"Using disk image directly: {archive_path}")
            return os.path.dirname(archive_path)

        # If this is a compressed disk image (e.g., .img.gz), QEMU can handle it directly
        for disk_format in disk_formats:
            for comp_format in compression_formats:
                if archive_path.lower().endswith(f"{disk_format}{comp_format}"):
                    self.logger.info(f"Using compressed disk image directly (QEMU will extract): {archive_path}")
                    return os.path.dirname(archive_path)

        # For regular archives that need extraction
        try:
            if archive_path.endswith(".zip"):
                self.logger.info(f"Extracting ZIP archive: {archive_path}")
                with zipfile.ZipFile(archive_path, "r") as zip_ref:
                    zip_ref.extractall(extract_dir)

            elif any(archive_path.endswith(ext) for ext in [".tar", ".tar.gz", ".tgz"]):
                self.logger.info(f"Extracting TAR archive: {archive_path}")
                with tarfile.open(archive_path, "r:*") as tar_ref:
                    tar_ref.extractall(extract_dir)

            else:
                # If we're here, it's not a recognized format for extraction
                self.logger.warning(f"Unrecognized archive format, QEMU may handle it: {archive_path}")
                return os.path.dirname(archive_path)  # Return directory anyway as QEMU might handle it

            self.logger.info(f"Archive extracted to: {extract_dir}")

            # Look for disk images in the extracted files
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    if any(file.lower().endswith(ext) for ext in disk_formats):
                        disk_path = os.path.join(root, file)
                        self.logger.info(f"Found disk image in extracted archive: {disk_path}")
                        # If we find a disk image in a subdirectory, we should return that directory
                        if root != extract_dir:
                            return root
                        break

            return extract_dir

        except Exception as e:
            self.logger.error(f"Error extracting archive: {e}")
            raise VMImageError(f"Failed to extract archive: {e}") from e


class Environment:
    """Manages VM environments, providing creation, starting, and stopping of VMs."""

    def __init__(
        self, config: Optional[VMConfig] = None, docker_client: docker.client = None, logger: logging.Logger = None
    ):
        """
        Initialize the environment for creating VM instances.

        Args:
            config: VMConfig instance for configuration
            docker_client: Docker client instance
            logger: Logger instance
        """
        # Use provided config or create default
        self.config = config or VMConfig()
        # Convert relative paths to absolute paths
        self.config.initial_vm_dir = os.path.abspath(self.config.initial_vm_dir)
        self.config.vm_environments = os.path.abspath(self.config.vm_environments)
        self.config.server_dir = os.path.abspath(self.config.server_dir)
        self.config.vms_dir = os.path.abspath(self.config.vms_dir)

        self.docker_client = docker_client or docker.from_env()
        self.logger = logger or logging.getLogger(__name__)

        # Set initial properties from config
        self.vm_os = self.config.vm_os
        self.initial_vm_dir = self.config.initial_vm_dir
        self.instance_counter = 0  # tracks how many VMs were started

        # Initialize managers with the config
        self.docker_manager = DockerManager(self.docker_client, self.logger, self.config)
        self.network_manager = NetworkManager(self.docker_client, self.logger, self.config)
        self.file_manager = FileManager(self.logger, self.config)

        # Ensure directories exist
        os.makedirs(self.config.vm_environments, exist_ok=True)
        os.makedirs(self.config.vms_dir, exist_ok=True)

        # Validate initial VM volume directory
        if not os.path.exists(self.initial_vm_dir):
            self.logger.warning(f"Initial VM volume directory not found: {self.initial_vm_dir}")

    def _get_instance_dir(self):
        """
        Create and return an absolute path for a new VM instance.
        Uses a more Docker-friendly naming convention.
        """
        # Use a simple incrementing counter and avoid slashes
        instance_counter = len(os.listdir(self.config.vm_environments)) + 1
        instance_dir = os.path.join(self.config.vm_environments, f"ubuntu_environment_{instance_counter}")
        os.makedirs(instance_dir, exist_ok=True)
        return instance_dir

    def prepare_vm_image(self, boot_source=None):
        """
        Prepare VM image from URL or local source.

        Args:
            boot_source: URL or path to boot image (None uses default)

        Returns:
            str: Path to prepared VM image directory
        """
        # If no boot source specified, use initial VM volume
        if not boot_source:
            if not os.path.exists(self.initial_vm_dir) or not os.listdir(self.initial_vm_dir):
                raise VMImageError(f"Initial VM volume directory is empty or not found: {self.initial_vm_dir}")
            return self.initial_vm_dir

        # If URL, download the image
        if is_url(boot_source):
            self.logger.info(f"Downloading VM image from: {boot_source}")

            # Create a more useful directory name based on the URL
            url_path = urllib.parse.urlparse(boot_source).path
            filename = os.path.basename(url_path)
            base_name = os.path.splitext(filename)[0]

            # Clean up the name (remove special characters)
            clean_name = "".join(c if c.isalnum() or c in ["-", "_"] else "_" for c in base_name)

            # Use timestamp to ensure uniqueness
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            vm_name = f"{clean_name}_{timestamp}"

            # Create the download directory with the useful name
            download_dir = os.path.join(self.config.vms_dir, vm_name)
            os.makedirs(download_dir, exist_ok=True)

            # Download the file
            downloaded_file = self.file_manager.download_file(boot_source, download_dir)

            # Extract if it's an archive
            if any(downloaded_file.endswith(ext) for ext in [".zip", ".tar", ".tar.gz", ".tgz"]):
                self.logger.info(f"Extracting archive to {download_dir}")
                self.file_manager.extract_archive(downloaded_file, download_dir)

                # Check if extraction created a subdirectory
                extracted_dirs = [
                    d
                    for d in os.listdir(download_dir)
                    if os.path.isdir(os.path.join(download_dir, d)) and d != os.path.basename(downloaded_file)
                ]

                if extracted_dirs and len(extracted_dirs) == 1:
                    # If there's just one subdirectory, use that as our VM directory
                    extracted_dir = os.path.join(download_dir, extracted_dirs[0])
                    self.logger.info(f"Using extracted directory: {extracted_dir}")
                    return extracted_dir

                return download_dir
            else:
                self.logger.info(f"Using downloaded file at: {download_dir}")
                return download_dir

        # If path, validate it exists
        elif os.path.exists(boot_source):
            return boot_source

        # If relative path, check in volume directory
        else:
            full_path = os.path.join(self.config.vms_dir, boot_source)
            if os.path.exists(full_path):
                return full_path
            else:
                raise VMImageError(f"VM image path not found: {boot_source}")

    def start_vm(self, boot_source=None, name_prefix: str = "vm"):
        """
        Start a new VM instance with proper port allocation.

        Args:
            boot_source: URL or path to boot image (None uses default)
            name_prefix: Prefix for container name (default: qemu_{vm_os}_)

        Returns:
            tuple: (container, connection_info)

        Raises:
            ContainerError: If container creation fails
            PortAllocationError: If port allocation fails
        """
        try:
            # Get an absolute instance directory path
            instance_dir = self._get_instance_dir()

            # Prepare VM image (this method uses absolute paths)
            if boot_source:
                source_dir = self.prepare_vm_image(boot_source)
                # Copy from source to instance directory using absolute paths
                self.file_manager.optimized_copy(source_dir, instance_dir)
            else:
                # Use default VM image with absolute paths
                self.file_manager.optimized_copy(self.initial_vm_dir, instance_dir)

            # Allocate available ports using NetworkManager
            ports_dict = self.network_manager.allocate_ports()

            # Define environment variables
            environment = {
                "BOOT": "ubuntu",  # using copied files instead of a downloaded image
                "DEBUG": self.config.vm_debug,
                "RAM_SIZE": self.config.vm_ram_size,
                "CPU_CORES": self.config.vm_cpu_cores,
                "DISK_SIZE": self.config.vm_disk_size,
                # "BOOT_MODE": self.config.vm_boot_mode,
                # "DISK_TYPE": self.config.vm_disk_type,
            }

            # Map the new instance_dir as volume to /storage in container
            volumes = {instance_dir: {"bind": "/storage", "mode": "rw"}, shared_dir: {"bind": "/shared", "mode": "rw"}}

            # Create unique container name
            container_name = f"{name_prefix}_{self.vm_os}_{self.instance_counter}"

            # Map ports
            host_vnc = ports_dict.get("vnc")
            host_ssh = ports_dict.get("ssh")

            ports = {
                8006: host_vnc,  # VNC port
                22: host_ssh,  # SSH port
            }

            # Add server port mapping if available
            if "server" in ports_dict:
                server_port = ports_dict.get("server")
                ports[8765] = server_port  # WebSocket server port

            # Create container
            container = self.docker_manager.create_container(
                name=container_name,
                image_name=self.config.default_vm_image,
                environment=environment,
                volumes=volumes,
                ports=ports,
                caps=["NET_ADMIN"],
                devices=["/dev/kvm", "/dev/net/tun"] if VMConfig.is_kvm_available() else None,
            )

            # Start the container
            self.docker_manager.start_container(container)

            # Wait for VM to be ready if server port is allocated
            if "server" in ports_dict:
                server_port = ports_dict.get("server")
                self.network_manager.wait_for_vm_ready(server_port, container)

            # Return container and connection info
            connection_info = {
                "container_name": container_name,
                "host_vnc": host_vnc,
                "ssh_port": host_ssh,
                "instance_dir": instance_dir,
            }

            if "server" in ports_dict:
                connection_info["server_port"] = ports_dict.get("server")

            self.logger.info(f"VM started successfully: {connection_info}")
            return container, connection_info

        except Exception as e:
            self.logger.error(f"Failed to start VM: {e}")
            # Clean up resources if startup fails
            if "container" in locals() and container:
                try:
                    self.docker_manager.stop_container(container, remove=True)
                except Exception as cleanup_error:
                    self.logger.error(f"Error during cleanup: {cleanup_error}")

            if "instance_dir" in locals() and os.path.exists(instance_dir):
                try:
                    # Consider keeping instance directory for debugging
                    pass
                except Exception as cleanup_error:
                    self.logger.error(f"Error cleaning up instance directory: {cleanup_error}")

            # Re-raise the original exception
            raise

    def create_and_setup_sandbox(self, name=None, server_dir=None):
        """
        Create a sandbox environment from the base VM image, SSH into it,
        copy the server files, and start the server.

        Args:
            name: Optional name for the sandbox environment
            server_dir: Path to the server directory to copy to the VM

        Returns:
            dict: Information about the sandbox environment including connection details
        """
        try:
            # Use provided server directory or default from config
            server_dir = os.path.abspath(server_dir or self.config.server_dir)

            # Validate server directory exists
            if not os.path.exists(server_dir):
                raise SandboxError(f"Server directory not found: {server_dir}")

            # Start the VM
            name_prefix = f"sandbox_{name}_" if name else "sandbox_"
            container, connection_info = self.start_vm(name_prefix=name_prefix)

            # Extract SSH port from connection info
            ssh_port = connection_info.get("ssh_port")
            if not ssh_port:
                raise SandboxError("SSH port was not allocated properly")

            # Wait for SSH to be available (VM might still be booting)
            self._wait_for_ssh("localhost", ssh_port, "sandbox-user", "password", timeout=60)

            # Set up SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            try:
                # Connect to the VM
                ssh.connect("localhost", port=ssh_port, username="sandbox-user", password="password")
                self.logger.info(f"SSH connection established to sandbox at localhost:{ssh_port}")

                # Copy server directory to VM using SCP
                self._scp_directory(server_dir, "sandbox-user@localhost:/home/sandbox-user/server", ssh_port)

                # Navigate to server directory and run commands
                commands = [
                    "cd /home/sandbox-user/server",
                    "uv sync",
                    "nohup uv run main.py > /dev/null 2>&1 &",  # Run in background, properly detached
                ]

                # Execute setup commands
                for cmd in commands:
                    stdin, stdout, stderr = ssh.exec_command(cmd)
                    exit_status = stdout.channel.recv_exit_status()

                    if exit_status != 0:
                        stderr_output = stderr.read().decode("utf-8")
                        self.logger.warning(
                            f"Command '{cmd}' returned non-zero exit code {exit_status}: {stderr_output}"
                        )
                    else:
                        self.logger.info(f"Successfully executed: {cmd}")

                # Add server status to connection info
                connection_info["server_status"] = "started"

                return connection_info

            except Exception as e:
                self.logger.error(f"Error setting up server in VM: {e}")
                raise SandboxError(f"Failed to set up server in VM: {e}") from e
            finally:
                ssh.close()

        except Exception as e:
            self.logger.error(f"Error creating sandbox environment: {e}")
            # Clean up if needed
            if "container" in locals() and container:
                self.stop_vm(container, remove_instance=False)  # Keep instance for debugging
            raise SandboxError(f"Failed to create sandbox environment: {e}") from e


# src/environment.py
"""
Environment Management for VM Containers with Dataclass Configuration

This module provides classes for managing Docker-based VM environments,
using dataclasses for configuration management.
"""

import dataclasses
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

import docker

# BASED ON THE FOLLOWING DOCKER-COMPOSE FILE FOR RUNNING THE VM
# ```yaml
# THIS DOCKER-COMPOSE FILE IS FOR RUNNING MANUALLY TO SETUP THE INITIAL ENVIRONMENT
# Global VM resource settings
# Configuration can be found here: https://github.com/qemus/qemu
# services:
#   qemu:
#     image: qemux/qemu
#     container_name: qemu
#     environment:
#       BOOT: "ubuntu" # Default boot image
#       # BOOT: "https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/main/Ubuntu.qcow2.zip" # Downloads the Spider2-V image
#       DEBUG: "Y"
#       RAM_SIZE: "4G"
#       CPU_CORES: "4"
#       DISK_SIZE: "16G"
#     devices:
#       - /dev/kvm
#       - /dev/net/tun
#     cap_add:
#       - NET_ADMIN
#     ports:
#       - 8006:8006 # NoVNC
#       - 2222:22 # SSH
#     volumes:
#       - ${ROOT_DIR}/src/docker/vms/ubuntu:/storage
#     restart: always
#     stop_grace_period: 2m
# ```


@dataclasses.dataclass
class VMConfig:
    """Configuration settings for VM and container management."""

    # Paths and directories
    initial_vm_dir: str | Path = "docker/vms/ubuntu"  # Path to the initial vm dir, used for creating new images
    vm_environments: str | Path = "docker/environments"  # Path to the environments directory
    server_dir: str | Path = "server"  # Path to the interaction server directory
    vms_dir: str | Path = "docker/vms/"  # Base directory for virtuals machines

    # VM settings
    vm_os: str = os.getenv("VM_OS", "ubuntu")
    vm_disk_size: str = os.getenv("VM_DISK_SIZE", "16G")
    vm_ram_size: str = os.getenv("VM_RAM_SIZE", "4G")
    vm_cpu_cores: str = os.getenv("VM_CPU_CORES", "4")
    vm_debug: str = os.getenv("VM_DEBUG", "Y")
    vm_boot_mode: str = os.getenv("BOOT_MODE", "uefi")  # uefi or legacy
    vm_disk_type: str = os.getenv("DISK_TYPE", "scsi")  # scsi, blk, or ide

    # Timeouts
    download_timeout: int = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))
    vm_ready_timeout: int = int(os.getenv("VM_READY_TIMEOUT", "300"))
    container_stop_timeout: int = int(os.getenv("CONTAINER_STOP_TIMEOUT", "5"))

    # INITIAL PORT RANGES: EXPOSED BY THE CONTAINER, HAVE A LOOK AT THE INITIAL DOCKER-COMPOSE FILE
    # Default ports - using higher ports to avoid common conflicts
    default_port_ranges: Dict[str, int] = dataclasses.field(default_factory=lambda: {"vnc": 8006, "ssh": 2222})

    # Docker image settings
    # Image for using the QEMU VM setup
    default_vm_image: str = "qemux/qemu"
    default_hardlink_threshold: int = 100 * 1024 * 1024  # 100MB

    def get_lock_file_path(self) -> Path:
        """Get the appropriate path for lock files based on platform."""
        temp_dir = Path(os.getenv("TEMP") if platform.system() == "Windows" else "/tmp")
        return temp_dir / "docker_port_allocation.lck"

    @staticmethod
    def is_kvm_available() -> bool:
        """Check if KVM is available."""
        return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


# Custom exceptions
class SandboxError(Exception):
    """Base class for all sandbox-related exceptions."""

    pass


class PortAllocationError(SandboxError):
    """Raised when unable to find or allocate an available port."""

    pass


class VMImageError(SandboxError):
    """Raised when there's an error with VM image handling."""

    pass


class ContainerError(SandboxError):
    """Raised when there's an error with container operations."""

    pass


class NetworkError(SandboxError):
    """Raised when there's a network-related error."""

    pass


# Common utilities
def create_http_client(retries=3, connect_timeout=30.0, read_timeout=60.0, maxsize=10):
    """Create a properly configured HTTP client."""
    return urllib3.PoolManager(
        retries=urllib3.Retry(retries, redirect=2, backoff_factor=0.5),
        timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout),
        maxsize=maxsize,
    )


# Global HTTP client for reuse
http = create_http_client()


def is_url(path):
    """
    Check if the provided path is a valid URL with an allowed scheme.

    Args:
        path: The path or URL to check

    Returns:
        bool: True if it's a valid URL, False otherwise
    """
    if not isinstance(path, str):
        return False

    try:
        result = urllib.parse.urlparse(path)
        return all([result.scheme, result.netloc]) and result.scheme in ["http", "https", "ftp"]
    except Exception:
        return False


def verify_file_checksum(file_path, expected_checksum, logger):
    """
    Verify file integrity using SHA256 checksum.

    Args:
        file_path: Path to the file to verify
        expected_checksum: Expected SHA256 checksum
        logger: Logger instance

    Returns:
        bool: True if checksum matches, False otherwise
    """
    if not os.path.exists(file_path):
        logger.warning(f"File not found for checksum verification: {file_path}")
        return False

    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            # Read and update hash in chunks for memory efficiency
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)

        file_hash = sha256_hash.hexdigest()

        if file_hash != expected_checksum:
            logger.warning(f"Checksum verification failed for {file_path}")
            return False

        logger.info(f"Checksum verified for {file_path}")
        return True
    except Exception as e:
        logger.error(f"Error during checksum verification: {e}")
        return False


@contextmanager
def acquire_port_lock(lock_file):
    """
    Context manager for safely acquiring a file lock for port allocation.

    Args:
        lock_file: Path to the lock file

    Yields:
        None when lock is acquired

    Raises:
        PortAllocationError: If lock cannot be acquired
    """
    lock = FileLock(str(lock_file), timeout=10)
    try:
        lock.acquire()
        yield
    except Exception as e:
        raise PortAllocationError(f"Failed to acquire port allocation lock: {e}") from e
    finally:
        if lock.is_locked:
            lock.release()


class NetworkManager:
    """Manages network resources and port allocation."""

    def __init__(
        self,
        docker_client: docker.client,
        logger: logging.Logger,
        config: VMConfig,
    ):
        """
        Initialize the network manager.

        Args:
            docker_client: Docker client instance
            logger: Logger instancec
            config: VMConfig instance
            start_port_ranges: Dict with starting ports for different services
        """
        self.docker_client = docker_client
        self.logger = logger
        self.config = config

        # Initialize with higher port numbers to avoid conflicts with system services
        self.start_port_ranges = config.default_port_ranges

        self.lock_file = config.get_lock_file_path()

    def find_available_port(self, start_port, max_retries=100):
        """
        Find the next available port starting from start_port.

        Args:
            start_port: Starting port number to check from
            max_retries: Maximum number of ports to check before giving up

        Returns:
            int: Available port number

        Raises:
            PortAllocationError: If no available port is found
        """
        # Get system ports
        try:
            system_ports = {
                conn.laddr.port
                for conn in psutil.net_connections()
                if hasattr(conn, "laddr") and hasattr(conn.laddr, "port")
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess) as e:
            self.logger.warning(f"Limited access to network connections, port detection may be incomplete: {e}")
            system_ports = set()

        # Get Docker container ports
        docker_ports = set()
        try:
            for container in self.docker_client.containers.list():
                ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
                if ports:
                    for port_mappings in ports.values():
                        if port_mappings:
                            docker_ports.update(int(p["HostPort"]) for p in port_mappings if p and "HostPort" in p)
        except Exception as e:
            self.logger.warning(f"Error retrieving Docker ports: {e}")

        # Combine all used ports
        used_ports = system_ports | docker_ports

        # Log used ports in port range (for debugging)
        used_in_range = [p for p in used_ports if start_port <= p <= start_port + 100]
        if used_in_range:
            self.logger.debug(f"Used ports in search range: {used_in_range}")

        # Find an available port with a retry limit
        port = start_port
        retries = 0

        while retries < max_retries:
            if port not in used_ports:
                # Double-check port availability with a direct socket test
                if self._test_port_availability(port):
                    return port
                else:
                    self.logger.debug(f"Port {port} appears free but failed socket test")

            port += 1
            retries += 1

            # Avoid well-known ports
            if port < 1024:
                port = 1024

        raise PortAllocationError(f"No available ports found after {max_retries} attempts starting from {start_port}")

    def _test_port_availability(self, port):
        """
        Test if a port is truly available by attempting to bind a socket.

        Args:
            port: Port number to test

        Returns:
            bool: True if port is available, False otherwise
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = False

        try:
            # Try to bind to the port
            sock.bind(("0.0.0.0", port))
            result = True
        except OSError:
            # Port is in use
            result = False
        finally:
            sock.close()

        return result

    def allocate_ports(self):
        """
        Safely allocate all required ports for a VM container.

        Returns:
            dict: Dictionary with allocated ports

        Raises:
            PortAllocationError: If port allocation fails
        """
        with acquire_port_lock(self.lock_file):
            try:
                ports = {}
                for name, start_port in self.start_port_ranges.items():
                    # Try to allocate the port, with retries
                    try:
                        allocated_port = self.find_available_port(start_port)
                        ports[name] = allocated_port
                        # Update starting port for next allocation
                        self.start_port_ranges[name] = allocated_port + 1
                    except PortAllocationError as e:
                        # If we can't find a port starting from the default,
                        # try with a higher range
                        self.logger.warning(f"Port allocation failed for {name}: {e}")
                        new_start = start_port + 1000  # Try a much higher range
                        self.logger.info(f"Retrying with higher port range starting at {new_start}")
                        allocated_port = self.find_available_port(new_start)
                        ports[name] = allocated_port
                        self.start_port_ranges[name] = allocated_port + 1

                self.logger.info(f"Allocated ports: {ports}")
                return ports
            except Exception as e:
                self.logger.error(f"Port allocation failed: {e}")
                raise PortAllocationError(f"Failed to allocate ports: {e}") from e

    def check_websocket_server(self, server_port, container, retry_count, max_retries):
        """
        Check if WebSocket server is available on the specified port.

        Args:
            server_port: Port number to check
            container: Docker container instance
            retry_count: Current retry count
            max_retries: Maximum number of retries

        Returns:
            tuple: (updated_retry_count, success_status)

        Raises:
            NetworkError: If container health check fails
        """
        status_http = create_http_client(retries=1, connect_timeout=5.0, read_timeout=5.0)

        try:
            # Try to perform a WebSocket handshake by sending an HTTP request with upgrade headers
            headers = {
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",  # Dummy key
                "Sec-WebSocket-Version": "13",
            }

            response = status_http.request(
                "GET",
                f"http://localhost:{server_port}",
                headers=headers,
                timeout=urllib3.Timeout(connect=5.0, read=5.0),
            )

            # WebSocket handshake should return 101 Switching Protocols
            # But we'll also accept any 2xx or 3xx response
            if response.status < 400:
                self.logger.debug(f"WebSocket server responded with status: {response.status}")
                return 0, True  # Reset retry count on success

            self.logger.debug(f"WebSocket server check returned status: {response.status}")
            return retry_count, False
        except Exception as e:
            self.logger.debug(f"WebSocket server check failed: {e}")
            retry_count += 1

            # If we've had too many consecutive failures, check if container is still running
            if retry_count > max_retries:
                try:
                    container.reload()
                    if container.status != "running":
                        raise NetworkError(
                            f"Container stopped running during VM initialization. Status: {container.status}"
                        )
                except Exception as container_error:
                    self.logger.error(f"Container health check failed: {container_error}")
                    raise NetworkError(f"Container health check failed: {container_error}") from container_error

            return retry_count, False
        finally:
            status_http.clear()

    def wait_for_vm_ready(self, server_port, container, timeout=None):
        """
        Wait for VM to be ready by checking WebSocket server availability.

        Args:
            server_port: Port number to check
            container: Docker container instance
            timeout: Maximum time to wait in seconds

        Returns:
            bool: True if VM is ready

        Raises:
            TimeoutError: If VM is not ready within the timeout period
            NetworkError: If container health check fails
        """
        timeout = timeout or self.config.vm_ready_timeout

        start_time = time.time()
        last_log_time = start_time
        retry_count = 0
        max_retries = 3  # Number of consecutive failures to tolerate

        try:
            while time.time() - start_time < timeout:
                current_time = time.time()
                retry_count, success = self.check_websocket_server(server_port, container, retry_count, max_retries)

                if success:
                    self.logger.info(f"VM WebSocket server is ready! Total wait time: {current_time - start_time:.2f}s")
                    return True

                if current_time - last_log_time >= 10:
                    self.logger.info(
                        f"Checking if WebSocket server is ready... ({current_time - start_time:.2f}s elapsed)"
                    )
                    last_log_time = current_time

                time.sleep(1)

            raise TimeoutError(f"WebSocket server failed to become ready within {timeout}s timeout period")
        except Exception as e:
            self.logger.error(f"Error waiting for WebSocket server to be ready: {e}")
            if isinstance(e, TimeoutError):
                raise
            raise NetworkError(f"Error waiting for WebSocket server: {e}") from e


class DockerManager:
    """Manages Docker image and container operations."""

    def __init__(self, docker_client: docker.client, logger: logging.Logger, config: VMConfig):
        """
        Initialize the Docker manager.

        Args:
            docker_client: Docker client instance
            logger: Logger instance
            config: VMConfig instance
        """
        self.docker_client = docker_client
        self.logger = logger
        self.config = config

    def ensure_image_exists(self, image_name):
        """
        Check if a Docker image exists locally, pull if not.

        Args:
            image_name: Name of the image to check

        Raises:
            ContainerError: If image pull fails
        """
        try:
            self.docker_client.images.get(image_name)
            self.logger.info(f"Image {image_name} found locally")
        except docker.errors.ImageNotFound:
            self.logger.info(f"Image {image_name} not found locally, pulling...")
            try:
                self.docker_client.images.pull(image_name)
                self.logger.info(f"Successfully pulled image: {image_name}")
            except Exception as e:
                self.logger.error(f"Error pulling image {image_name}: {e}")
                raise ContainerError(f"Failed to pull Docker image: {e}") from e

    def create_container(self, name, image_name, environment, volumes, ports, caps=None, devices=None, shm_size="512m"):
        """
        Create a new Docker container.

        Args:
            name: Name for the container
            image_name: Docker image name
            environment: Environment variables
            volumes: Volume mappings
            ports: Port mappings
            caps: Linux capabilities to add
            devices: Devices to expose
            shm_size: Shared memory size

        Returns:
            docker.models.containers.Container: Created container

        Raises:
            ContainerError: If container creation fails
        """
        try:
            self.logger.info(f"Creating container '{name}' from image '{image_name}'")

            # Ensure image exists
            self.ensure_image_exists(image_name)

            # Create the container without starting it
            container = self.docker_client.containers.create(
                image=image_name,
                name=name,
                environment=environment,
                cap_add=caps or ["NET_ADMIN"],
                devices=devices or ["/dev/kvm", "/dev/net/tun"] if VMConfig.is_kvm_available() else None,
                volumes=volumes,
                ports=ports,
                shm_size=shm_size,
                detach=True,
            )

            self.logger.info(f"Successfully created container: {name}")
            return container

        except Exception as e:
            self.logger.error(f"Error creating container: {e}")
            raise ContainerError(f"Failed to create container: {e}") from e

    def get_existing_container(self, name):
        """
        Get an existing container by name.

        Args:
            name: Container name

        Returns:
            docker.models.containers.Container or None: Container if found
        """
        try:
            return self.docker_client.containers.get(name)
        except docker.errors.NotFound:
            return None

    def start_container(self, container):
        """
        Start a container and verify it's running.

        Args:
            container: Docker container instance

        Raises:
            ContainerError: If container cannot be started
        """
        try:
            container.start()
            # Verify container started successfully
            container.reload()
            if container.status != "running":
                raise ContainerError(f"Container could not be started, status: {container.status}")
            self.logger.info(f"Container started: {container.name}")
        except Exception as e:
            self.logger.error(f"Error starting container: {e}")
            raise ContainerError(f"Failed to start container: {e}") from e

    def stop_container(self, container, timeout=None, remove=False):
        """
        Stop a container.

        Args:
            container: Docker container instance
            timeout: Timeout for stopping in seconds
            remove: Whether to remove the container after stopping

        Returns:
            bool: True if successful

        Raises:
            ContainerError: If container cannot be stopped
        """
        if not container:
            return True  # Nothing to stop

        timeout = timeout or self.config.container_stop_timeout

        try:
            container.stop(timeout=timeout)
            self.logger.info(f"Stopped container: {container.name}")

            # Add a small delay to ensure container has fully stopped
            time.sleep(0.5)

            if remove:
                container.remove()
                self.logger.info(f"Removed container: {container.name}")

            return True
        except Exception as e:
            self.logger.error(f"Error stopping container: {e}")
            raise ContainerError(f"Failed to stop container: {e}") from e

    def remove_container(self, container, force=True):
        """
        Remove a container.

        Args:
            container: Docker container instance
            force: Whether to force removal

        Returns:
            bool: True if successful

        Raises:
            ContainerError: If container cannot be removed
        """
        if not container:
            return True  # Nothing to remove

        try:
            container.remove(force=force, v=True)  # v=True to remove volumes
            self.logger.info(f"Removed container: {container.name}")
            return True
        except docker.errors.NotFound:
            self.logger.info(f"Container already removed: {container.name}")
            return True
        except Exception as e:
            self.logger.error(f"Error removing container: {e}")
            raise ContainerError(f"Failed to remove container: {e}") from e


class FileManager:
    """Manages file operations including optimized copying and downloads."""

    def __init__(self, logger: logging.Logger, config: VMConfig):
        """
        Initialize the file manager.

        Args:
            logger: Logger instance
            config: VMConfig instance
        """
        self.logger = logger
        self.config = config

    def optimized_copy(self, src_dir, dst_dir, hardlink_threshold=None):
        """
        Perform an optimized copy of files using hardlinks when possible.

        Args:
            src_dir: Source directory
            dst_dir: Destination directory
            hardlink_threshold: Size threshold for using hardlinks (bytes)

        Returns:
            bool: True if successful

        Raises:
            VMImageError: If copy fails
        """
        if not os.path.exists(src_dir):
            raise VMImageError(f"Source directory not found: {src_dir}")

        hardlink_threshold = hardlink_threshold or self.config.default_hardlink_threshold
        self.logger.info(f"Starting optimized copy from {src_dir} to {dst_dir}")

        try:
            # Get list of files to copy
            items = os.listdir(src_dir)
            total_items = len(items)

            with tqdm(total=total_items, desc="Copying VM files", unit="files") as pbar:
                for item in items:
                    src_path = os.path.join(src_dir, item)
                    dst_path = os.path.join(dst_dir, item)

                    # Skip if file/directory already exists
                    if os.path.exists(dst_path):
                        pbar.update(1)
                        continue

                    if os.path.isfile(src_path):
                        # Try to create hardlink first (much faster than copy)
                        try:
                            file_size = os.path.getsize(src_path)
                            if file_size > hardlink_threshold:
                                self.logger.debug(
                                    f"Creating hardlink for large file: {item} ({file_size / (1024 * 1024):.2f} MB)"
                                )
                                # For large files, use hardlinks if possible
                                os.link(src_path, dst_path)
                            else:
                                # For smaller files, do a regular copy
                                shutil.copy2(src_path, dst_path)
                        except OSError:
                            # Fallback to regular copy if hardlink fails
                            self.logger.debug(f"Hardlink failed for {item}, using regular copy")
                            shutil.copy2(src_path, dst_path)
                    elif os.path.isdir(src_path):
                        # For directories, we need to do a recursive copy
                        os.makedirs(dst_path, exist_ok=True)
                        # Recursive copy, but handle each file individually
                        for root, dirs, files in os.walk(src_path):
                            # Create all subdirectories
                            for d in dirs:
                                src_subdir = os.path.join(root, d)
                                rel_path = os.path.relpath(src_subdir, src_path)
                                dst_subdir = os.path.join(dst_path, rel_path)
                                os.makedirs(dst_subdir, exist_ok=True)

                            # Copy all files, using hardlinks for large files
                            for f in files:
                                src_file = os.path.join(root, f)
                                rel_path = os.path.relpath(src_file, src_path)
                                dst_file = os.path.join(dst_path, rel_path)

                                # Create parent directories if they don't exist
                                os.makedirs(os.path.dirname(dst_file), exist_ok=True)

                                try:
                                    # Check file size
                                    file_size = os.path.getsize(src_file)
                                    if file_size > hardlink_threshold:
                                        # Use hardlink for large files
                                        os.link(src_file, dst_file)
                                    else:
                                        # Regular copy for smaller files
                                        shutil.copy2(src_file, dst_file)
                                except OSError:
                                    # Fallback to regular copy
                                    shutil.copy2(src_file, dst_file)

                    pbar.update(1)

            self.logger.info(f"Optimized copy completed: {total_items} items processed")
            return True

        except Exception as e:
            self.logger.error(f"Error during optimized copy: {e}")
            raise VMImageError(f"Failed to copy files: {e}") from e

    def download_file(self, url, destination_dir, retries=3):
        """
        Download a file with resume capability.

        Args:
            url: URL to download from
            destination_dir: Directory to save the file
            retries: Number of retry attempts

        Returns:
            str: Path to the downloaded file

        Raises:
            VMImageError: If download fails
        """
        if not is_url(url):
            raise VMImageError(f"Invalid URL: {url}")

        try:
            # Prepare for download
            downloaded_file_name = url.split("/")[-1]
            downloaded_file_path = os.path.join(destination_dir, downloaded_file_name)
            os.makedirs(destination_dir, exist_ok=True)

            downloaded_size = 0
            retry_count = 0

            while retry_count <= retries:
                headers = {}
                if os.path.exists(downloaded_file_path):
                    downloaded_size = os.path.getsize(downloaded_file_path)
                    headers["Range"] = f"bytes={downloaded_size}-"

                response = None
                try:
                    # Try to download the file
                    response = http.request(
                        "GET",
                        url,
                        headers=headers,
                        preload_content=False,  # Don't read data into memory yet
                    )

                    # Handle Range Not Satisfiable (416) case
                    if response.status == 416:
                        self.logger.info("File already fully downloaded.")
                        break

                    # Check for errors
                    if response.status >= 400:
                        raise VMImageError(f"HTTP Error: {response.status} for URL: {url}")

                    # Get content length for progress tracking
                    total_size = int(response.headers.get("content-length", 0))

                    with open(downloaded_file_path, "ab") as file:
                        with tqdm(
                            desc=f"Downloading {downloaded_file_name}",
                            total=total_size,
                            unit="iB",
                            unit_scale=True,
                            unit_divisor=1024,
                            initial=downloaded_size,
                            ascii=True,
                        ) as progress_bar:
                            try:
                                for chunk in response.stream(8192):
                                    size = file.write(chunk)
                                    progress_bar.update(size)
                                self.logger.info("Download completed successfully.")
                                # Break out of the retry loop on success
                                break
                            except Exception as e:
                                self.logger.error(f"Download error: {e}")
                                retry_count += 1
                                if retry_count <= retries:
                                    time.sleep(5)
                                    self.logger.info(f"Retrying download (attempt {retry_count}/{retries})...")
                                else:
                                    raise VMImageError(f"Download failed after {retries} attempts: {e}") from e
                except Exception as e:
                    self.logger.error(f"Error during download: {e}")
                    retry_count += 1
                    if retry_count <= retries:
                        time.sleep(5)
                        self.logger.info(f"Retrying download (attempt {retry_count}/{retries})...")
                    else:
                        raise VMImageError(f"Failed to download file after {retries} attempts: {e}") from e
                finally:
                    # Make sure to release resources even if an exception occurs
                    if response is not None:
                        response.release_conn()

            return downloaded_file_path

        except Exception as e:
            self.logger.error(f"Error downloading file: {e}")
            raise VMImageError(f"Failed to download file: {e}") from e

    def extract_archive(self, archive_path, extract_dir=None):
        """
        Extract a zip or tar archive.

        Args:
            archive_path: Path to the archive file
            extract_dir: Directory to extract to (defaults to archive's directory)

        Returns:
            str: Path to the extracted contents

        Raises:
            VMImageError: If extraction fails
        """
        extract_dir = extract_dir or os.path.dirname(archive_path)

        # List of supported disk image formats
        disk_formats = [".qcow2", ".vmdk", ".vhd", ".vhdx", ".vdi", ".img", ".raw", ".iso"]
        compression_formats = [".gz", ".xz", ".zip", ".tar", ".tar.gz", ".tgz"]

        # If this is a standard disk image format, we don't need to extract it
        if any(archive_path.lower().endswith(ext) for ext in disk_formats):
            self.logger.info(f"Using disk image directly: {archive_path}")
            return os.path.dirname(archive_path)

        # If this is a compressed disk image (e.g., .img.gz), QEMU can handle it directly
        for disk_format in disk_formats:
            for comp_format in compression_formats:
                if archive_path.lower().endswith(f"{disk_format}{comp_format}"):
                    self.logger.info(f"Using compressed disk image directly (QEMU will extract): {archive_path}")
                    return os.path.dirname(archive_path)

        # For regular archives that need extraction
        try:
            if archive_path.endswith(".zip"):
                self.logger.info(f"Extracting ZIP archive: {archive_path}")
                with zipfile.ZipFile(archive_path, "r") as zip_ref:
                    zip_ref.extractall(extract_dir)

            elif any(archive_path.endswith(ext) for ext in [".tar", ".tar.gz", ".tgz"]):
                self.logger.info(f"Extracting TAR archive: {archive_path}")
                with tarfile.open(archive_path, "r:*") as tar_ref:
                    tar_ref.extractall(extract_dir)

            else:
                # If we're here, it's not a recognized format for extraction
                self.logger.warning(f"Unrecognized archive format, QEMU may handle it: {archive_path}")
                return os.path.dirname(archive_path)  # Return directory anyway as QEMU might handle it

            self.logger.info(f"Archive extracted to: {extract_dir}")

            # Look for disk images in the extracted files
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    if any(file.lower().endswith(ext) for ext in disk_formats):
                        disk_path = os.path.join(root, file)
                        self.logger.info(f"Found disk image in extracted archive: {disk_path}")
                        # If we find a disk image in a subdirectory, we should return that directory
                        if root != extract_dir:
                            return root
                        break

            return extract_dir

        except Exception as e:
            self.logger.error(f"Error extracting archive: {e}")
            raise VMImageError(f"Failed to extract archive: {e}") from e


class Environment:
    """Manages VM environments, providing creation, starting, and stopping of VMs."""

    def __init__(
        self, config: Optional[VMConfig] = None, docker_client: docker.client = None, logger: logging.Logger = None
    ):
        """
        Initialize the environment for creating VM instances.

        Args:
            config: VMConfig instance for configuration
            docker_client: Docker client instance
            logger: Logger instance
        """
        # Use provided config or create default
        self.config = config or VMConfig()
        # Convert relative paths to absolute paths
        self.config.initial_vm_dir = os.path.abspath(self.config.initial_vm_dir)
        self.config.vm_environments = os.path.abspath(self.config.vm_environments)
        self.config.server_dir = os.path.abspath(self.config.server_dir)
        self.config.vms_dir = os.path.abspath(self.config.vms_dir)

        self.docker_client = docker_client or docker.from_env()
        self.logger = logger or logging.getLogger(__name__)

        # Set initial properties from config
        self.vm_os = self.config.vm_os
        self.initial_vm_dir = self.config.initial_vm_dir
        self.instance_counter = 0  # tracks how many VMs were started

        # Initialize managers with the config
        self.docker_manager = DockerManager(self.docker_client, self.logger, self.config)
        self.network_manager = NetworkManager(self.docker_client, self.logger, self.config)
        self.file_manager = FileManager(self.logger, self.config)

        # Ensure directories exist
        os.makedirs(self.config.vm_environments, exist_ok=True)
        os.makedirs(self.config.vms_dir, exist_ok=True)

        # Validate initial VM volume directory
        if not os.path.exists(self.initial_vm_dir):
            self.logger.warning(f"Initial VM volume directory not found: {self.initial_vm_dir}")

    def _get_instance_dir(self):
        """
        Create and return an absolute path for a new VM instance.
        Uses a more Docker-friendly naming convention.
        """
        # Use a simple incrementing counter and avoid slashes
        instance_counter = len(os.listdir(self.config.vm_environments)) + 1
        instance_dir = os.path.join(self.config.vm_environments, f"ubuntu_instance_{instance_counter}")
        os.makedirs(instance_dir, exist_ok=True)
        return instance_dir

    def prepare_vm_image(self, boot_source=None):
        """
        Prepare VM image from URL or local source.

        Args:
            boot_source: URL or path to boot image (None uses default)

        Returns:
            str: Path to prepared VM image directory
        """
        # If no boot source specified, use initial VM volume
        if not boot_source:
            if not os.path.exists(self.initial_vm_dir) or not os.listdir(self.initial_vm_dir):
                raise VMImageError(f"Initial VM volume directory is empty or not found: {self.initial_vm_dir}")
            return self.initial_vm_dir

        # If URL, download the image
        if is_url(boot_source):
            self.logger.info(f"Downloading VM image from: {boot_source}")

            # Create a more useful directory name based on the URL
            url_path = urllib.parse.urlparse(boot_source).path
            filename = os.path.basename(url_path)
            base_name = os.path.splitext(filename)[0]

            # Clean up the name (remove special characters)
            clean_name = "".join(c if c.isalnum() or c in ["-", "_"] else "_" for c in base_name)

            # Use timestamp to ensure uniqueness
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            vm_name = f"{clean_name}_{timestamp}"

            # Create the download directory with the useful name
            download_dir = os.path.join(self.config.vms_dir, vm_name)
            os.makedirs(download_dir, exist_ok=True)

            # Download the file
            downloaded_file = self.file_manager.download_file(boot_source, download_dir)

            # Extract if it's an archive
            if any(downloaded_file.endswith(ext) for ext in [".zip", ".tar", ".tar.gz", ".tgz"]):
                self.logger.info(f"Extracting archive to {download_dir}")
                self.file_manager.extract_archive(downloaded_file, download_dir)

                # Check if extraction created a subdirectory
                extracted_dirs = [
                    d
                    for d in os.listdir(download_dir)
                    if os.path.isdir(os.path.join(download_dir, d)) and d != os.path.basename(downloaded_file)
                ]

                if extracted_dirs and len(extracted_dirs) == 1:
                    # If there's just one subdirectory, use that as our VM directory
                    extracted_dir = os.path.join(download_dir, extracted_dirs[0])
                    self.logger.info(f"Using extracted directory: {extracted_dir}")
                    return extracted_dir

                return download_dir
            else:
                self.logger.info(f"Using downloaded file at: {download_dir}")
                return download_dir

        # If path, validate it exists
        elif os.path.exists(boot_source):
            return boot_source

        # If relative path, check in volume directory
        else:
            full_path = os.path.join(self.config.vms_dir, boot_source)
            if os.path.exists(full_path):
                return full_path
            else:
                raise VMImageError(f"VM image path not found: {boot_source}")

    def start_vm(self, boot_source=None, name_prefix: str = "vm"):
        """
        Start a new VM instance with proper port allocation.

        Args:
            boot_source: URL or path to boot image (None uses default)
            name_prefix: Prefix for container name (default: qemu_{vm_os}_)

        Returns:
            tuple: (container, connection_info)

        Raises:
            ContainerError: If container creation fails
            PortAllocationError: If port allocation fails
        """
        try:
            # Get an absolute instance directory path
            instance_dir = self._get_instance_dir()

            # Prepare VM image (this method uses absolute paths)
            if boot_source:
                source_dir = self.prepare_vm_image(boot_source)
                # Copy from source to instance directory using absolute paths
                self.file_manager.optimized_copy(source_dir, instance_dir)
            else:
                # Use default VM image with absolute paths
                self.file_manager.optimized_copy(self.initial_vm_dir, instance_dir)

            # Allocate available ports using NetworkManager
            ports_dict = self.network_manager.allocate_ports()

            # Define environment variables
            environment = {
                "BOOT": "ubuntu",  # using copied files instead of a downloaded image
                "DEBUG": self.config.vm_debug,
                "RAM_SIZE": self.config.vm_ram_size,
                "CPU_CORES": self.config.vm_cpu_cores,
                "DISK_SIZE": self.config.vm_disk_size,
                # "BOOT_MODE": self.config.vm_boot_mode,
                # "DISK_TYPE": self.config.vm_disk_type,
            }

            # Map the new instance_dir as volume to /storage in container
            volumes = {instance_dir: {"bind": "/storage", "mode": "rw"}}

            # Create unique container name
            container_name = f"{name_prefix}_{self.vm_os}_{self.instance_counter}"

            # Map ports
            host_vnc = ports_dict.get("vnc")
            host_ssh = ports_dict.get("ssh")

            ports = {
                8006: host_vnc,  # VNC port
                22: host_ssh,  # SSH port
            }

            # Add server port mapping if available
            if "server" in ports_dict:
                server_port = ports_dict.get("server")
                ports[8765] = server_port  # WebSocket server port

            # Create container
            container = self.docker_manager.create_container(
                name=container_name,
                image_name=self.config.default_vm_image,
                environment=environment,
                volumes=volumes,
                ports=ports,
                caps=["NET_ADMIN"],
                devices=["/dev/kvm", "/dev/net/tun"] if VMConfig.is_kvm_available() else None,
            )

            # Start the container
            self.docker_manager.start_container(container)

            # Wait for VM to be ready if server port is allocated
            if "server" in ports_dict:
                server_port = ports_dict.get("server")
                self.network_manager.wait_for_vm_ready(server_port, container)

            # Return container and connection info
            connection_info = {
                "container_name": container_name,
                "host_vnc": host_vnc,
                "ssh_port": host_ssh,
                "instance_dir": instance_dir,
            }

            if "server" in ports_dict:
                connection_info["server_port"] = ports_dict.get("server")

            self.logger.info(f"VM started successfully: {connection_info}")
            return container, connection_info

        except Exception as e:
            self.logger.error(f"Failed to start VM: {e}")
            # Clean up resources if startup fails
            if "container" in locals() and container:
                try:
                    self.docker_manager.stop_container(container, remove=True)
                except Exception as cleanup_error:
                    self.logger.error(f"Error during cleanup: {cleanup_error}")

            if "instance_dir" in locals() and os.path.exists(instance_dir):
                try:
                    # Consider keeping instance directory for debugging
                    pass
                except Exception as cleanup_error:
                    self.logger.error(f"Error cleaning up instance directory: {cleanup_error}")

            # Re-raise the original exception
            raise

    def create_and_setup_sandbox(self, name=None, server_dir=None):
        """
        Create a sandbox environment from the base VM image, SSH into it,
        copy the server files, and start the server with improved VM boot handling.

        Args:
            name: Optional name for the sandbox environment
            server_dir: Path to the server directory to copy to the VM

        Returns:
            dict: Information about the sandbox environment including connection details
        """
        try:
            # Use provided server directory or default from config
            server_dir = os.path.abspath(server_dir or self.config.server_dir)

            # Validate server directory exists
            if not os.path.exists(server_dir):
                raise SandboxError(f"Server directory not found: {server_dir}")

            # Start the VM
            name_prefix = f"sandbox_{name}_" if name else "sandbox_"
            container, connection_info = self.start_vm(name_prefix=name_prefix)

            # Extract SSH port from connection info
            ssh_port = connection_info.get("ssh_port")
            if not ssh_port:
                raise SandboxError("SSH port was not allocated properly")

            # Give the VM more time to initialize before attempting SSH
            self.logger.info("Waiting 30 seconds for VM to complete boot sequence...")
            time.sleep(30)

            # Check container health
            try:
                container.reload()
                if container.status != "running":
                    raise SandboxError(f"Container is not running. Current status: {container.status}")
                self.logger.info(f"Container status: {container.status}")
            except Exception as e:
                self.logger.error(f"Failed to check container status: {e}")
                raise SandboxError(f"Failed to verify container status: {e}") from e

            # Wait for SSH to be available (VM might still be booting)
            try:
                self._wait_for_ssh("localhost", ssh_port, "sandbox-user", "password", timeout=120)
            except TimeoutError as e:
                # If SSH times out, check VM boot logs for issues
                try:
                    logs = container.logs(tail=200).decode("utf-8")
                    self.logger.error(f"SSH connection failed. Container logs (tail):\n{logs}")
                except Exception as log_error:
                    self.logger.error(f"Failed to retrieve container logs: {log_error}")
                raise SandboxError(f"Failed to establish SSH connection: {e}") from e

            # Set up SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            try:
                # Connect to the VM - add longer timeouts
                self.logger.info(f"Establishing SSH connection to localhost:{ssh_port}...")
                ssh.connect(
                    "localhost",
                    port=ssh_port,
                    username="sandbox-user",
                    password="password",
                    timeout=30,  # Longer timeout
                    banner_timeout=30,  # Explicitly set banner timeout
                    auth_timeout=30,  # Explicitly set auth timeout
                )
                self.logger.info(f"SSH connection established to sandbox at localhost:{ssh_port}")

                # Verify SSH connection works by running a simple command
                stdin, stdout, stderr = ssh.exec_command("echo SSH connection successful")
                result = stdout.read().decode("utf-8").strip()
                self.logger.info(f"SSH test command result: {result}")

                # Copy server directory to VM using SCP
                self._scp_directory(server_dir, "sandbox-user@localhost:/home/sandbox-user/server", ssh_port)

                # Navigate to server directory and run commands
                commands = [
                    "cd /home/sandbox-user/server",
                    "uv sync",
                    "nohup uv run main.py > /tmp/server.log 2>&1 &",  # Run in background, save logs to file
                ]

                # Execute setup commands
                for cmd in commands:
                    stdin, stdout, stderr = ssh.exec_command(cmd)
                    exit_status = stdout.channel.recv_exit_status()

                    if exit_status != 0:
                        stderr_output = stderr.read().decode("utf-8")
                        self.logger.warning(
                            f"Command '{cmd}' returned non-zero exit code {exit_status}: {stderr_output}"
                        )
                    else:
                        self.logger.info(f"Successfully executed: {cmd}")

                # Add server status to connection info
                connection_info["server_status"] = "started"

                return connection_info

            except Exception as e:
                self.logger.error(f"Error setting up server in VM: {e}")
                raise SandboxError(f"Failed to set up server in VM: {e}") from e
            finally:
                ssh.close()

        except Exception as e:
            self.logger.error(f"Error creating sandbox environment: {e}")
            # Clean up if needed
            if "container" in locals() and container:
                self.stop_vm(container, remove_instance=False)  # Keep instance for debugging
            raise SandboxError(f"Failed to create sandbox environment: {e}") from e

    def _wait_for_ssh(self, hostname, port, username, password, timeout=120):
        """
        Wait for SSH service to be available with improved error handling and retries.

        Args:
            hostname: Host to connect to
            port: SSH port
            username: SSH username
            password: SSH password
            timeout: Maximum time to wait in seconds

        Returns:
            bool: True if SSH is available

        Raises:
            TimeoutError: If SSH is not available within timeout
        """
        start_time = time.time()
        retry_interval = 3  # Start with 3 seconds between retries
        max_retry_interval = 10  # Maximum retry interval
        current_attempt = 0

        self.logger.info(f"Waiting for SSH service at {hostname}:{port} (timeout: {timeout}s)")

        while time.time() - start_time < timeout:
            current_attempt += 1
            try:
                # Create a new SSH client for each attempt
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # Increase logging for debugging SSH issues
                if current_attempt % 5 == 0:  # Log details every 5 attempts
                    self.logger.debug(
                        f"SSH connection attempt {current_attempt}, elapsed time: {time.time() - start_time:.1f}s"
                    )

                # Use a shorter connection timeout to detect problems faster
                connect_timeout = min(10, timeout / 3)
                ssh.connect(
                    hostname,
                    port=port,
                    username=username,
                    password=password,
                    timeout=connect_timeout,
                    auth_timeout=connect_timeout,
                    banner_timeout=connect_timeout,
                )

                # Test the connection with a simple command
                stdin, stdout, stderr = ssh.exec_command("echo SSH connection successful")
                result = stdout.read().decode("utf-8").strip()

                if "SSH connection successful" in result:
                    self.logger.info(
                        f"SSH connection test successful after {current_attempt} attempts ({time.time() - start_time:.1f}s)"
                    )
                    ssh.close()
                    return True
                else:
                    self.logger.warning(f"SSH connection established but command test failed. Got: '{result}'")
                    # Continue trying since the command didn't work properly

                ssh.close()
            except paramiko.ssh_exception.SSHException as e:
                if "Error reading SSH protocol banner" in str(e):
                    self.logger.debug(f"SSH service not ready yet (banner error): {e}")
                else:
                    self.logger.debug(f"SSH error: {e}")
            except (socket.error, socket.timeout) as e:
                self.logger.debug(f"Network error connecting to SSH: {e}")
            except Exception as e:
                self.logger.debug(f"Unexpected error connecting to SSH: {type(e).__name__}: {e}")
            finally:
                # Increase retry interval with exponential backoff
                retry_interval = min(retry_interval * 1.5, max_retry_interval)

                # Check if we still have time left
                remaining_time = timeout - (time.time() - start_time)
                if remaining_time <= 0:
                    break

                # Sleep but don't exceed our timeout
                sleep_time = min(retry_interval, remaining_time)
                time.sleep(sleep_time)

        # Final state report before giving up
        self.logger.error(
            f"SSH service did not become available within {timeout}s timeout after {current_attempt} attempts"
        )
        raise TimeoutError(f"SSH service did not become available within {timeout}s timeout")

    def _scp_directory(self, local_path, remote_path, port):
        """
        Copy a directory to a remote host using scp.

        Args:
            local_path: Local directory path
            remote_path: Remote path in user@host:/path format
            port: SSH port

        Returns:
            bool: True if successful

        Raises:
            SandboxError: If scp fails
        """
        try:
            cmd = ["scp", "-r", "-P", str(port), local_path, remote_path]
            self.logger.info(f"Running SCP command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,  # Don't raise exception, handle manually
            )

            if result.returncode != 0:
                self.logger.error(f"SCP failed with code {result.returncode}: {result.stderr}")
                raise SandboxError(f"Failed to copy files with SCP: {result.stderr}")

            self.logger.info(f"Successfully copied {local_path} to {remote_path}")
            return True

        except Exception as e:
            self.logger.error(f"Error during SCP operation: {e}")
            raise SandboxError(f"Failed to copy files with SCP: {e}") from e

    def stop_vm(self, container, remove_instance=True):
        """
        Stop a VM and optionally remove its instance directory.

        Args:
            container: Docker container instance
            remove_instance: Whether to remove the instance directory

        Returns:
            bool: True if successful
        """
        if not container:
            return True

        try:
            # Get container info before stopping
            container_name = container.name
            container.reload()

            # Extract instance number from container name
            instance_num = None
            if container_name.startswith(f"qemu_{self.vm_os}_"):
                try:
                    instance_num = int(container_name.split("_")[-1])
                except ValueError:
                    pass

            # Stop and remove the container
            self.docker_manager.stop_container(container, timeout=30, remove=True)

            # Remove instance directory if requested
            if remove_instance and instance_num is not None:
                instance_dir = os.path.join(self.config.vm_environments, f"{self.vm_os}{instance_num}")
                if os.path.exists(instance_dir) and os.path.isdir(instance_dir):
                    self.logger.info(f"Removing instance directory: {instance_dir}")
                    shutil.rmtree(instance_dir)

            return True

        except Exception as e:
            self.logger.error(f"Error stopping VM: {e}")
            raise ContainerError(f"Failed to stop VM: {e}") from e

    def list_running_vms(self):
        """
        List all running VM instances started by this environment.

        Returns:
            list: List of container information dictionaries
        """
        try:
            containers = self.docker_client.containers.list(filters={"name": f"vm_{self.vm_os}", "status": "running"})

            result = []
            for container in containers:
                # Extract instance number from container name
                instance_num = None
                if container.name.startswith(f"qemu_{self.vm_os}_"):
                    try:
                        instance_num = int(container.name.split("_")[-1])
                    except ValueError:
                        pass

                # Get port mappings
                ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
                vnc_port = None
                server_port = None
                ssh_port = None

                # Extract port mappings
                if "8006/tcp" in ports and ports["8006/tcp"]:
                    vnc_port = int(ports["8006/tcp"][0]["HostPort"])
                if "8765/tcp" in ports and ports["8765/tcp"]:
                    server_port = int(ports["8765/tcp"][0]["HostPort"])
                if "22/tcp" in ports and ports["22/tcp"]:
                    ssh_port = int(ports["22/tcp"][0]["HostPort"])

                # Build result
                vm_info = {
                    "container_id": container.id,
                    "container_name": container.name,
                    "instance_num": instance_num,
                    "host_vnc": vnc_port,
                    "server_port": server_port,
                    "ssh_port": ssh_port,
                    "status": container.status,
                }

                if instance_num is not None:
                    vm_info["instance_dir"] = os.path.join(self.config.vm_environments, f"{self.vm_os}{instance_num}")

                result.append(vm_info)

            return result

        except Exception as e:
            self.logger.error(f"Error listing VMs: {e}")
            raise ContainerError(f"Failed to list VMs: {e}") from e

    def clone_vm(self, source_container, boot_source=None):
        """
        Clone an existing VM.

        Args:
            source_container: Source container to clone
            boot_source: Optional alternative boot source

        Returns:
            tuple: (container, connection_info) of the new VM
        """
        try:
            # Get source container info
            source_container.reload()
            source_name = source_container.name

            # Extract instance number
            instance_num = None
            if source_name.startswith(f"qemu_{self.vm_os}_"):
                try:
                    instance_num = int(source_name.split("_")[-1])
                except ValueError:
                    pass

            # Get source instance directory
            source_dir = None
            if instance_num is not None:
                source_dir = os.path.join(self.config.vm_environments, f"{self.vm_os}{instance_num}")

            if not source_dir or not os.path.exists(source_dir):
                # If we can't find the source directory, start fresh
                return self.start_vm(boot_source)

            # Start a new VM, using the source directory as the boot source
            return self.start_vm(source_dir if not boot_source else boot_source)

        except Exception as e:
            self.logger.error(f"Error cloning VM: {e}")
            raise ContainerError(f"Failed to clone VM: {e}") from e

    def _scp_directory(self, local_path, remote_path, port):
        """
        Copy a directory to a remote host using scp.

        Args:
            local_path: Local directory path
            remote_path: Remote path in user@host:/path format
            port: SSH port

        Returns:
            bool: True if successful

        Raises:
            SandboxError: If scp fails
        """
        try:
            cmd = ["scp", "-r", "-P", str(port), local_path, remote_path]
            self.logger.info(f"Running SCP command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,  # Don't raise exception, handle manually
            )

            if result.returncode != 0:
                self.logger.error(f"SCP failed with code {result.returncode}: {result.stderr}")
                raise SandboxError(f"Failed to copy files with SCP: {result.stderr}")

            self.logger.info(f"Successfully copied {local_path} to {remote_path}")
            return True

        except Exception as e:
            self.logger.error(f"Error during SCP operation: {e}")
            raise SandboxError(f"Failed to copy files with SCP: {e}") from e

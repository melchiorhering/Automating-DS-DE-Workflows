import asyncio
import logging
import os
import platform
import time
import zipfile
from pathlib import Path
from typing import Dict, Optional, Union, List

import docker
import psutil
import urllib3
from filelock import FileLock
from smolagents import CodeAgent
from tqdm import tqdm

from sandbox_client import SandboxClient

# Create a urllib3 PoolManager with better connection handling
http = urllib3.PoolManager(
    retries=urllib3.Retry(3, redirect=2, backoff_factor=0.5),
    timeout=urllib3.Timeout(connect=30.0, read=60.0),
    maxsize=10
)

PROJECT_ROOT = str(os.getenv("ROOT_DIR", "./"))
VOLUME_DIRECTORY = os.getenv("VOLUME_DIRECTORY", "./volumes")

# Global URL definitions
UBUNTU_X86_URL = "https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/main/Ubuntu.qcow2.zip"
WINDOWS_X86_URL = "https://huggingface.co/datasets/xlangai/windows_osworld/resolve/main/Windows-10-x64.qcow2.zip"

# Global VM resource settings
VM_DISK_SIZE = os.getenv("VM_DISK_SIZE", "32G")
VM_RAM_SIZE = os.getenv("VM_RAM_SIZE", "4G")
VM_CPU_CORES = os.getenv("VM_CPU_CORES", "4")


class PortAllocationError(Exception):
    """Raised when unable to find available port."""
    pass


class ContainerConfig:
    """
    Configuration class for Docker containers and images.
    Handles image selection, build configuration, and container settings.
    """

    def __init__(
        self,
        image_name: str,
        dockerfile_path: str = None,
        build_args: Dict[str, str] = None,
        is_vm: bool = True,
        os_type: str = "Ubuntu",
        vm_image_url: str = None,
        shm_size: str = "512m",
        environment: Dict[str, str] = None,
        ports: Dict[int, Optional[int]] = None,
        volumes: Dict[str, Dict[str, str]] = None,
        caps: List[str] = None,
        devices: List[str] = None,
    ):
        """
        Initialize container configuration.

        Args:
            image_name: Name of the Docker image to use
            dockerfile_path: Path to Dockerfile if building image
            build_args: Arguments for Docker build
            is_vm: Whether this is a VM-based container
            os_type: OS type for VM containers ("Ubuntu" or "Windows")
            vm_image_url: URL for VM image download (overrides default)
            shm_size: Shared memory size
            environment: Environment variables for container
            ports: Port mappings (container:host)
            volumes: Volume mappings
            caps: Linux capabilities to add
            devices: Devices to expose to container
        """
        self.image_name = image_name
        self.dockerfile_path = dockerfile_path
        self.build_args = build_args or {}
        self.is_vm = is_vm
        self.os_type = os_type
        self.shm_size = shm_size
        self.environment = environment or {}
        self.ports = ports or {}
        self.volumes = volumes or {}
        self.capabilities = caps or []
        self.devices = devices or []

        # Set VM image URL based on OS type if not provided
        self.vm_image_url = vm_image_url
        if is_vm and not vm_image_url:
            self.vm_image_url = UBUNTU_X86_URL if os_type == "Ubuntu" else WINDOWS_X86_URL

    @classmethod
    def standard_container(cls, image_name: str = "kasm-ubuntu", dockerfile_path: str = "environment/Dockerfile.kasm", **kwargs):
        """Create configuration for a standard (non-VM) container."""
        return cls(
            image_name=image_name,
            dockerfile_path=dockerfile_path,
            is_vm=False,
            build_args={"BASE_TAG": "develop", "BASE_IMAGE": "core-ubuntu-noble"},
            **kwargs
        )

    @classmethod
    def ubuntu_vm(cls, **kwargs):
        """Create configuration for Ubuntu VM container."""
        return cls(
            image_name="happysixd/osworld-ubuntu-docker",
            is_vm=True,
            os_type="Ubuntu",
            vm_image_url=UBUNTU_X86_URL,
            capabilities=["NET_ADMIN"],
            devices=["/dev/kvm"],
            **kwargs
        )

    @classmethod
    def windows_vm(cls, **kwargs):
        """Create configuration for Windows VM container."""
        return cls(
            image_name="happysixd/osworld-windows-docker",
            is_vm=True,
            os_type="Windows",
            vm_image_url=WINDOWS_X86_URL,
            capabilities=["NET_ADMIN"],
            devices=["/dev/kvm"],
            **kwargs
        )

    def build_image(self, docker_client, logger=None):
        """Build Docker image from Dockerfile."""
        if not self.dockerfile_path:
            if logger:
                logger.info(f"Using existing image: {self.image_name}")
            return

        try:
            if logger:
                logger.info(f"Building image '{self.image_name}' from {self.dockerfile_path}")

            image, build_logs = docker_client.images.build(
                path=PROJECT_ROOT,
                dockerfile=self.dockerfile_path,
                tag=self.image_name,
                buildargs=self.build_args,
                rm=True,
            )

            # Log build output if logger is provided
            if logger:
                for log in build_logs:
                    if "stream" in log:
                        log_line = log["stream"].strip()
                        if log_line:
                            logger.debug(log_line)
                logger.info(f"Successfully built image: {self.image_name}")

            return image

        except Exception as e:
            if logger:
                logger.error(f"Error building Docker image: {str(e)}")
            raise


class Sandbox:
    """
    Manages a sandbox client connecting to a sandbox server in a Docker container.
    Supports both standard sandbox environments and VM-based environments.
    """

    def __init__(
        self,
        agent: CodeAgent,
        task: str,
        container_config: Optional[ContainerConfig] = None,
        host: str = "localhost",
        port: int = 8765,
        client_prefix: str = "sandbox",
        container_name: str = None,
        build_image: bool = False,
        docker_env: dict = {},
        volume_directory: str = None,
        vms_dir: str = None,
        download_retries: int = 3,
        vm_ready_timeout: int = 300,
        container_stop_timeout: int = 5,
        start_port_range: dict = None,
    ):
        """
        Initialize the sandbox with a Docker container.

        Args:
            agent: The agent that will use this sandbox
            task: Description of the task
            container_config: Container configuration (if None, defaults to Ubuntu VM)
            host: The hostname where sandbox server will be accessible
            port: The port number for the sandbox server
            client_prefix: Prefix for client ID
            container_name: Optional custom container name
            build_image: Whether to build the image before creating containers
            docker_env: Environment variables for Docker client
            volume_directory: Directory for persistent volumes
            vms_dir: Directory for VM images
            download_retries: Number of retries for downloading VM images
            vm_ready_timeout: Maximum time to wait for VM to be ready (seconds)
            container_stop_timeout: Timeout when stopping containers (seconds)
            start_port_range: Dict with starting ports for different services
        """
        self.agent = agent
        self.task = task
        self.host = host
        self.port = port
        self.client_prefix = client_prefix

        # Initialize logger early
        self.logger = logging.getLogger(f"{agent.name}.Sandbox")

        # Set timeout configurations early so they're available during setup
        self.download_retries = download_retries
        self.vm_ready_timeout = vm_ready_timeout
        self.container_stop_timeout = container_stop_timeout

        # Use default Ubuntu VM container config if none provided
        self.config = container_config or ContainerConfig.ubuntu_vm()

        self.container_name = container_name or f"{client_prefix}-{agent.name}"

        # Configure directories
        self.project_root = PROJECT_ROOT
        self.volume_directory = volume_directory or VOLUME_DIRECTORY
        self.vms_dir = vms_dir or os.path.join(self.project_root, "vms")

        # Create directories if they don't exist
        os.makedirs(self.vms_dir, exist_ok=True)
        os.makedirs(self.volume_directory, exist_ok=True)

        # Additional ports for VM mode
        self.vnc_port = None
        self.server_port = None
        self.chromium_port = None
        self.vlc_port = None
        self.client = None
        self.container = None
        self.vm_path = None

        # Default port ranges that can be overridden
        self.start_port_range = {
            "vnc": 8006,
            "server": 5000,
            "chromium": 9222,
            "vlc": 8080
        }
        if start_port_range:
            self.start_port_range.update(start_port_range)

        try:
            # Initialize Docker client
            self.docker_client = docker.from_env(**(docker_env or {}))

            # VM preparation (only if using VM)
            if self.config.is_vm:
                temp_dir = Path(os.getenv("TEMP") if platform.system() == "Windows" else "/tmp")
                self.lock_file = temp_dir / "docker_port_allocation.lck"
                self.lock_file.parent.mkdir(parents=True, exist_ok=True)

                # Get VM image
                self.vm_path = self._get_vm_image()

            # Build the image if requested and a dockerfile is specified
            if build_image and self.config.dockerfile_path:
                self.config.build_image(self.docker_client, self.logger)

            # Create the container
            self.container = self._create_container()

            # Initialize sandbox client after container is created (non-VM mode only)
            if not self.config.is_vm:
                self.client = SandboxClient(f"ws://{host}:{port}", client_id=client_prefix)

        except Exception as e:
            self.logger.error(f"Error initializing sandbox: {e}")
            # Clean up any resources that might have been created
            self.cleanup()
            raise

    def _get_vm_image(self):
        """Get the path to the VM image for the configured OS type."""
        url = self.config.vm_image_url
        vm_name = url.split("/")[-1]

        if vm_name.endswith(".zip"):
            vm_name = vm_name[:-4]

        vm_path = os.path.join(self.vms_dir, vm_name)

        if not os.path.exists(vm_path):
            self.logger.info(f"VM image not found locally. Downloading {self.config.os_type} image...")
            vm_path = self._download_vm_image(url, self.vms_dir)

        self.logger.info(f"Using VM image: {vm_path}")
        return vm_path

    def _download_vm_image(self, url, vms_dir):
        """Download a VM image from the given URL using urllib3."""
        self.logger.info(f"Downloading VM image from: {url}")
        downloaded_file_name = url.split("/")[-1]
        downloaded_size = 0

        os.makedirs(vms_dir, exist_ok=True)
        downloaded_file_path = os.path.join(vms_dir, downloaded_file_name)

        retry_count = 0
        max_retries = self.download_retries

        while retry_count <= max_retries:
            headers = {}
            if os.path.exists(downloaded_file_path):
                downloaded_size = os.path.getsize(downloaded_file_path)
                headers["Range"] = f"bytes={downloaded_size}-"

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
                    self.logger.info("VM image already fully downloaded.")
                    break

                # Check for errors
                if response.status >= 400:
                    raise Exception(f"HTTP Error: {response.status}")

                # Get content length for progress tracking
                total_size = int(response.headers.get("content-length", 0))

                with open(downloaded_file_path, "ab") as file:
                    with tqdm(
                        desc="Progress",
                        total=total_size,
                        unit="iB",
                        unit_scale=True,
                        unit_divisor=1024,
                        initial=downloaded_size,
                        ascii=True,
                    ) as progress_bar:
                        try:
                            for chunk in response.stream(1024):
                                size = file.write(chunk)
                                progress_bar.update(size)
                            self.logger.info("Download completed successfully.")
                            # Break out of the retry loop on success
                            break
                        except (urllib3.exceptions.HTTPError, IOError) as e:
                            self.logger.error(f"Download error: {e}")
                            retry_count += 1
                            if retry_count <= max_retries:
                                time.sleep(5)
                                self.logger.info(f"Retrying download (attempt {retry_count}/{max_retries})...")
                            else:
                                self.logger.error("Maximum retry attempts reached.")
                                raise
                finally:
                    # Make sure to release resources even if an exception occurs
                    response.release_conn()

                # Verify checksum after successful download
                try:
                    checksum_url = f"{url}.sha256"
                    checksum_response = http.request("GET", checksum_url)
                    if checksum_response.status == 200:
                        expected_checksum = checksum_response.data.decode('utf-8').strip()
                        if self._verify_file_checksum(downloaded_file_path, expected_checksum):
                            self.logger.info("Checksum verification passed.")
                        else:
                            self.logger.warning("Checksum verification failed, but continuing anyway.")
                except Exception as e:
                    self.logger.warning(f"Could not verify checksum: {e}")

            except Exception as e:
                self.logger.error(f"Error during download: {e}")
                retry_count += 1
                if retry_count <= max_retries:
                    time.sleep(5)
                    self.logger.info(f"Retrying download (attempt {retry_count}/{max_retries})...")
                else:
                    self.logger.error("Maximum retry attempts reached.")
                    raise

        # Extract zip file if download was successful
        if downloaded_file_name.endswith(".zip"):
            # Unzip the downloaded file
            self.logger.info("Extracting VM image...")
            with zipfile.ZipFile(downloaded_file_path, "r") as zip_ref:
                zip_ref.extractall(vms_dir)
            self.logger.info(f"VM image extracted to: {vms_dir}")

            # Return path to the extracted VM image
            return os.path.join(vms_dir, downloaded_file_name[:-4])

        return downloaded_file_path

    def _verify_file_checksum(self, file_path, expected_checksum):
        """Verify file integrity using SHA256 checksum."""
        import hashlib

        if not os.path.exists(file_path):
            return False

        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read and update hash in chunks for memory efficiency
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)

        file_hash = sha256_hash.hexdigest()

        if file_hash != expected_checksum:
            self.logger.warning(f"Checksum verification failed for {file_path}")
            return False

        self.logger.info(f"Checksum verified for {file_path}")
        return True

    def _get_available_port(self, start_port: int) -> int:
        """Find next available port starting from start_port."""
        # Get system ports
        system_ports = {conn.laddr.port for conn in psutil.net_connections()}

        # Get Docker container ports
        docker_ports = set()
        for container in self.docker_client.containers.list():
            ports = container.attrs["NetworkSettings"]["Ports"]
            if ports:
                for port_mappings in ports.values():
                    if port_mappings:
                        docker_ports.update(int(p["HostPort"]) for p in port_mappings)

        used_ports = system_ports | docker_ports
        port = start_port
        while port < 65354:
            if port not in used_ports:
                return port
            port += 1
        raise PortAllocationError(f"No available ports found starting from {start_port}")

    def _create_container(self):
        """Create or use existing Docker container."""
        try:
            # Check if container already exists
            try:
                container = self.docker_client.containers.get(self.container_name)
                # If container exists but not running, start it
                if container.status != "running":
                    try:
                        container.start()
                        # Verify container started successfully
                        container.reload()
                        if container.status != "running":
                            raise Exception(f"Container exists but could not be started")
                    except Exception as e:
                        self.logger.error(f"Error starting existing container: {e}")
                        # If we can't start it, try to remove it and create new
                        try:
                            container.remove(force=True)
                            self.logger.info(f"Removed non-startable container: {self.container_name}")
                        except Exception:
                            self.logger.warning(f"Could not remove non-startable container: {self.container_name}")
                        # Create new container
                        return self._create_new_container()
                self.logger.info(f"Using existing container: {self.container_name}")
                return container
            except docker.errors.NotFound:
                return self._create_new_container()

        except Exception as e:
            self.logger.error(f"Error creating container: {e}")
            raise

    def _create_new_container(self):
        """Create appropriate container based on configuration."""
        if self.config.is_vm:
            return self._create_vm_container()
        else:
            return self._create_standard_container()

    def _create_standard_container(self):
        """Create a standard (non-VM) sandbox container."""
        # Get host screenshot directory
        host_screenshot_dir = os.path.join(self.volume_directory, "screenshots")
        os.makedirs(host_screenshot_dir, exist_ok=True)

        # Set up volumes
        volumes = {host_screenshot_dir: {"bind": "/tmp/screenshots", "mode": "rw"}}
        if self.config.volumes:
            volumes.update(self.config.volumes)

        # Set up environment
        environment = {"SANDBOX_HOST": "0.0.0.0", "SANDBOX_PORT": "8765", "VNCOPTIONS": "-disableBasicAuth"}
        if self.config.environment:
            environment.update(self.config.environment)

        # Set up ports
        ports = {
            "6901/tcp": None,
            "4901/tcp": None,
            "8765/tcp": self.port,
        }

        # Create container
        container = self.docker_client.containers.run(
            image=self.config.image_name,
            name=self.container_name,
            detach=True,
            ports=ports,
            environment=environment,
            volumes=volumes,
            shm_size=self.config.shm_size,
            tty=True,
            stdin_open=True,
            restart_policy={"Name": "unless-stopped"},
        )

        self.logger.info(f"Created new standard container: {self.container_name}")
        return container

    def _create_vm_container(self):
        """Create a VM-based container."""
        # Use a lock for port allocation to avoid race conditions
        lock = FileLock(str(self.lock_file), timeout=10)

        # Get host volume directory for persistence
        host_data_dir = os.path.join(self.volume_directory, f"vm_data_{self.config.os_type.lower()}")
        os.makedirs(host_data_dir, exist_ok=True)

        # Screenshots directory
        host_screenshot_dir = os.path.join(self.volume_directory, f"screenshots_{self.config.os_type.lower()}")
        os.makedirs(host_screenshot_dir, exist_ok=True)

        # Directory for custom server code
        custom_server_dir = os.path.join(self.project_root, "custom_server")
        if not os.path.exists(custom_server_dir):
            self.logger.info(f"Creating custom server directory at: {custom_server_dir}")
            os.makedirs(custom_server_dir, exist_ok=True)

        try:
            with lock:
                # Allocate all required ports using configured ranges
                self.vnc_port = self._get_available_port(self.start_port_range["vnc"])
                self.server_port = self._get_available_port(self.start_port_range["server"])
                self.chromium_port = self._get_available_port(self.start_port_range["chromium"])
                self.vlc_port = self._get_available_port(self.start_port_range["vlc"])

                # Environment settings for the VM container
                environment = {
                    "DISK_SIZE": VM_DISK_SIZE,
                    "RAM_SIZE": VM_RAM_SIZE,
                    "CPU_CORES": VM_CPU_CORES,
                }

                # Add custom environment variables
                if self.config.environment:
                    environment.update(self.config.environment)

                # Set up volumes
                volumes = {
                    os.path.abspath(self.vm_path): {"bind": "/System.qcow2", "mode": "ro"},
                    host_data_dir: {"bind": "/vm_data", "mode": "rw"},
                    host_screenshot_dir: {"bind": "/screenshots", "mode": "rw"},
                    os.path.abspath(custom_server_dir): {"bind": "/home/user/server", "mode": "rw"},
                }

                # Add custom volumes if specified
                if self.config.volumes:
                    volumes.update(self.config.volumes)

                # Create the VM container
                container = self.docker_client.containers.run(
                    image=self.config.image_name,
                    name=self.container_name,
                    environment=environment,
                    cap_add=self.config.capabilities,
                    devices=self.config.devices,
                    volumes=volumes,
                    ports={
                        8006: self.vnc_port,
                        5000: self.server_port,
                        9222: self.chromium_port,
                        8080: self.vlc_port
                    },
                    detach=True,
                )

            self.logger.info(
                f"Started VM container with ports - VNC: {self.vnc_port}, "
                f"Server: {self.server_port}, Chrome: {self.chromium_port}, VLC: {self.vlc_port}"
            )

            # Copy any additional server files
            self._copy_server_files_to_container(container)

            # Wait for VM to be ready
            self._wait_for_vm_ready()

            return container

        except Exception as e:
            self.logger.error(f"Error creating VM container: {e}")
            raise

    def _copy_server_files_to_container(self, container):
        """Copy additional server files to the container if needed."""
        try:
            # Example: copy a startup script and make it executable
            startup_script_path = os.path.join(self.project_root, "scripts", "vm_startup.sh")
            if os.path.exists(startup_script_path):
                # Create a tar archive of the file
                import io
                import tarfile

                file_data = io.BytesIO()
                with tarfile.open(fileobj=file_data, mode="w") as tar:
                    tar.add(startup_script_path, arcname="vm_startup.sh")
                file_data.seek(0)

                # Copy the file to the container
                container.put_archive("/home/user/", file_data)

                # Make the script executable
                container.exec_run("chmod +x /home/user/vm_startup.sh")

                # Restart the server service if needed
                container.exec_run("pkill -f 'python3 /home/user/server/main.py'")
                container.exec_run("cd /home/user/server && nohup python3 main.py > /dev/null 2>&1 &")

                self.logger.info("Custom server startup script deployed and executed")
        except Exception as e:
            self.logger.error(f"Error copying server files to container: {e}")

    def _wait_for_vm_ready(self, timeout: int = None):
        """Wait for VM to be ready by checking screenshot endpoint using urllib3."""
        if timeout is None:
            timeout = self.vm_ready_timeout

        start_time = time.time()
        last_log_time = 0
        retry_count = 0
        max_retries = 3  # Number of consecutive failures to tolerate

        # Create a local http client with shorter timeouts for status checks
        status_http = urllib3.PoolManager(
            retries=urllib3.Retry(2, redirect=1),
            timeout=urllib3.Timeout(connect=5.0, read=5.0)
        )

        def check_screenshot():
            nonlocal retry_count
            try:
                response = status_http.request(
                    "GET",
                    f"http://localhost:{self.server_port}/screenshot",
                    timeout=urllib3.Timeout(connect=5.0, read=5.0),
                )
                if response.status == 200:
                    retry_count = 0  # Reset retry count on success
                    return True
                return False
            except Exception:
                retry_count += 1
                # If we've had too many consecutive failures, check if container is still running
                if retry_count > max_retries:
                    try:
                        self.container.reload()
                        if self.container.status != "running":
                            raise Exception("Container stopped running during VM initialization")
                    except Exception as e:
                        self.logger.error(f"Container health check failed: {e}")
                        raise
                return False

        try:
            while (current_time := time.time()) - start_time < timeout:
                if check_screenshot():
                    self.logger.info(f"VM is ready! Total wait time: {current_time - start_time:.2f}s")
                    return True

                # Only log every 10 seconds to avoid spam
                if current_time - last_log_time >= 10:
                    self.logger.info(f"Checking if virtual machine is ready... ({current_time - start_time:.2f}s elapsed)")
                    last_log_time = current_time

                time.sleep(1)

            raise TimeoutError(f"VM failed to become ready within {timeout}s timeout period")
        finally:
            # Clean up local http client
            status_http.clear()

    async def connect(self):
        """Connect to the sandbox server in the container."""
        # Wait briefly for container to initialize
        await asyncio.sleep(2)

        if not self.config.is_vm:
            await self.client.connect()
        else:
            self.logger.info("VM container connected and ready to use")

    async def disconnect(self):
        """Disconnect from the sandbox server."""
        if not self.config.is_vm:
            await self.client.disconnect()
        else:
            self.logger.info("VM container disconnected")

    def stop_container(self, remove=False):
        """Stop the Docker container."""
        try:
            self.container.stop(timeout=self.container_stop_timeout)
            self.logger.info(f"Stopped container: {self.container_name}")

            # Add a small delay to ensure container has fully stopped
            time.sleep(0.5)

            if remove:
                self.container.remove()
                self.logger.info(f"Removed container: {self.container_name}")

            # Reset VM-specific ports
            if self.config.is_vm:
                self.vnc_port = None
                self.server_port = None
                self.chromium_port = None
                self.vlc_port = None

        except Exception as e:
            self.logger.error(f"Error stopping container: {e}")

    def cleanup(self):
        """Complete cleanup of resources."""
        try:
            # Disconnect any active connection first
            if hasattr(self, 'client') and self.client:
                try:
                    # Handle various async event loop situations
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            fut = asyncio.ensure_future(self.disconnect())
                            # Wait with a timeout
                            asyncio.wait_for(fut, timeout=5)
                        else:
                            asyncio.run(self.disconnect())
                    except (RuntimeError, asyncio.TimeoutError):
                        self.logger.warning("Could not disconnect cleanly, continuing cleanup")
                except Exception as e:
                    self.logger.error(f"Error disconnecting client: {e}")
                self.client = None  # Clear client reference

            # Stop the container
            if hasattr(self, 'container') and self.container:
                try:
                    # Force removal if needed
                    try:
                        self.container.stop(timeout=self.container_stop_timeout)
                    except Exception as e:
                        self.logger.warning(f"Error stopping container, trying force removal: {e}")

                    # Wait a moment
                    time.sleep(0.5)

                    try:
                        # Force=True means it will stop it first if needed
                        self.container.remove(force=True, v=True)  # v=True to remove volumes
                        self.logger.info(f"Removed container: {self.container_name}")
                    except Exception as e:
                        self.logger.error(f"Error removing container: {e}")
                except Exception as e:
                    self.logger.error(f"Error during container cleanup: {e}")

                self.container = None  # Clear container reference

            # Reset VM-specific ports
            self.vnc_port = None
            self.server_port = None
            self.chromium_port = None
            self.vlc_port = None

            # Additional cleanup
            self.logger.info(f"Sandbox for {self.agent.name} cleaned up")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    def get_vm_connection_info(self):
        """Get connection information for VM containers."""
        if not self.config.is_vm:
            return None

        if not all([self.server_port, self.chromium_port, self.vnc_port, self.vlc_port]):
            raise RuntimeError("VM not started - ports not allocated")

        return {
            "server_port": self.server_port,
            "chromium_port": self.chromium_port,
            "vnc_port": self.vnc_port,
            "vlc_port": self.vlc_port,
            "host": "localhost",
            "connection_string": f"localhost:{self.server_port}:{self.chromium_port}:{self.vnc_port}:{self.vlc_port}",
        }

    async def __aenter__(self):
        """Support for async context manager."""
        try:
            await self.connect()
            return self
        except Exception as e:
            self.logger.error(f"Error in async context entry: {e}")
            await self.__aexit__(None, e, None)
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup when exiting async context."""
        try:
            await self.disconnect()
        except Exception as e:
            self.logger.error(f"Error in async disconnect: {e}")
        finally:
            self.cleanup()

    def __enter__(self):
        """Support for synchronous context manager."""
        try:
            # Run connect in the appropriate event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    future = asyncio.ensure_future(self.connect())
                    loop.run_until_complete(future)
                else:
                    asyncio.run(self.connect())
            except RuntimeError:
                # No event loop exists yet
                asyncio.run(self.connect())
            return self
        except Exception as e:
            self.logger.error(f"Error in context entry: {e}")
            self.__exit__(None, e, None)
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup when exiting context."""
        try:
            # Run disconnect in the appropriate event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    future = asyncio.ensure_future(self.disconnect())
                    loop.run_until_complete(future)
                else:
                    asyncio.run(self.disconnect())
            except Exception:
                # Best effort, continue with cleanup
                pass
        finally:
            # Always do cleanup
            self.cleanup()

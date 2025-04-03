import asyncio
import logging
import os
import platform
import time
import zipfile
from pathlib import Path

import docker
import psutil
import requests
from filelock import FileLock
from smolagents import CodeAgent
from tqdm import tqdm

from sandbox_client import SandboxClient

PROJECT_ROOT = str(os.getenv("ROOT_DIR", Path(__file__).parent.parent))
DOCKERFILE_PATH = "environment/Dockerfile.kasm"
VMS_DIR = os.path.join(PROJECT_ROOT, "tmp")

# VM Image URLs
UBUNTU_X86_URL = "https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/main/Ubuntu.qcow2.zip"
WINDOWS_X86_URL = "https://huggingface.co/datasets/xlangai/windows_osworld/resolve/main/Windows-10-x64.qcow2.zip"


class PortAllocationError(Exception):
    """Raised when unable to find available port."""

    pass


class Sandbox:
    """
    Manages a sandbox client connecting to a sandbox server in a Docker container.
    Supports both standard sandbox environments and VM-based environments.
    """

    def __init__(
        self,
        agent: CodeAgent,
        task: str,
        host: str = "localhost",
        port: int = 8765,
        client_prefix: str = "sandbox",
        image: str = "kasm-ubuntu",
        container_name: str = None,
        build_image: bool = False,
        dockerfile_path: str = "environment/Dockerfile.kasm",
        build_args: dict = None,
        docker_env: dict = {},
        use_vm: bool = True,
        os_type: str = "Ubuntu",
    ):
        """
        Initialize the sandbox with a Docker container.

        Args:
            agent: The agent that will use this sandbox
            task: Description of the task
            host: The hostname where sandbox server will be accessible
            port: The port number for the sandbox server
            client_prefix: Prefix for client ID
            image: Docker image to use for the container
            container_name: Optional custom container name
            build_image: Whether to build the image before creating containers
            dockerfile_path: Path to the Dockerfile relative to project root
            build_args: Dictionary of build arguments to pass to Docker
            use_vm: Whether to use a VM-based container
            os_type: OS type for VM ("Ubuntu" or "Windows")
        """
        self.agent = agent
        self.task = task
        self.host = host
        self.port = port
        self.client_prefix = client_prefix
        self.image = image
        self.container_name = container_name or f"{client_prefix}-{agent.name}"
        self.use_vm = use_vm
        self.os_type = os_type

        # Additional ports for VM mode
        self.vnc_port = None
        self.server_port = None
        self.chromium_port = None
        self.vlc_port = None

        # Initialize logger early
        self.logger = logging.getLogger(f"{agent.name}.Sandbox")

        # Initialize Docker client
        self.docker_client = docker.from_env(**docker_env)

        # Get VM image if using VM mode
        if self.use_vm:
            self.vm_path = self._get_vm_image(os_type)
            temp_dir = Path(os.getenv("TEMP") if platform.system() == "Windows" else "/tmp")
            self.lock_file = temp_dir / "docker_port_allocation.lck"
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            # VM-based containers use the happysixd/osworld-docker image
            self.image = "happysixd/osworld-docker"
        else:
            # Build the standard image if requested
            if build_image:
                self._build_image(dockerfile_path, build_args or {})

        # Create the container
        self.container = self._create_container()

        # Initialize sandbox client after container is created
        if not self.use_vm:
            # Only initialize the standard sandbox client for non-VM mode
            self.client = SandboxClient(f"ws://{host}:{port}", client_id=client_prefix)

    def _get_available_port(self, start_port: int) -> int:
        """Find next available port starting from start_port."""
        # Get system ports
        system_ports = set(conn.laddr.port for conn in psutil.net_connections())

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

    def _download_vm_image(self, url, vms_dir):
        """Download a VM image from the given URL."""
        self.logger.info(f"Downloading VM image from: {url}")
        downloaded_file_name = url.split("/")[-1]
        downloaded_size = 0

        os.makedirs(vms_dir, exist_ok=True)
        downloaded_file_path = os.path.join(vms_dir, downloaded_file_name)

        while True:
            headers = {}
            if os.path.exists(downloaded_file_path):
                downloaded_size = os.path.getsize(downloaded_file_path)
                headers["Range"] = f"bytes={downloaded_size}-"

            with requests.get(url, headers=headers, stream=True) as response:
                if response.status_code == 416:
                    # File already fully downloaded
                    self.logger.info("VM image already fully downloaded.")
                    break

                response.raise_for_status()
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
                            for data in response.iter_content(chunk_size=1024):
                                size = file.write(data)
                                progress_bar.update(size)
                        except (requests.exceptions.RequestException, IOError) as e:
                            self.logger.error(f"Download error: {e}")
                            time.sleep(5)
                            self.logger.error("Retrying...")
                        else:
                            self.logger.info("Download completed successfully.")
                            break

        if downloaded_file_name.endswith(".zip"):
            # Unzip the downloaded file
            self.logger.info("Extracting VM image...")
            with zipfile.ZipFile(downloaded_file_path, "r") as zip_ref:
                zip_ref.extractall(vms_dir)
            self.logger.info(f"VM image extracted to: {vms_dir}")

            # Return path to the extracted VM image
            if downloaded_file_name.endswith(".zip"):
                return os.path.join(vms_dir, downloaded_file_name[:-4])

        return downloaded_file_path

    def _get_vm_image(self, os_type):
        """Get the path to the VM image for the specified OS type."""
        url = UBUNTU_X86_URL if os_type == "Ubuntu" else WINDOWS_X86_URL
        vm_name = url.split("/")[-1]

        if vm_name.endswith(".zip"):
            vm_name = vm_name[:-4]

        vm_path = os.path.join(VMS_DIR, vm_name)

        if not os.path.exists(vm_path):
            self.logger.info(f"VM image not found locally. Downloading {os_type} image...")
            vm_path = self._download_vm_image(url, VMS_DIR)

        self.logger.info(f"Using VM image: {vm_path}")
        return vm_path

    def _build_image(self, dockerfile_path, build_args):
        """Build the Docker image from a Dockerfile."""
        try:
            # Set default build args from your compose.yaml if not provided
            if not build_args:
                build_args = {"BASE_TAG": "develop", "BASE_IMAGE": "core-ubuntu-noble"}

            self.logger.info(f"Building image '{self.image}' from Dockerfile: {dockerfile_path}")

            # Build the image using the Docker API
            image, build_logs = self.docker_client.images.build(
                path=PROJECT_ROOT,  # Build context (root of your project)
                dockerfile=dockerfile_path,
                tag=self.image,
                buildargs=build_args,
                rm=True,  # Remove intermediate containers
            )

            # Log build output
            for log in build_logs:
                if "stream" in log:
                    log_line = log["stream"].strip()
                    if log_line:
                        self.logger.debug(log_line)

            self.logger.info(f"Successfully built image: {self.image}")
            return image

        except Exception as e:
            self.logger.error(f"Error building Docker image: {str(e)}")
            raise

    def _wait_for_vm_ready(self, timeout: int = 300):
        """Wait for VM to be ready by checking screenshot endpoint."""
        start_time = time.time()

        def check_screenshot():
            try:
                response = requests.get(f"http://localhost:{self.server_port}/screenshot", timeout=(10, 10))
                return response.status_code == 200
            except Exception:
                return False

        while time.time() - start_time < timeout:
            if check_screenshot():
                self.logger.info("VM is ready!")
                return True
            self.logger.info("Checking if virtual machine is ready...")
            time.sleep(1)

        raise TimeoutError("VM failed to become ready within timeout period")

    def _create_container(self):
        """Create a Docker container from the specified image."""
        try:
            # Check if container already exists
            try:
                container = self.docker_client.containers.get(self.container_name)
                # If container exists but not running, start it
                if container.status != "running":
                    container.start()
                self.logger.info(f"Using existing container: {self.container_name}")
                return container
            except docker.errors.NotFound:
                if self.use_vm:
                    return self._create_vm_container()
                else:
                    return self._create_standard_container()
        except Exception as e:
            self.logger.error(f"Error creating container: {e}")
            raise

    def _create_standard_container(self):
        """Create a standard sandbox container."""
        # Get host screenshot directory
        volume_dir = os.getenv("VOLUME_DIRECTORY", ".")
        host_screenshot_dir = os.path.join(volume_dir, "screenshots")
        os.makedirs(host_screenshot_dir, exist_ok=True)

        # Create new container
        container = self.docker_client.containers.run(
            image=self.image,
            name=self.container_name,
            detach=True,  # Run in background
            ports={
                "6901/tcp": None,  # Let Docker assign random port
                "4901/tcp": None,
                "8765/tcp": self.port,  # Map sandbox port to host port
            },
            environment={"SANDBOX_HOST": "0.0.0.0", "SANDBOX_PORT": "8765", "VNCOPTIONS": "-disableBasicAuth"},
            volumes={host_screenshot_dir: {"bind": "/tmp/screenshots", "mode": "rw"}},
            shm_size="512m",
            tty=True,
            stdin_open=True,
            restart_policy={"Name": "unless-stopped"},
        )
        self.logger.info(f"Created new container: {self.container_name}")
        return container

    def _create_vm_container(self):
        """Create a VM-based container."""
        # Use a lock for port allocation to avoid race conditions
        lock = FileLock(str(self.lock_file), timeout=10)

        # Get host volume directory for persistence
        volume_dir = os.getenv("VOLUME_DIRECTORY", ".")
        host_data_dir = os.path.join(volume_dir, f"vm_data_{self.os_type.lower()}")
        os.makedirs(host_data_dir, exist_ok=True)

        # If you want a specific screenshots directory as well
        host_screenshot_dir = os.path.join(volume_dir, f"screenshots_{self.os_type.lower()}")
        os.makedirs(host_screenshot_dir, exist_ok=True)

        try:
            with lock:
                # Allocate all required ports
                self.vnc_port = self._get_available_port(8006)
                self.server_port = self._get_available_port(5000)
                self.chromium_port = self._get_available_port(9222)
                self.vlc_port = self._get_available_port(8080)

                # Environment settings for the VM container
                environment = {"DISK_SIZE": "32G", "RAM_SIZE": "4G", "CPU_CORES": "4"}

                # Create the VM container with additional volumes
                container = self.docker_client.containers.run(
                    image=self.image,
                    name=self.container_name,
                    environment=environment,
                    cap_add=["NET_ADMIN"],
                    devices=["/dev/kvm"],
                    volumes={
                        os.path.abspath(self.vm_path): {"bind": "/System.qcow2", "mode": "ro"},
                        host_data_dir: {"bind": "/vm_data", "mode": "rw"},
                        host_screenshot_dir: {"bind": "/screenshots", "mode": "rw"},
                    },
                    ports={8006: self.vnc_port, 5000: self.server_port, 9222: self.chromium_port, 8080: self.vlc_port},
                    detach=True,
                )

            self.logger.info(
                f"Started VM container with ports - VNC: {self.vnc_port}, "
                f"Server: {self.server_port}, Chrome: {self.chromium_port}, VLC: {self.vlc_port}"
            )
            self.logger.info(f"VM data directory mounted at: {host_data_dir}")
            self.logger.info(f"VM screenshots directory mounted at: {host_screenshot_dir}")

            # Wait for VM to be ready
            self._wait_for_vm_ready()

            return container

        except Exception as e:
            self.logger.error(f"Error creating VM container: {e}")
            raise

    async def connect(self):
        """Connect to the sandbox server in the container."""
        # Wait briefly for container to initialize
        await asyncio.sleep(2)

        if not self.use_vm:
            await self.client.connect()
        else:
            self.logger.info("VM container connected and ready to use")

    async def disconnect(self):
        """Disconnect from the sandbox server."""
        if not self.use_vm:
            await self.client.disconnect()
        else:
            self.logger.info("VM container disconnected")

    def stop_container(self, remove=False):
        """Stop the Docker container."""
        try:
            self.container.stop(timeout=5)
            self.logger.info(f"Stopped container: {self.container_name}")
            if remove:
                self.container.remove()
                self.logger.info(f"Removed container: {self.container_name}")

            # Reset VM-specific ports
            if self.use_vm:
                self.vnc_port = None
                self.server_port = None
                self.chromium_port = None
                self.vlc_port = None

        except Exception as e:
            self.logger.error(f"Error stopping container: {e}")

    def get_vm_connection_info(self):
        """Get connection information for VM containers."""
        if not self.use_vm:
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

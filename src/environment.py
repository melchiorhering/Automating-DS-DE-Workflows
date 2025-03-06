import logging
import os
from pathlib import Path

import docker
from smolagents import CodeAgent

from src.sandbox_client import SandboxClient

PROJECT_ROOT = str(os.getenv("ROOT_DIR", Path(__file__).parent.parent))
DOCKERFILE_PATH = "environment/Dockerfile.kasm"


class Sandbox:
    """
    Manages a sandbox client connecting to a sandbox server in a Docker container.
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
        """
        self.agent = agent
        self.task = task
        self.host = host
        self.port = port
        self.client_prefix = client_prefix
        self.image = image
        self.container_name = container_name or f"{client_prefix}-{agent.name}"

        # Initialize logger early
        self.logger = logging.getLogger(f"{agent.name}.Sandbox")

        # Initialize Docker client
        self.docker_client = docker.from_env()

        # Build the image if requested
        if build_image:
            self._build_image(dockerfile_path, build_args or {})

        # Create the container
        self.container = self._create_container()

        # Initialize sandbox client after container is created
        self.client = SandboxClient(f"ws://{host}:{port}", client_id=client_prefix)

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
                # Get host screenshot directory (same as in compose.yaml)
                volume_dir = os.getenv("VOLUME_DIRECTORY", ".")
                host_screenshot_dir = os.path.join(volume_dir, "screenshots")
                os.makedirs(host_screenshot_dir, exist_ok=True)

                # Create new container if it doesn't exist
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
                    volumes={
                        host_screenshot_dir: {"bind": "/tmp/screenshots", "mode": "rw"}  # Match compose.yaml approach
                    },
                    shm_size="512m",
                    tty=True,
                    stdin_open=True,
                    restart_policy={"Name": "unless-stopped"},
                )
                self.logger.info(f"Created new container: {self.container_name}")
                return container
        except Exception as e:
            self.logger.error(f"Error creating container: {e}")
            raise

    async def connect(self):
        """Connect to the sandbox server in the container."""
        # Wait briefly for container to initialize
        import asyncio

        await asyncio.sleep(2)
        await self.client.connect()

    async def disconnect(self):
        """Disconnect from the sandbox server."""
        await self.client.disconnect()

    def stop_container(self, remove=False):
        """Stop the Docker container."""
        try:
            self.container.stop(timeout=5)
            self.logger.info(f"Stopped container: {self.container_name}")
            if remove:
                self.container.remove()
                self.logger.info(f"Removed container: {self.container_name}")
        except Exception as e:
            self.logger.error(f"Error stopping container: {e}")

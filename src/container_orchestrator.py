import logging
import os
import time
from typing import Dict, List, Tuple

import docker


class ContainerOrchestrator:
    """
    Dynamically manages sandbox containers for parallel execution.
    """

    def __init__(
        self,
        base_name: str = "kasm-ubuntu",
        image_name: str = "master-thesis-kasm-ubuntu",
        base_port: int = 8765,
        min_containers: int = 1,
        max_containers: int = 10,
    ):
        """
        Initialize the container orchestrator.

        Args:
            base_name: Base name for the containers
            image_name: Docker image to use
            base_port: Starting port for WebSocket servers
            min_containers: Minimum number of containers to maintain
            max_containers: Maximum number of containers allowed
        """
        self.logger = logging.getLogger("ContainerOrchestrator")
        self.base_name = base_name
        self.image_name = image_name
        self.base_port = base_port
        self.min_containers = min_containers
        self.max_containers = max_containers

        # Connect to Docker
        self.client = docker.from_env()

        # Track active containers and their ports
        self.containers: Dict[str, Dict] = {}

        # Initialize with minimum containers
        self._ensure_min_containers()

    def _ensure_min_containers(self) -> None:
        """Ensure the minimum number of containers are running."""
        active_count = len(self.containers)
        if active_count < self.min_containers:
            to_create = self.min_containers - active_count
            self.logger.info(f"Starting {to_create} initial container(s)")
            for _ in range(to_create):
                self.create_container()

    def create_container(self) -> Tuple[str, int]:
        """
        Create a new sandbox container.

        Returns:
            Tuple of (container_id, websocket_port)
        """
        if len(self.containers) >= self.max_containers:
            raise RuntimeError(f"Maximum container limit ({self.max_containers}) reached")

        # Generate a unique name and find an available port
        container_number = len(self.containers) + 1
        container_name = f"{self.base_name}-{container_number}"
        ws_port = self.base_port + container_number
        vnc_port = 6901 + container_number

        # Create shared volume directory if it doesn't exist
        screenshots_dir = os.path.join(os.getcwd(), "screenshots", f"container-{container_number}")
        os.makedirs(screenshots_dir, exist_ok=True)

        # Create the container
        container = self.client.containers.run(
            self.image_name,
            name=container_name,
            detach=True,
            ports={
                "8765/tcp": ws_port,  # WebSocket port
                "6901/tcp": vnc_port,  # VNC port
            },
            environment={
                "VNC_PW": "password",
                # "VNC_RESOLUTION": "1120x1120",
                "SANDBOX_HOST": "0.0.0.0",
                "SANDBOX_PORT": "8765",
            },
            volumes={screenshots_dir: {"bind": "/tmp/screenshots", "mode": "rw"}},
            shm_size="512m",
        )

        # Wait for container to be ready (simple wait, could be improved)
        time.sleep(2)

        # Store container info
        self.containers[container.id] = {
            "name": container_name,
            "ws_port": ws_port,
            "vnc_port": vnc_port,
            "container": container,
            "active_tasks": 0,
        }

        self.logger.info(f"Created container {container_name} with WebSocket on port {ws_port}")
        return container.id, ws_port

    def scale_to(self, count: int) -> None:
        """
        Scale to specified number of containers.

        Args:
            count: Target number of containers
        """
        current_count = len(self.containers)
        if count < self.min_containers:
            count = self.min_containers
        elif count > self.max_containers:
            count = self.max_containers

        if count > current_count:
            # Scale up
            for _ in range(count - current_count):
                self.create_container()
        elif count < current_count:
            # Scale down (remove least busy containers)
            containers_by_load = sorted(self.containers.items(), key=lambda x: x[1]["active_tasks"])

            for container_id, _ in containers_by_load[: current_count - count]:
                self.remove_container(container_id)

    def remove_container(self, container_id: str) -> None:
        """
        Remove a specific container.

        Args:
            container_id: ID of container to remove
        """
        if container_id not in self.containers:
            self.logger.warning(f"Container {container_id} not found")
            return

        container_info = self.containers[container_id]
        container = container_info["container"]

        try:
            container.stop(timeout=5)
            container.remove()
            self.logger.info(f"Removed container {container_info['name']}")
        except Exception as e:
            self.logger.error(f"Error removing container {container_info['name']}: {e}")

        del self.containers[container_id]

    def get_available_container(self) -> Tuple[str, int]:
        """
        Get the least busy available container.

        Returns:
            Tuple of (container_id, websocket_port)
        """
        if not self.containers:
            return self.create_container()

        # Find least busy container
        container_id, container_info = min(self.containers.items(), key=lambda x: x[1]["active_tasks"])

        # Update task count
        self.containers[container_id]["active_tasks"] += 1

        return container_id, container_info["ws_port"]

    def release_container(self, container_id: str) -> None:
        """
        Mark a container as having completed a task.

        Args:
            container_id: ID of the container to release
        """
        if container_id in self.containers:
            self.containers[container_id]["active_tasks"] = max(0, self.containers[container_id]["active_tasks"] - 1)

    def get_all_containers(self) -> List[Tuple[str, int]]:
        """
        Get all available containers and their WebSocket ports.

        Returns:
            List of (container_id, websocket_port) tuples
        """
        return [(cid, info["ws_port"]) for cid, info in self.containers.items()]

    def cleanup(self) -> None:
        """Remove all containers managed by this orchestrator."""
        for container_id, container_info in list(self.containers.items()):
            try:
                container_info["container"].stop(timeout=5)
                container_info["container"].remove()
                self.logger.info(f"Stopped and removed container {container_info['name']}")
            except Exception as e:
                self.logger.error(f"Error cleaning up container {container_info['name']}: {e}")

        self.containers.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

import asyncio
import logging
from typing import Any, Dict, List, Optional

from container_orchestrator import ContainerOrchestrator
from sandbox_client import SandboxClient


class ParallelSandboxClient:
    """
    Client for parallel execution across multiple sandbox containers.
    """

    def __init__(
        self,
        orchestrator: Optional[ContainerOrchestrator] = None,
        host: str = "localhost",
        min_containers: int = 1,
        max_containers: int = 5,
    ):
        """
        Initialize the parallel sandbox client.

        Args:
            orchestrator: Optional existing orchestrator instance
            host: Host for WebSocket connections
            min_containers: Minimum number of containers to maintain
            max_containers: Maximum number of containers allowed
        """
        self.logger = logging.getLogger("ParallelSandboxClient")
        self.host = host

        # Create or use provided orchestrator
        if orchestrator:
            self.orchestrator = orchestrator
            self._owns_orchestrator = False
        else:
            self.orchestrator = ContainerOrchestrator(min_containers=min_containers, max_containers=max_containers)
            self._owns_orchestrator = True

        # Track active client connections
        self.active_clients: Dict[str, SandboxClient] = {}

    async def get_client(self, container_id: Optional[str] = None) -> SandboxClient:
        """
        Get a client connected to a specific container or least busy container.

        Args:
            container_id: Optional ID of specific container to connect to

        Returns:
            Connected SandboxClient instance
        """
        if container_id and container_id in self.orchestrator.containers:
            # Use specified container
            ws_port = self.orchestrator.containers[container_id]["ws_port"]
        else:
            # Get least busy container
            container_id, ws_port = self.orchestrator.get_available_container()

        # Create client if not already connected to this container
        if container_id not in self.active_clients:
            uri = f"ws://{self.host}:{ws_port}"
            client = SandboxClient(uri, client_id=f"parallel-{container_id[:8]}")
            await client.connect()
            self.active_clients[container_id] = client

        return self.active_clients[container_id]

    async def execute_in_parallel(
        self, action: str, data_list: List[Dict[str, Any]], container_count: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute the same action across multiple containers with different data.

        Args:
            action: Action name to execute
            data_list: List of data dictionaries to send with the action
            container_count: Optional number of containers to use (scales if needed)

        Returns:
            List of results from all executions
        """
        # Scale containers if requested
        if container_count:
            self.orchestrator.scale_to(container_count)

        # Create tasks for parallel execution
        tasks = []
        container_ids = []

        for data in data_list:
            # Get a container and create a task
            container_id, _ = self.orchestrator.get_available_container()
            container_ids.append(container_id)
            client = await self.get_client(container_id)
            tasks.append(client.send_command(action, data))

        # Execute all tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Release containers
        for container_id in container_ids:
            self.orchestrator.release_container(container_id)

        # Process results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Error in task {i}: {result}")
                processed_results.append({"status": "error", "message": str(result)})
            else:
                processed_results.append(result)

        return processed_results

    async def execute_screenshots_parallel(self, count: int) -> List[Dict[str, Any]]:
        """
        Take screenshots from multiple containers in parallel.

        Args:
            count: Number of screenshots to take (will scale containers if needed)

        Returns:
            List of screenshot results
        """
        # Make sure we have enough containers
        if count > self.orchestrator.max_containers:
            count = self.orchestrator.max_containers
            self.logger.warning(f"Requested {count} containers but limited to {self.orchestrator.max_containers}")

        self.orchestrator.scale_to(count)

        # Take screenshots in parallel
        container_infos = self.orchestrator.get_all_containers()[:count]
        tasks = []

        for container_id, _ in container_infos:
            client = await self.get_client(container_id)
            tasks.append(client.get_screenshot_with_metadata())

        return await asyncio.gather(*tasks)

    async def close(self) -> None:
        """Close all client connections."""
        close_tasks = []
        for client in self.active_clients.values():
            close_tasks.append(client.disconnect())

        if close_tasks:
            await asyncio.gather(*close_tasks)

        self.active_clients.clear()

        # Cleanup orchestrator if we created it
        if self._owns_orchestrator:
            self.orchestrator.cleanup()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

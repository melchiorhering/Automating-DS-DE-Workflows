"""
Agent VM Environment Orchestrator
=================================
Manages multiple VM environments, each with its own sandbox server,
allowing AI agents to operate in isolated environments.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, Optional

import docker
from environment import Environment, VMConfig
from sandbox.qemu import QemuVMManager, SSHManager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AgentOrchestrator")


@dataclass
class AgentEnvironmentInfo:
    """Information about an agent's VM environment."""

    # Identifiers
    agent_id: str
    container_name: str

    # Connection info
    host: str = "localhost"
    ssh_port: Optional[int] = None
    vnc_port: Optional[int] = None
    ws_port: Optional[int] = None  # WebSocket server port

    # Storage
    instance_dir: Optional[str] = None
    shared_dir: Optional[str] = None

    # Status
    status: str = "initializing"
    server_running: bool = False


class AgentEnvironmentOrchestrator:
    """
    Orchestrates multiple VM environments for AI agents.
    Each agent gets its own isolated VM environment with a sandbox server.
    """

    def __init__(
        self,
        base_vm_dir: str = "docker/vms/ubuntu-noble",
        environments_dir: str = "docker/environments",
        server_dir: str = "server",
        shared_dir: str = "docker/shared",
        max_parallel_setups: int = 3,
    ):
        """
        Initialize the orchestrator.

        Args:
            base_vm_dir: Path to the base VM image
            environments_dir: Directory for environment instances
            server_dir: Path to the sandbox server code
            shared_dir: Directory shared between host and VMs
            max_parallel_setups: Maximum number of parallel VM setups
        """
        self.base_vm_dir = os.path.abspath(base_vm_dir)
        self.environments_dir = os.path.abspath(environments_dir)
        self.server_dir = os.path.abspath(server_dir)
        self.shared_dir = os.path.abspath(shared_dir)

        # Ensure directories exist
        for directory in [self.environments_dir, self.shared_dir]:
            os.makedirs(directory, exist_ok=True)

        # Setup docker client
        self.docker_client = docker.from_env()

        # Create VM environment manager
        self.config = VMConfig(
            initial_vm_dir=self.base_vm_dir,
            vm_environments=self.environments_dir,
            server_dir=self.server_dir,
        )
        self.env_manager = Environment(config=self.config, docker_client=self.docker_client, logger=logger)

        # Track agent environments
        self.agent_environments: Dict[str, AgentEnvironmentInfo] = {}

        # Executor for parallel setup
        self.executor = ThreadPoolExecutor(max_workers=max_parallel_setups)

        # Startup states
        self._starting_up = False
        self._shutdown_requested = False

    def create_agent_environment(self, agent_id: str) -> AgentEnvironmentInfo:
        """
        Create a new VM environment for an agent.

        Args:
            agent_id: Unique identifier for the agent

        Returns:
            Environment information for the agent
        """
        if agent_id in self.agent_environments:
            logger.info(f"Environment for agent {agent_id} already exists")
            return self.agent_environments[agent_id]

        logger.info(f"Creating environment for agent {agent_id}")

        # Create agent-specific shared directory
        agent_shared_dir = os.path.join(self.shared_dir, agent_id)
        os.makedirs(agent_shared_dir, exist_ok=True)

        # Create container name
        container_name = f"agent_{agent_id}_vm"

        # Create environment info
        env_info = AgentEnvironmentInfo(
            agent_id=agent_id, container_name=container_name, shared_dir=agent_shared_dir, status="creating"
        )

        # Store in our registry
        self.agent_environments[agent_id] = env_info

        # Start VM asynchronously
        self.executor.submit(self._setup_vm_environment, agent_id)

        return env_info

    def _setup_vm_environment(self, agent_id: str) -> None:
        """
        Set up the VM environment for an agent (runs in a separate thread).

        Args:
            agent_id: Agent ID to set up environment for
        """
        env_info = self.agent_environments[agent_id]

        try:
            # Start VM
            container, connection_info = self.env_manager.start_vm(name_prefix=f"agent_{agent_id}")

            # Update environment info with connection details
            env_info.ssh_port = connection_info.get("ssh_port")
            env_info.vnc_port = connection_info.get("host_vnc")
            env_info.instance_dir = connection_info.get("instance_dir")
            env_info.status = "vm_started"

            # Wait for VM to fully boot
            logger.info(f"Waiting for VM {agent_id} to boot completely...")
            time.sleep(20)  # Give VM time to boot

            # Configure shared directory and start sandbox server
            self._configure_and_start_server(agent_id, container, connection_info)

        except Exception as e:
            logger.error(f"Failed to set up VM environment for agent {agent_id}: {e}")
            env_info.status = "failed"

    def _configure_and_start_server(self, agent_id: str, container, connection_info: dict) -> None:
        """
        Configure shared directory and start sandbox server in VM.

        Args:
            agent_id: Agent ID
            container: Docker container
            connection_info: VM connection information
        """
        env_info = self.agent_environments[agent_id]

        try:
            # Get the VM's SSH port
            ssh_port = env_info.ssh_port

            # Create a VM manager for this VM
            vm_manager = QemuVMManager(docker_client=self.docker_client)

            ssh_manager = SSHManager(hostname="localhost", port=ssh_port, username="sandbox-user", password="password")

            # Wait for SSH to be available
            if not vm_manager.wait_for_ssh(
                hostname="localhost",
                port=ssh_port,
                username="sandbox-user",
                password="password",
                max_attempts=15,
                initial_delay=10,
            ):
                raise Exception(f"SSH not available for agent {agent_id} VM")

            # Connect via SSH
            ssh_manager.connect()

            # Copy server files to VM
            logger.info(f"Copying sandbox server files to VM for agent {agent_id}")
            ssh_manager.transfer_directory(
                self.server_dir, "/home/sandbox-user/server", skip_items=["__pycache__", ".git"]
            )

            # Configure shared directory in VM
            vm_manager.configure_9p_in_vm(ssh_manager=ssh_manager, mount_tag="shared", mount_point="/shared")

            # Create screenshot directory
            screenshot_dir = vm_manager.setup_screenshot_directory(
                ssh_manager=ssh_manager, mount_tag="shared", vm_id=agent_id
            )

            # Install required packages
            logger.info(f"Installing required packages for agent {agent_id}")
            result = ssh_manager.exec_command("cd /home/sandbox-user/server && uv sync")
            if result["status"] != 0:
                logger.warning(f"Package installation warning: {result['stderr']}")

            # Start sandbox server on a specific port (SSH port + 1000 to avoid conflicts)
            ws_port = ssh_port + 1000
            logger.info(f"Starting sandbox server for agent {agent_id} on port {ws_port}")

            # Port forward the websocket port
            docker_port_cmd = f"""docker port {env_info.container_name} | grep 8765/tcp ||
                                 docker exec {env_info.container_name} bash -c "iptables -t nat -A PREROUTING -p tcp --dport 8765 -j DNAT --to-destination :8765"
                              """
            os.system(docker_port_cmd)

            # Start server in the background
            start_cmd = "cd /home/sandbox-user/server && HOST=0.0.0.0 PORT=8765 nohup python -m sandbox_server > /tmp/server.log 2>&1 &"
            result = ssh_manager.exec_command(start_cmd)

            # Update environment info
            env_info.ws_port = ws_port
            env_info.status = "ready"
            env_info.server_running = True

            logger.info(f"Environment for agent {agent_id} is ready at ws://localhost:{ws_port}")

        except Exception as e:
            logger.error(f"Failed to configure and start server for agent {agent_id}: {e}")
            env_info.status = "failed"
        finally:
            # Close SSH connection
            if "ssh_manager" in locals():
                ssh_manager.close()

    def get_environment_info(self, agent_id: str) -> Optional[AgentEnvironmentInfo]:
        """
        Get information about an agent's environment.

        Args:
            agent_id: Agent ID

        Returns:
            Environment information or None if not found
        """
        return self.agent_environments.get(agent_id)

    def get_websocket_url(self, agent_id: str) -> Optional[str]:
        """
        Get the WebSocket URL for an agent's sandbox server.

        Args:
            agent_id: Agent ID

        Returns:
            WebSocket URL or None if not available
        """
        env_info = self.agent_environments.get(agent_id)
        if env_info and env_info.ws_port and env_info.server_running:
            return f"ws://{env_info.host}:{env_info.ws_port}"
        return None

    def shutdown_agent_environment(self, agent_id: str) -> bool:
        """
        Shutdown an agent's VM environment.

        Args:
            agent_id: Agent ID

        Returns:
            True if successful, False otherwise
        """
        if agent_id not in self.agent_environments:
            logger.warning(f"No environment found for agent {agent_id}")
            return False

        env_info = self.agent_environments[agent_id]

        try:
            # Find the container
            container = self.docker_client.containers.get(env_info.container_name)

            # Stop the VM
            self.env_manager.stop_vm(container, remove_instance=True)

            # Update status
            env_info.status = "shutdown"
            env_info.server_running = False

            logger.info(f"Environment for agent {agent_id} has been shutdown")
            return True

        except docker.errors.NotFound:
            logger.warning(f"Container for agent {agent_id} not found")
            env_info.status = "not_found"
            return False
        except Exception as e:
            logger.error(f"Failed to shutdown environment for agent {agent_id}: {e}")
            env_info.status = "shutdown_failed"
            return False

    def shutdown_all(self) -> None:
        """Shutdown all agent environments."""
        logger.info("Shutting down all agent environments")

        for agent_id in list(self.agent_environments.keys()):
            self.shutdown_agent_environment(agent_id)

        # Clean up executor
        self.executor.shutdown(wait=True)

        logger.info("All agent environments have been shutdown")

    def __del__(self):
        """Cleanup on destruction."""
        if not self._shutdown_requested:
            try:
                self.shutdown_all()
            except:
                pass


# Example usage
if __name__ == "__main__":
    orchestrator = AgentEnvironmentOrchestrator()

    # Create environments for 3 agents
    for i in range(3):
        agent_id = f"agent_{i}"
        env_info = orchestrator.create_agent_environment(agent_id)
        print(f"Created environment for {agent_id}: {env_info}")

    # Wait for all environments to be ready
    try:
        while True:
            all_ready = True
            for agent_id, env_info in orchestrator.agent_environments.items():
                if env_info.status != "ready":
                    all_ready = False
                    break

            if all_ready:
                break

            time.sleep(5)

        print("All environments are ready. Press Ctrl+C to shutdown.")

        # Keep running until interrupted
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        orchestrator.shutdown_all()

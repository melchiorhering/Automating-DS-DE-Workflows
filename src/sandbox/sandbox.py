"""SandboxVMManager — thin wrapper around VMManager that bootstraps
    the agent services (FastAPI + Jupyter) once the base VM is up.

    It relies on the new VMManager.start() method, which guarantees that:
    1. the Docker container is running
    2. sshd inside the guest is reachable
    3. a *persistent* SSH session is opened and cached on self.ssh

Consequently, SandboxVMManager no longer re‑creates SSHClient objects.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Optional

import requests
from smolagents import AgentLogger, LogLevel

from .configs import SandboxVMConfig
from .errors import RemoteCommandError, VMOperationError
from .virtualmachine import VMManager  # our updated persistent‑session base


class SandboxClient:
    """HTTP helper for the FastAPI service inside the sandbox VM."""

    def __init__(self, host: str, port: int):
        self.base_url = f"http://{host}:{port}"

    def health(self):
        return requests.get(f"{self.base_url}/health").json()

    def take_screenshot(self, method: str = "pyautogui"):
        return requests.get(f"{self.base_url}/screenshot", params={"method": method}).json()

    def start_recording(self):
        return requests.get(f"{self.base_url}/record", params={"mode": "start"}).json()

    def stop_recording(self):
        return requests.get(f"{self.base_url}/record", params={"mode": "stop"}).json()


class SandboxVMManager(VMManager):
    """Specialized VMManager that wires FastAPI + Jupyter kernels inside the guest."""

    def __init__(
        self,
        config: SandboxVMConfig,
        logger: Optional[AgentLogger] = None,
        preserve_on_exit: bool = False,
        **kwargs,
    ):
        if not isinstance(config, SandboxVMConfig):
            raise TypeError("SandboxVMManager requires SandboxVMConfig")
        super().__init__(config=config, logger=logger, **kwargs)

        # if we had to spin it up, we'll clean it later unless preserve_on_exit
        self._should_cleanup = not (self.container and self.container.status == "running")
        self._preserve_on_exit = preserve_on_exit

        self.logger.log(f"Initialization _should_cleanup set to: {self._should_cleanup}", level=LogLevel.DEBUG)
        if self._preserve_on_exit:
            self.logger.log("⚠️ Container files will be preserved on exit", level=LogLevel.INFO)

    # ------------------------------------------------------------------
    # Context helpers ---------------------------------------------------
    # ------------------------------------------------------------------
    def connect_or_start(self):
        """Either reconnect to a running container or bootstrap anew."""
        if self.container and self.container.status == "running":
            self.logger.log("🔁 Detected running container. Reconnecting...", level=LogLevel.INFO)
            self.reconnect()
        else:
            self.logger.log_rule("🚀 Starting new sandbox VM")
            self.__enter__()

    @contextlib.contextmanager
    def sandbox_vm_context(self):
        try:
            self.__enter__()
            yield self
        finally:
            self.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # Enter/exit --------------------------------------------------------
    # ------------------------------------------------------------------
    def __enter__(self) -> "SandboxVMManager":
        try:
            self.start_agent_vm()
            self._should_cleanup = False
            return self
        except Exception as e:
            self.logger.log_error(f"❌ Exception during VM startup: {e}")
            self.cleanup(delete_storage=True)
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        delete_storage = not self._preserve_on_exit
        reason = "⚠️ Exiting VM context with error," if (exc_type or self._should_cleanup) else "🧹 Normal exit,"
        action = " preserving container files..." if not delete_storage else " cleaning up VM container..."
        self.logger.log(reason + action, level=LogLevel.INFO)
        self.cleanup(delete_storage=delete_storage)
        return False

    # ------------------------------------------------------------------
    # Internal helpers --------------------------------------------------
    # ------------------------------------------------------------------
    def _ensure_mounted(self, mount_point: str, tag: str):
        self.logger.log(f"🔍 Checking if {mount_point} is mounted...", level=LogLevel.INFO)
        try:
            self.ssh.exec_command(f"mountpoint -q {mount_point}")
            self.logger.log(f"✅ {mount_point} already mounted", level=LogLevel.DEBUG)
            return
        except RemoteCommandError as e:
            if e.status == 32:  # not a mountpoint
                self.logger.log(f"🔧 {mount_point} not mounted. Mounting now...", level=LogLevel.INFO)
            else:
                self.logger.log_error(f"❌ mountpoint check failed (exit {e.status}): {e.stderr}")
                raise

        self.ssh.exec_command(f"mount -t 9p -o trans=virtio {tag} {mount_point}", as_root=True)
        self.logger.log(f"✅ Mounted {tag} → {mount_point}", level=LogLevel.INFO)

    def _wait_for_services(self, timeout: float = 120.0, interval: float = 10):
        self.logger.log_rule("🔎 Services Check")
        fastapi_url = f"http://{self.cfg.host_sandbox_server_host}:{self.cfg.host_sandbox_server_port}/health"
        jupyter_url = (
            f"http://{self.cfg.host_sandbox_jupyter_kernel_host}:{self.cfg.host_sandbox_jupyter_kernel_port}/api"
        )
        deadline = time.time() + timeout

        def healthy(name: str, url: str) -> bool:
            try:
                r = requests.get(url, timeout=2)
                if r.status_code == 200:
                    self.logger.log(f"✅ {name} healthy at {url}", level=LogLevel.INFO)
                    return True
                self.logger.log(f"⏳ {name} → {r.status_code}: {r.text}", level=LogLevel.DEBUG)
            except requests.RequestException as e:
                self.logger.log(f"⏳ {name} not ready: {e}", level=LogLevel.DEBUG)
            return False

        while time.time() < deadline:
            if healthy("FastAPI", fastapi_url) and healthy("Jupyter KG", jupyter_url):
                return
            time.sleep(interval)
        raise VMOperationError(f"❌ Services not reachable within {timeout:.0f}s")

    def _prepare_shared_mount(self):
        mount = f"/mnt/{self.cfg.container_name}"
        self.ssh.exec_command(f"mkdir -p {mount}", as_root=True)
        # truncate (create/clear) log files under sudo
        for log_name in (self.cfg.sandbox_services_log, self.cfg.sandbox_jupyter_kernel_log):
            self.ssh.exec_command(
                f"truncate -s 0 {mount}/{log_name} || sudo tee {mount}/{log_name} <<< '' > /dev/null",
                as_root=True,
            )
        self._ensure_mounted(mount, self.cfg.guest_shared_dir.name)

    def _transfer_server_code(self):
        self.ssh.put_directory(self.cfg.host_services_dir, str(self.cfg.sandbox_services_dir))
        # Run the services script
        self.ssh.exec_command(f"chmod +x {self.cfg.sandbox_services_dir}/start.sh")

    def _handle_server_start_failure(self, err: VMOperationError):
        self.logger.log_error(f"❌ FastAPI server health check failed: {err}")
        try:
            logs = self.tail_server_logs()
            self.logger.log_error(f"🪵 Last sandbox-server logs:\n{logs}")
        except VMOperationError as log_err:
            self.logger.log_error(f"⚠️ Could not read log file: {log_err}")
        raise err

    # ------------------------------------------------------------------
    # Public VM bootstrap ----------------------------------------------
    # ------------------------------------------------------------------
    def start_agent_vm(self):
        """High-level bootstrap for the sandbox services."""
        # Superclass start() ensures container + SSH session ready.
        self.start()

        # Shared directory mount & log files
        self._prepare_shared_mount()

        # Runtime environment sent to start.sh inside guest
        self.cfg.runtime_env.update(
            {
                "SHARED_DIR": f"/mnt/{self.cfg.container_name}",
                "PORT": str(self.cfg.sandbox_server_port),
                "SERVICES_LOG": str(self.cfg.sandbox_services_log),
                "JUPYTER_KERNEL_NAME": self.cfg.sandbox_jupyter_kernel_name,
                "JUPYTER_KERNEL_GATEWAY_APP_PORT": str(self.cfg.sandbox_jupyter_kernel_port),
                "JUPYTER_KERNEL_GATEWAY_APP_LOG": str(self.cfg.sandbox_jupyter_kernel_log),
            }
        )

        if self.cfg.host_services_dir and Path(self.cfg.host_services_dir).exists():
            self._transfer_server_code()

        # fire-and-forget launch of FastAPI + kernels
        self.ssh.exec_command(
            "./start.sh",
            cwd=str(self.cfg.sandbox_services_dir),
            env=self.cfg.runtime_env,
            block=False,
        )

        try:
            self._wait_for_services()
            self.sandbox_client = SandboxClient(
                host=self.cfg.host_sandbox_server_host,
                port=self.cfg.host_sandbox_server_port,
            )
        except VMOperationError as e:
            self._handle_server_start_failure(e)

    # ------------------------------------------------------------------
    # Reconnect to an already running container ------------------------
    # ------------------------------------------------------------------
    def reconnect(self):
        """
        Reattach to an already running container and restart sandbox services.
        """
        self.logger.log_rule("🔁 Reconnect to Sandbox VM")

        # Ensure container + sshd + session.
        self.start()  # <- already handles wait_for_ssh_ready + connect

        # Restart inside-guest services
        self.logger.log("🔁 Restarting services via start.sh...", level=LogLevel.INFO)
        self.ssh.exec_command(
            "./start.sh",
            cwd=str(self.cfg.sandbox_services_dir),
            env=self.cfg.runtime_env,
            block=False,
        )

        self._wait_for_services()
        self.sandbox_client = SandboxClient(
            host=self.cfg.host_sandbox_server_host,
            port=self.cfg.sandbox_server_port,
        )
        self.logger.log("✅ Reconnected & services healthy", level=LogLevel.INFO)

    # ------------------------------------------------------------------
    # Utility -----------------------------------------------------------
    # ------------------------------------------------------------------
    def tail_server_logs(self, lines: int = 100) -> str:
        path = Path(self.cfg.host_container_shared_dir) / self.cfg.sandbox_services_log
        if not path.exists():
            raise VMOperationError(f"Log file not found: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return "".join(f.readlines()[-lines:])
        except Exception as e:
            raise VMOperationError(f"Failed to read log file: {e}") from e

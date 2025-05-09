from __future__ import annotations

import contextlib
import time

import requests
from smolagents import AgentLogger, LogLevel

from .configs import SandboxVMConfig
from .errors import RemoteCommandError, VMOperationError
from .ssh import SSHClient, SSHConfig
from .virtualmachine import VMManager


class SandboxClient:
    def __init__(self, host: str, port: int):
        self.base_url = f"http://{host}:{port}"

    def health(self):
        return requests.get(f"{self.base_url}/health").json()

    def take_screenshot(self, method="pyautogui"):
        return requests.get(f"{self.base_url}/screenshot", params={"method": method}).json()

    def start_recording(self):
        return requests.get(f"{self.base_url}/record", params={"mode": "start"}).json()

    def stop_recording(self):
        return requests.get(f"{self.base_url}/record", params={"mode": "stop"}).json()


class SandboxVMManager(VMManager):
    def __init__(
        self,
        config: SandboxVMConfig,
        logger: AgentLogger,
        preserve_on_exit: bool = False,
        **kwargs,
    ):
        if not isinstance(config, SandboxVMConfig):
            raise TypeError("SandboxVMManager requires SandboxVMConfig")
        super().__init__(config=config, logger=logger, **kwargs)

        self._should_cleanup = not (self.container and self.container.status == "running")
        self._preserve_on_exit = preserve_on_exit

        self.logger.log(f"Initialization _should_cleanup set to: {self._should_cleanup}", level=LogLevel.DEBUG)
        if self._preserve_on_exit:
            self.logger.log("⚠️ Container files will be preserved on exit (preserve_on_exit=True)", level=LogLevel.INFO)

    def connect_or_start(self):
        if self.container and self.container.status == "running":
            self.logger.log("🔁 Detected running container. Reconnecting...", level=LogLevel.INFO)
            self.reconnect()
        else:
            self.logger.log_rule("🚀 Starting new sandbox VM")
            try:
                self.__enter__()
            except Exception as e:
                raise VMOperationError(f"Failed to start sandbox VM: {e}") from e

    @contextlib.contextmanager
    def sandbox_vm_context(self):
        try:
            self.__enter__()
            yield self
        finally:
            self.__exit__(None, None, None)

    def __enter__(self) -> SandboxVMManager:
        try:
            self.start_agent_vm()
            self._should_cleanup = False
            return self
        except Exception as e:
            self.logger.log_error(f"❌ - Exception during VM startup: {e}")
            self.cleanup(delete_storage=True)
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        delete_storage = not self._preserve_on_exit
        reason = "⚠️ Exiting VM context with error," if exc_type or self._should_cleanup else "🧹 Normal exit,"
        action = " preserving container files..." if not delete_storage else " cleaning up VM container..."
        self.logger.log(reason + action, level=LogLevel.INFO)
        self.cleanup(delete_storage=delete_storage)
        return False

    def _ensure_mounted(self, mount_point: str, tag: str):
        self.logger.log(f"🔍 Checking if {mount_point} is already mounted...", level=LogLevel.INFO)
        try:
            self.ssh.exec_command(f"mountpoint -q {mount_point}")
            self.logger.log(f"✅ {mount_point} is already mounted.", level=LogLevel.DEBUG)
            return
        except RemoteCommandError as e:
            if e.status == 32:  # Exit code for "not a mountpoint"
                self.logger.log(f"🔧 {mount_point} is not mounted. Proceeding to mount...", level=LogLevel.INFO)
            else:
                self.logger.log_error(f"❌ Unexpected mountpoint check failure (exit {e.status}): {e.stderr}")
                raise

        try:
            self.ssh.exec_command(f"mount -t 9p -o trans=virtio {tag} {mount_point}", as_root=True)
            self.logger.log(f"✅ Mounted {tag} to {mount_point}.", level=LogLevel.INFO)
        except RemoteCommandError as e:
            self.logger.log_error(f"❌ Failed to mount {tag} to {mount_point}: {e.stderr}")
            raise

    def _wait_for_services(self, timeout: float = 120.0, interval: float = 10):
        self.logger.log_rule("🔎 Services Check")

        fastapi_url = f"http://{self.cfg.host_sandbox_server_host}:{self.cfg.host_sandbox_server_port}/health"
        jupyter_url = (
            f"http://{self.cfg.host_sandbox_jupyter_kernel_host}:{self.cfg.host_sandbox_jupyter_kernel_port}/api"
        )

        deadline = time.time() + timeout

        def is_healthy(name, url):
            try:
                r = requests.get(url, timeout=2)
                if r.status_code == 200:
                    self.logger.log(f"✅ {name} is healthy at {url}", level=LogLevel.INFO)
                    return True
                self.logger.log(f"⏳ - {name} responded with {r.status_code}: {r.text}", level=LogLevel.DEBUG)
            except requests.RequestException as e:
                self.logger.log(f"⏳ - {name} not ready: {e}", level=LogLevel.DEBUG)
            return False

        while time.time() < deadline:
            if is_healthy("FastAPI server", fastapi_url) and is_healthy("Jupyter Kernel Gateway", jupyter_url):
                return
            time.sleep(interval)

        raise VMOperationError(f"❌ - Services did not become reachable within {timeout:.1f} seconds.")

    def _start_container_and_ssh(self):
        self.create_container()
        time.sleep(5)
        self._wait_for_ssh_ready()
        self.ssh = SSHClient(SSHConfig(port=self.cfg.host_ssh_port), logger=self.logger)

    def _prepare_shared_mount(self):
        mount = f"/mnt/{self.cfg.container_name}"
        self.ssh.exec_command(f"mkdir -p {mount}", as_root=True)

        # Use tee workaround so redirection happens under sudo
        self.ssh.exec_command(
            f"truncate -s 0 {mount}/{self.cfg.sandbox_services_log} || sudo tee {mount}/{self.cfg.sandbox_services_log} <<< '' > /dev/null",
            as_root=True,
        )
        self.ssh.exec_command(
            f"truncate -s 0 {mount}/{self.cfg.sandbox_jupyter_kernel_log} || sudo tee {mount}/{self.cfg.sandbox_jupyter_kernel_log} <<< '' > /dev/null",
            as_root=True,
        )

        self._ensure_mounted(mount, self.cfg.guest_shared_dir.name)

    def _transfer_server_code(self):
        self.ssh.transfer_directory(self.cfg.host_server_dir, str(self.cfg.sandbox_server_dir))
        self.ssh.exec_command(f"chmod +x {self.cfg.sandbox_server_dir}/start.sh")

    def _handle_server_start_failure(self, e: VMOperationError):
        self.logger.log_error(f"❌ FastAPI server failed health check: {e}")
        try:
            logs = self.tail_server_logs()
            self.logger.log_error(f"🪵 Last sandbox-server logs:\n{logs}")
        except VMOperationError as log_err:
            self.logger.log_error(f"⚠️ Could not read log file: {log_err}")
        raise

    def tail_server_logs(self, lines: int = 100) -> str:
        path = self.cfg.host_container_shared_dir / self.cfg.sandbox_services_log
        if not path.exists():
            raise VMOperationError(f"❌ - Log file not found at {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return "".join(f.readlines()[-lines:])
        except Exception as e:
            raise VMOperationError(f"❌ - Failed to read log file: {e}") from e

    def start_agent_vm(self):
        if not self.container or self.container.status != "running":
            self._start_container_and_ssh()
        self._prepare_shared_mount()

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

        if self.cfg.host_server_dir:
            self._transfer_server_code()

        self.ssh.exec_command("./start.sh", cwd=str(self.cfg.sandbox_server_dir), env=self.cfg.runtime_env, block=False)

        try:
            self._wait_for_services()
            self.sandbox_client = SandboxClient(
                host=self.cfg.host_sandbox_server_host,
                port=self.cfg.host_sandbox_server_port,
            )
        except VMOperationError as e:
            self._handle_server_start_failure(e)

    def reconnect(self):
        self.logger.log_rule("🔁 Reconnect to Sandbox VM")
        if not self.container or self.container.status != "running":
            raise VMOperationError("No running container to reconnect to.")

        self._wait_for_ssh_ready()
        self.ssh = SSHClient(SSHConfig(port=self.cfg.host_ssh_port), logger=self.logger)

        self.logger.log("🔁 Re-running start.sh to cleanly restart all services...", level=LogLevel.INFO)
        self.ssh.exec_command("./start.sh", cwd=str(self.cfg.sandbox_server_dir), env=self.cfg.runtime_env, block=False)

        self._wait_for_services()
        self.sandbox_client = SandboxClient(
            host=self.cfg.host_sandbox_server_host,
            port=self.cfg.sandbox_server_port,
        )
        self.logger.log("✅ Reconnected and reinitialized sandbox services.", level=LogLevel.INFO)

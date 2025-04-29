from __future__ import annotations

import contextlib
import importlib
import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from .configs import SandboxVMConfig
from .errors import RemoteCommandError, VMOperationError
from .ssh import SSHClient, SSHConfig
from .virtualmachine import VMManager

# ────────────────────────────── Logging Setup ──────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ────────────────────────────── SandboxClient ──────────────────────────────
class SandboxClient:
    def __init__(self, manager: SandboxVMManager):
        self.manager = manager

    def health(self) -> dict:
        response = self.manager.api_health_check.sync_detailed(client=self.manager.client)
        return response.parsed or json.loads(response.content)

    def take_screenshot(self, method: str = "pyautogui") -> dict:
        method_enum = self.manager.models.ScreenshotEndpointScreenshotGetMethod(method)
        response = self.manager.api_screenshot.sync_detailed(client=self.manager.client, method=method_enum)
        result = response.parsed or json.loads(response.content)
        result["screenshot_path"] = self.manager.cfg.container_shared_dir / result["screenshot_path"]
        return result

    def start_recording(self) -> dict:
        response = self.manager.api_record.sync_detailed(
            client=self.manager.client,
            mode=self.manager.models.RecordRecordGetMode.START,
        )
        return response.parsed or json.loads(response.content)

    def stop_recording(self) -> dict:
        response = self.manager.api_record.sync_detailed(
            client=self.manager.client,
            mode=self.manager.models.RecordRecordGetMode.STOP,
        )
        return response.parsed or json.loads(response.content)


# ────────────────────────────── Manager ──────────────────────────────
class SandboxVMManager(VMManager):
    def __init__(
        self,
        config: SandboxVMConfig,
        preserve_on_exit: bool = False,
        logger: logging.Logger = logger,
        **kwargs,
    ):
        if not isinstance(config, SandboxVMConfig):
            raise TypeError("SandboxVMManager requires SandboxVMConfig")
        super().__init__(config=config, logger=logger, **kwargs)

        self._should_cleanup = not (self.container and self.container.status == "running")
        self._preserve_on_exit = preserve_on_exit
        self.log.debug("Initialization _should_cleanup set to: %s", self._should_cleanup)
        if self._preserve_on_exit:
            self.log.info("⚠️ Container files will be preserved on exit (preserve_on_exit=True)")

    @contextlib.contextmanager
    def sandbox_vm_context(self):
        """Context manager wrapper for SandboxVMManager, ensuring proper startup and cleanup."""
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
            self.log.error("❌ - Exception during VM startup: %s", e, exc_info=True)
            self.cleanup(delete_storage=True)
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        delete_storage = not self._preserve_on_exit
        if self._should_cleanup or exc_type is not None:
            if delete_storage:
                self.log.warning("⚠️ Exiting VM context with error, cleaning up completely...")
            else:
                self.log.warning("⚠️ Exiting VM context with error, preserving container files...")
        else:
            if delete_storage:
                self.log.info("🧹 Normal exit, cleaning up VM container...")
            else:
                self.log.info("🧹 Normal exit, preserving container files as requested...")
        self.cleanup(delete_storage=delete_storage)
        return False

    def _ensure_mounted(self, mount_point: str, tag: str):
        self.log.info("Mounting %s -> %s", tag, mount_point)
        try:
            self.ssh.exec_command(f"mount -t 9p -o trans=virtio {tag} {mount_point}", as_root=True)
        except RemoteCommandError as e:
            if "already mounted" in e.stderr:
                self.log.warning("⚠️ - Shared directory already mounted, continuing...")
            else:
                raise

    def _wait_for_services(self, timeout: float = 120.0, interval: float = 3.0):
        """
        Wait for the Jupyter Gateway Kernel and FastAPI server to be reachable and healthy.
        """

        url = f"http://{self.cfg.host_sandbox_server_host}:{self.cfg.host_sandbox_server_port}/health"
        self.log.info("🔍 - Waiting for FastAPI server health check at %s...", url)
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    self.log.info(response.text.strip())
                    self.log.info("✅ FastAPI server is healthy at %s", url)
                    return
                else:
                    self.log.debug("⏳ - Server responded with status %s", response.status_code)
            except requests.RequestException as e:
                self.log.debug("⏳ - Server not ready (RequestException): %s", e)
            except Exception as e:
                self.log.debug("⏳ - Unexpected error while checking server: %s", e)

            time.sleep(interval)

        raise VMOperationError(f"❌ - FastAPI server did not become reachable at {url} within {timeout:.1f} seconds.")

    def _start_container_and_ssh(self):
        self.create_container()
        time.sleep(5)
        self._wait_for_ssh_ready()
        self.ssh = SSHClient(SSHConfig(port=self.cfg.host_ssh_port), logger=self.log)

    def _prepare_shared_mount(self):
        mount = f"/mnt/{self.cfg.container_name}"
        self.ssh.exec_command(f"mkdir -p {mount}", as_root=True)
        # Create the FastAPI server log file
        self.ssh.exec_command(f"touch {mount}/{self.cfg.sandbox_server_log}", as_root=True)
        # Create the Jupyter kernel log file
        self.ssh.exec_command(f"touch {mount}/{self.cfg.sandbox_jupyter_kernel_log}", as_root=True)
        self._ensure_mounted(mount, self.cfg.guest_shared_dir.name)

    def _transfer_server_code(self):
        self.ssh.transfer_directory(self.cfg.host_server_dir, str(self.cfg.sandbox_server_dir))
        self.ssh.exec_command(f"chmod +x {self.cfg.sandbox_server_dir}/start.sh")

    def _handle_server_start_failure(self, e: VMOperationError):
        self.log.error("❌ FastAPI server failed health check: %s", e)
        try:
            logs = self.tail_server_logs()
            self.log.error("🪵 Last sandbox-server logs:\n%s", logs)
        except VMOperationError as log_err:
            self.log.error("⚠️ Could not read log file: %s", log_err)
        raise

    def _generate_client_from_openapi(self):
        server_url = f"http://{self.cfg.host_sandbox_server_host}:{self.cfg.host_sandbox_server_port}"
        openapi_schema_url = f"{server_url}/openapi.json"

        self.log.info("📦 Generating FastAPI client from OpenAPI schema at %s...", openapi_schema_url)

        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "openapi.json"
            try:
                response = requests.get(openapi_schema_url, timeout=5)
                response.raise_for_status()
                schema_path.write_text(response.text)

                result = subprocess.run(
                    [
                        "openapi-python-client",
                        "generate",
                        "--path",
                        str(schema_path),
                        "--output-path",
                        str(self.cfg.client_output_dir),
                        "--overwrite",
                    ],
                    capture_output=True,
                    text=True,
                )

                if result.returncode != 0:
                    self.log.error("❌ FastAPI client generation failed:\n%s", result.stderr)
                    raise VMOperationError("FastAPI client generation failed")

                self.log.info("✅ FastAPI client generated at %s", self.cfg.client_output_dir.resolve())
            except Exception as e:
                self.log.error("❌ Failed to fetch or generate OpenAPI client: %s", e)
                raise

    def _import_generated_client(self):
        parent_dir = str(self.cfg.client_output_dir)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        client_module_name = "sandbox_rest_server_client"

        try:
            self.client_package = importlib.import_module(client_module_name)
            self.client = self.client_package.client.Client(
                base_url=f"http://{self.cfg.host_sandbox_server_host}:{self.cfg.host_sandbox_server_port}"
            )

            # Import models and API endpoints
            self.models = importlib.import_module(f"{client_module_name}.models")
            self.api_health_check = importlib.import_module(f"{client_module_name}.api.default.health_check_health_get")
            self.api_screenshot = importlib.import_module(
                f"{client_module_name}.api.default.screenshot_endpoint_screenshot_get"
            )
            self.api_record = importlib.import_module(f"{client_module_name}.api.default.record_record_get")

            self.sandbox_client = SandboxClient(manager=self)

            self.log.info("✅ FastAPI client loaded and ready: %s", self.client)
        except ImportError as e:
            self.log.error("❌ Could not import generated client: %s", e)
            raise

    def _generate_fastapi_client(self):
        if self.cfg.force_regenerate_client or not self.cfg.client_output_dir.exists():
            self._generate_client_from_openapi()
        else:
            self.log.info("⚠️ FastAPI client already exists at %s, skipping regeneration", self.cfg.client_output_dir)

        self._import_generated_client()

    def tail_server_logs(self, lines: int = 100) -> str:
        path = self.cfg.container_shared_dir / self.cfg.sandbox_server_log
        if not path.exists():
            raise VMOperationError("❌ - Log file not found at %s", path)

        try:
            with open(path, "r", encoding="utf-8") as f:
                return "".join(f.readlines()[-lines:])
        except Exception as e:
            raise VMOperationError("❌ - Failed to read log file: %s", e) from e

    def start_agent_vm(self):
        if not self.container or self.container.status != "running":
            self._start_container_and_ssh()

        self._prepare_shared_mount()

        self.cfg.runtime_env["SHARED_DIR"] = f"/mnt/{self.cfg.container_name}"

        # FastAPI server environment variables
        self.cfg.runtime_env["PORT"] = str(self.cfg.sandbox_server_port)
        self.cfg.runtime_env["SERVER_LOG"] = str(self.cfg.sandbox_server_log)

        # Jupyter kernel environment variables
        self.cfg.runtime_env["JUPYTER_KERNEL_NAME"] = self.cfg.sandbox_jupyter_kernel_name
        self.cfg.runtime_env["JUPYTER_KERNEL_GATEWAY_APP_PORT"] = str(self.cfg.sandbox_jupyter_kernel_port)
        self.cfg.runtime_env["JUPYTER_KERNEL_GATEWAY_APP_LOG"] = str(self.cfg.sandbox_jupyter_kernel_log)

        if self.cfg.host_server_dir:
            self._transfer_server_code()

        self.ssh.exec_command("./start.sh", cwd=str(self.cfg.sandbox_server_dir), env=self.cfg.runtime_env, block=False)

        try:
            self._wait_for_services()
            self._generate_fastapi_client()
        except VMOperationError as e:
            self._handle_server_start_failure(e)

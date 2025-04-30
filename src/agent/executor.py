import base64
import json
import pickle
import sys
import time
from pathlib import Path
from textwrap import dedent
from typing import Any, List

import requests
from smolagents.agents import AgentError
from smolagents.monitoring import LogLevel
from smolagents.remote_executors import RemotePythonExecutor
from websocket import create_connection

# Allow imports from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox.configs import SandboxVMConfig
from sandbox.sandbox import SandboxVMManager


class SandboxExecutor(RemotePythonExecutor):
    """
    Executes Python code inside a QEMU-based virtual machine using a Jupyter Kernel Gateway.

    This executor boots a VM container via QEMU, starts a Jupyter Kernel Gateway inside it,
    connects to the kernel via WebSocket, and supports executing code, installing packages
    using `uv`, and returning final results via pickle encoding.

    Args:
        config (SandboxVMConfig): Configuration object specifying VM and host/kernel details.
        additional_imports (List[str]): List of Python packages to install via `uv`.
        logger (logging.Logger): Logger instance to use for status and error logging.
        preserve_on_exit (bool): If True, keeps the VM running after executor exits. Defaults to False.
        **kwargs: Additional arguments passed to the base RemotePythonExecutor or SandboxVMManager.
    """

    def __init__(
        self,
        additional_imports: List[str],
        logger,
        config: SandboxVMConfig,
        preserve_on_exit: bool = False,
        **kwargs,
    ):
        super().__init__(additional_imports, logger)

        self.vm = SandboxVMManager(config=config, preserve_on_exit=preserve_on_exit, **kwargs)
        self.vm.__enter__()  # start the VM

        self.host = config.host_sandbox_jupyter_kernel_host
        self.port = config.host_sandbox_jupyter_kernel_port
        self.base_url = f"http://{self.host}:{self.port}"
        self.ws_url = f"ws://{self.host}:{self.port}"
        self.kernel_id = None
        self.ws = None

        self._create_kernel()
        self.installed_packages = self.install_packages(additional_imports)

        self.logger.log("AgentExecutor is running with Jupyter kernel.", level=LogLevel.INFO)

    def install_packages(self, additional_imports: list[str]):
        """
        Rewrite of the original install_packages method to use the new uv package manager and also always include pyautogui and smolagents.
        """
        additional_imports = set(additional_imports + ["smolagents", "pyautogui"])
        _, execution_logs = self.run_code_raise_errors(f"!pip install {' '.join(additional_imports)}")
        self.logger.log(execution_logs)
        return additional_imports

    def _create_kernel(self, retries: int = 5, delay: float = 2.0):
        """
        Creates a new Jupyter kernel via the Kernel Gateway HTTP API and establishes a WebSocket connection.

        This method:
        - Sends a POST request to the /api/kernels endpoint to create a new kernel.
        - Verifies that the response status code is 201 (Created).
        - Logs detailed error diagnostics if the creation fails.
        - Establishes a WebSocket connection to the kernel's /channels endpoint.
        - Retries up to `retries` times on failure, with `delay` seconds between attempts.

        Raises:
            RuntimeError: If the kernel creation fails with a non-201 status code.
            Exception: If connection or API failures persist after all retries.
        """
        for attempt in range(retries):
            try:
                resp = requests.post(f"{self.base_url}/api/kernels", timeout=5)

                if resp.status_code != 201:
                    error_details = {
                        "status_code": resp.status_code,
                        "headers": dict(resp.headers),
                        "url": resp.url,
                        "body": resp.text,
                        "request_method": resp.request.method,
                        "request_headers": dict(resp.request.headers),
                        "request_body": resp.request.body,
                    }
                    self.logger.log_error(f"Failed to create kernel. Details: {json.dumps(error_details, indent=2)}")
                    raise RuntimeError(f"Failed to create kernel: Status {resp.status_code}\nResponse: {resp.text}")

                self.kernel_id = resp.json()["id"]
                self.ws = create_connection(f"{self.ws_url}/api/kernels/{self.kernel_id}/channels")
                return

            except Exception as e:
                if attempt < retries - 1:
                    self.logger.log(f"Kernel creation attempt {attempt + 1} failed, retrying...", level=LogLevel.INFO)
                    time.sleep(delay)
                else:
                    self.logger.log(f"Kernel creation failed after {retries} attempts: {e}", level=LogLevel.ERROR)
                    raise

    def _send_execute_request(self, code: str) -> str:
        """Send code execution request to kernel."""
        import uuid

        # Generate a unique message ID
        msg_id = str(uuid.uuid4())

        # Create execute request
        execute_request = {
            "header": {
                "msg_id": msg_id,
                "username": "anonymous",
                "session": str(uuid.uuid4()),
                "msg_type": "execute_request",
                "version": "5.0",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": False,
            },
        }

        self.ws.send(json.dumps(execute_request))
        return msg_id

    def run_code_raise_errors(self, code_action: str, return_final_answer: bool = False) -> tuple[Any, str]:
        """
        Execute code and return result based on whether it's a final answer.
        """
        try:
            if return_final_answer:
                match = self.final_answer_pattern.search(code_action)
                if match:
                    pre_final_answer_code = self.final_answer_pattern.sub("", code_action)
                    result_expr = match.group(1)
                    wrapped_code = pre_final_answer_code + dedent(f"""
                        import pickle, base64
                        _result = {result_expr}
                        print("RESULT_PICKLE:" + base64.b64encode(pickle.dumps(_result)).decode())
                        """)
            else:
                wrapped_code = code_action

            # Send execute request
            msg_id = self._send_execute_request(wrapped_code)

            # Collect output and results
            outputs = []
            result = None
            waiting_for_idle = False

            while True:
                msg = json.loads(self.ws.recv())
                msg_type = msg.get("msg_type", "")
                parent_msg_id = msg.get("parent_header", {}).get("msg_id")

                # Only process messages related to our execute request
                if parent_msg_id != msg_id:
                    continue

                if msg_type == "stream":
                    text = msg["content"]["text"]
                    if return_final_answer and text.startswith("RESULT_PICKLE:"):
                        pickle_data = text[len("RESULT_PICKLE:") :].strip()
                        result = pickle.loads(base64.b64decode(pickle_data))
                        waiting_for_idle = True
                    else:
                        outputs.append(text)
                elif msg_type == "error":
                    traceback = msg["content"].get("traceback", [])
                    raise AgentError("\n".join(traceback), self.logger)
                elif msg_type == "status" and msg["content"]["execution_state"] == "idle":
                    if not return_final_answer or waiting_for_idle:
                        break

            return result, "".join(outputs)

        except Exception as e:
            self.logger.log_error(f"Code execution failed: {e}")
            raise

    def cleanup(self):
        try:
            if self.kernel_id:
                requests.delete(f"{self.base_url}/api/kernels/{self.kernel_id}", timeout=5)
            if self.ws:
                self.ws.close()
            self.vm.__exit__(None, None, None)
            self.logger.log("🧹 Cleaned up kernel and VM", level=LogLevel.INFO)
        except Exception as e:
            self.logger.log_error(f"Cleanup failed: {e}")

    def delete(self):
        self.cleanup()

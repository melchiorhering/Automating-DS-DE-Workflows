import base64
import json
import logging
import pickle
import time
from textwrap import dedent
from typing import Any, List

import requests
from smolagents.agents import AgentError
from smolagents.monitoring import LogLevel
from smolagents.remote_executors import RemotePythonExecutor
from websocket import create_connection

from .configs import SandboxVMConfig
from .sandbox import SandboxVMManager


class SandboxExecutor(RemotePythonExecutor):
    """
    Executes Python code inside a QEMU-based VM using Jupyter Kernel Gateway.
    Starts and manages the VM container, kernel gateway, and websocket connection.
    """

    def __init__(
        self,
        config: SandboxVMConfig,
        additional_imports: List[str],
        logger: logging.Logger,
        preserve_on_exit: bool = False,
    ):
        super().__init__(additional_imports, logger)

        self.vm = SandboxVMManager(config=config, preserve_on_exit=preserve_on_exit)
        self.vm.__enter__()  # start the VM

        self.host = config.host_sandbox_jupyter_kernel_host
        self.port = config.host_sandbox_jupyter_kernel_port
        self.base_url = f"http://{self.host}:{self.port}"
        self.ws_url = f"ws://{self.host}:{self.port}"
        self.kernel_id = None
        self.ws = None

        self._start_kernel()
        self.installed_packages = self.install_packages(additional_imports)

        self.logger.log("AgentExecutor is running with Jupyter kernel.", level=LogLevel.INFO)

    def install_packages(self, additional_imports: list[str]):
        # REWRITE OF INSTALL PACKAGES USING UV
        additional_imports = additional_imports + ["smolagents"]
        _, execution_logs = self.run_code_raise_errors(f"!uv pip install {' '.join(additional_imports)}")
        self.logger.log(execution_logs)
        return additional_imports

    def _start_kernel(self, retries: int = 5, delay: float = 2.0):
        for attempt in range(retries):
            try:
                resp = requests.post(f"{self.base_url}/api/kernels", timeout=5)
                resp.raise_for_status()
                self.kernel_id = resp.json()["id"]
                self.ws = create_connection(f"{self.ws_url}/api/kernels/{self.kernel_id}/channels")
                return
            except (requests.RequestException, Exception) as e:
                if attempt < retries - 1:
                    self.logger.log(f"Kernel start attempt {attempt + 1} failed, retrying...", level=LogLevel.INFO)
                    time.sleep(delay)
                else:
                    self.logger.log(f"Kernel start failed after {retries} attempts: {e}", level=LogLevel.ERROR)
                    raise

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

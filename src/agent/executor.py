import base64
import json
import pickle
import re
import sys
import time
import uuid
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Tuple

import requests
from smolagents.agents import AgentError, AgentLogger
from smolagents.monitoring import LogLevel
from smolagents.remote_executors import RemotePythonExecutor
from smolagents.tools import Tool, get_tools_definition_code
from websocket import create_connection

# Allow imports from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox.configs import SandboxVMConfig
from sandbox.sandbox import SandboxVMManager


class SandboxExecutor(RemotePythonExecutor):
    def __init__(
        self,
        additional_imports: List[str],
        logger: AgentLogger,
        config: SandboxVMConfig,
        preserve_on_exit: bool = False,
        reconnect: bool = False,
        **kwargs,
    ):
        super().__init__(additional_imports, logger)
        self.logger.log_rule("🎢 Sandbox Executor Initialization")
        self.kernel_id = None
        self.ws = None
        self._exited = False

        try:
            self.logger.log("✨ Initializing SandboxExecutor...", level=LogLevel.INFO)
            self.vm = SandboxVMManager(config=config, logger=self.logger, preserve_on_exit=preserve_on_exit, **kwargs)

            self.logger.log("🔌 Connecting to Sandbox VM...", level=LogLevel.DEBUG)
            if reconnect and self.vm.container and self.vm.container.status == "running":
                self.vm.reconnect()
            else:
                self.logger.log_rule("🚀 Start Sandbox VM")
                self.vm.__enter__()

            self._wait_for_services()

            self.host = config.host_sandbox_jupyter_kernel_host
            self.port = config.host_sandbox_jupyter_kernel_port
            self.base_url = f"http://{self.host}:{self.port}"
            self.ws_url = f"ws://{self.host}:{self.port}"

            self._initialize_kernel_connection()

            self.installed_packages = self.install_packages(additional_imports)
            self.logger.log_rule("✅ Sandbox Ready")

        except Exception as e:
            self.logger.log_error(f"SandboxExecutor init failed: {e}")
            self.cleanup()
            raise

    def _wait_for_services(self, retries: int = 10, delay: float = 5.0):
        """Final confirmation that the FastAPI and Kernel Gateway servers are reachable."""
        fastapi_url = f"http://{self.vm.cfg.host_sandbox_server_host}:{self.vm.cfg.host_sandbox_server_port}/health"
        jupyter_url = f"http://{self.vm.cfg.host_sandbox_jupyter_kernel_host}:{self.vm.cfg.host_sandbox_jupyter_kernel_port}/api/kernels"

        for attempt in range(1, retries + 1):
            fastapi_ok = jupyter_ok = False

            try:
                r1 = requests.get(fastapi_url, timeout=2)
                self.logger.log(f"🌐 FastAPI health check: {r1}", level=LogLevel.DEBUG)
                fastapi_ok = r1.status_code == 200
            except Exception as e:
                self.logger.log(f"⏳ FastAPI not ready (attempt {attempt}): {e}", level=LogLevel.DEBUG)

            try:
                r2 = requests.get(jupyter_url, timeout=2)
                self.logger.log(f"🌐 Jupyter Kernel Gateway health check: {r2}", level=LogLevel.DEBUG)
                jupyter_ok = r2.status_code == 200
            except Exception as e:
                self.logger.log(f"⏳ Jupyter Kernel Gateway not ready (attempt {attempt}): {e}", level=LogLevel.DEBUG)

            if fastapi_ok and jupyter_ok:
                self.logger.log("✅ Both FastAPI and Jupyter Kernel Gateway are reachable.", level=LogLevel.INFO)
                return

            time.sleep(delay)

        # Only raise if we never returned
        raise RuntimeError("❌ Required services are not reachable after retries.")

    def _initialize_kernel_connection(self, retries: int = 5, delay: float = 2.0):
        """Creates a new Jupyter kernel and connects via WebSocket. Logs API docs if it fails."""
        self.logger.log_rule("🧠 Kernel Initialization")
        self.logger.log("🔗 Connect or create Jupyter Kernel", level=LogLevel.DEBUG)
        for attempt in range(retries):
            try:
                self.logger.log(f"🆕 Creating new kernel (attempt {attempt + 1}/{retries})...", level=LogLevel.DEBUG)
                r = requests.post(f"{self.base_url}/api/kernels", timeout=5)

                if r.status_code == 201:
                    self.kernel_id = r.json()["id"]
                    self.logger.log(f"✅ Created new kernel: {self.kernel_id}", level=LogLevel.INFO)
                    break
                else:
                    self.logger.log_error(f"❌ Kernel creation failed: {r.status_code} — {r.text}")

            except Exception as e:
                self.logger.log_error(f"⚠️ Kernel creation attempt {attempt + 1} failed: {e}")
                if attempt == retries - 1:
                    self._log_jupyter_api_markdown()
                    raise
                time.sleep(delay)

        try:
            ws_url = f"{self.ws_url}/api/kernels/{self.kernel_id}/channels"
            self.logger.log(f"🌐 Connecting WebSocket to: {ws_url}", level=LogLevel.DEBUG)
            self.ws = create_connection(ws_url)
            self.logger.log("📡 WebSocket connected to kernel.", level=LogLevel.INFO)
        except Exception as e:
            self.logger.log_error(f"❌ Failed to connect WebSocket: {e}")
            raise

    def _send_execute_request(self, code: str) -> str:
        msg_id = str(uuid.uuid4())
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
        self.logger.log(f"📤 Sending execute request with ID {msg_id}...", level=LogLevel.DEBUG)
        self.ws.send(json.dumps(execute_request))
        return msg_id

    @staticmethod
    def strip_ansi(text: str) -> str:
        ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]|\x1B[@-_]|\x1b\[.*?m")
        return ansi_escape.sub("", text)

    def send_tools(self, tools: Dict[str, Tool]):
        tool_definition_code = get_tools_definition_code(tools)

        packages_to_install = set()
        for tool in tools.values():
            for package in tool.to_dict()["requirements"]:
                if package not in self.installed_packages:
                    packages_to_install.add(package)
                    self.installed_packages.append(package)

        self.logger.log(f"🔨 Tool Definition Code:\n{tool_definition_code}", level=LogLevel.DEBUG)
        self.logger.log(f"📦 Detected packages to install: {', '.join(packages_to_install)}", level=LogLevel.DEBUG)

        if packages_to_install:
            code = f"!uv pip install {' '.join(packages_to_install)}\n{tool_definition_code}"
        else:
            self.logger.log("ℹ️ No new packages to install.", level=LogLevel.DEBUG)
            code = tool_definition_code

        result, logs = self.run_code_raise_errors(code)
        self.logger.log(f"📄 Execution logs:\n{logs}", level=LogLevel.DEBUG)

        self.logger.log("result", result, level=LogLevel.DEBUG)

    def install_packages(self, additional_imports: list[str]):
        packages = additional_imports + ["smolagents", "pyautogui"]
        self.logger.log(f"📆 Installing packages: {', '.join(packages)}", level=LogLevel.DEBUG)
        _, logs = self.run_code_raise_errors(f"!uv pip install {' '.join(packages)}")
        return packages

    def run_code_raise_errors(self, code_action: str, return_final_answer: bool = False) -> Tuple[Any, str]:
        try:
            if return_final_answer:
                match = self.final_answer_pattern.search(code_action)
                if match:
                    code_action = self.final_answer_pattern.sub("", code_action)
                    result_expr = match.group(1)
                    code_action += dedent(f"""
                        import pickle, base64
                        _result = {result_expr}
                        print("RESULT_PICKLE:" + base64.b64encode(pickle.dumps(_result)).decode())
                    """)

            msg_id = self._send_execute_request(code_action)
            outputs, result, waiting_for_idle = [], None, False

            while True:
                msg = json.loads(self.ws.recv())
                self.logger.log(f"📥 Received message: {json.dumps(msg, indent=3)}", level=LogLevel.DEBUG)

                msg_type = msg.get("msg_type", "")
                parent_msg_id = msg.get("parent_header", {}).get("msg_id")

                if parent_msg_id != msg_id:
                    continue

                if msg_type == "stream":
                    text = msg["content"]["text"]
                    self.logger.log(f"📜 Stream output: {self.strip_ansi(text)}", level=LogLevel.INFO)
                    if return_final_answer and text.startswith("RESULT_PICKLE:"):
                        pickle_data = text[len("RESULT_PICKLE:") :].strip()
                        result = pickle.loads(base64.b64decode(pickle_data))
                        waiting_for_idle = True
                    else:
                        outputs.append(text)

                elif msg_type == "error":
                    raw_traceback = msg["content"].get("traceback", [])
                    cleaned = [self.strip_ansi(line) for line in raw_traceback]
                    raise AgentError("\n".join(cleaned), self.logger)

                elif msg_type == "status" and msg["content"]["execution_state"] == "idle":
                    if not return_final_answer or waiting_for_idle:
                        break

            output_log = "".join(outputs)
            self.logger.log(f"📄 Execution completed. Logs:\n{self.strip_ansi(output_log)}", level=LogLevel.INFO)
            return result, output_log

        except Exception as e:
            self.logger.log_error(f"💥 Code execution failed: {e}")
            raise

    def cleanup(self):
        self.vm.container.stop()

        if getattr(self, "_exited", False):
            return
        try:
            self.logger.log("🪩 Cleaning up sandbox resources...", level=LogLevel.INFO)
            if self.kernel_id:
                requests.delete(f"{self.base_url}/api/kernels/{self.kernel_id}", timeout=5)
            if self.ws:
                self.ws.close()
            if hasattr(self, "vm"):
                self.vm.__exit__(None, None, None)
            self.logger.log("✅ Cleanup complete.", level=LogLevel.INFO)
        except Exception as e:
            self.logger.log_error(f"Cleanup failed: {e}")
        self._exited = True

    def delete(self):
        self.cleanup()

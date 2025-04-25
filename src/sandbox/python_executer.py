import base64
import logging
import pickle
import re
from typing import Any, List, Tuple

from smolagents.agents import AgentError
from smolagents.monitoring import LogLevel
from smolagents.remote_executors import RemotePythonExecutor

from sandbox.agent import AgentVMManager


class SandboxPythonExecutor(RemotePythonExecutor):
    """
    Executes Python code using the AgentVMManager sandbox server client.
    """

    def __init__(self, agent_vm: AgentVMManager, additional_imports: List[str], logger: logging.Logger):
        """
        Initialize the Sandbox Python Executor.

        Args:
            agent_vm: The running AgentVMManager instance.
            additional_imports: Additional imports to install inside the sandbox.
            logger: Logger to use.
        """
        super().__init__(additional_imports, logger)
        self.agent_vm = agent_vm

        self.logger.log("Checking sandbox server connection...", level=LogLevel.INFO)
        health = self.agent_vm.sandbox_health()
        if health.get("status") != "ok":
            raise ConnectionError("Cannot connect to sandbox server via AgentVMManager")
        self.logger.log("Sandbox server connection established", level=LogLevel.INFO)

        self.installed_packages = self.install_packages(additional_imports)
        self.logger.log("Sandbox Python Executor is running", level=LogLevel.INFO)

    def run_code_raise_errors(self, code: str, return_final_answer: bool = False) -> Tuple[Any, str]:
        """
        Execute code in the sandbox and return results.

        Args:
            code: Python code to execute
            return_final_answer: Whether to extract a final answer

        Returns:
            Tuple of (result, stdout)
        """
        if return_final_answer:
            code = self._wrap_code_for_final_answer(code)

        response = self.agent_vm.sandbox_execute_code(code)
        if "stderr" in response and response["stderr"]:
            raise AgentError(response["stderr"], self.logger)

        stdout = response.get("stdout", "")

        if return_final_answer:
            return self._extract_final_answer(stdout)

        return None, stdout

    def _wrap_code_for_final_answer(self, code: str) -> str:
        """
        Wrap code to extract the final answer if requested.
        """
        match = self.final_answer_pattern.search(code)
        if not match:
            return code

        result_expr = match.group(1)
        pre_final_answer_code = self.final_answer_pattern.sub("", code)
        return f"""{pre_final_answer_code}
import base64, pickle
_result = {result_expr}
print("FINAL_ANSWER_PICKLE:" + base64.b64encode(pickle.dumps(_result)).decode())
"""

    def _extract_final_answer(self, stdout: str) -> Tuple[Any, str]:
        """
        Extract and unpickle the final answer from the output.
        """
        pickle_pattern = re.compile(r"FINAL_ANSWER_PICKLE:(.+)$", re.MULTILINE)
        pickle_match = pickle_pattern.search(stdout)
        if pickle_match:
            try:
                pickle_data = pickle_match.group(1).strip()
                result = pickle.loads(base64.b64decode(pickle_data))
                stdout = pickle_pattern.sub("", stdout)
                return result, stdout
            except Exception as e:
                raise AgentError(f"Failed to unpickle final answer: {e}", self.logger)
        else:
            raise AgentError("Final answer pattern not found in stdout", self.logger)

    def send_variables(self, variables: dict):
        """
        Send variables to the sandbox environment.
        """
        pickled_vars = base64.b64encode(pickle.dumps(variables)).decode()
        code = f"""
import pickle, base64
vars_dict = pickle.loads(base64.b64decode('{pickled_vars}'))
locals().update(vars_dict)
"""
        self.run_code_raise_errors(code)

    def install_packages(self, additional_imports: List[str]) -> List[str]:
        """
        Install required packages in the sandbox environment.

        Args:
            additional_imports: List of package names to install

        Returns:
            Updated list of installed packages after installation
        """
        if not additional_imports:
            return self.get_installed_packages()

        response = self.agent_vm.sandbox_execute_code("", packages=additional_imports)
        if "stderr" in response and response["stderr"]:
            self.logger.log(f"⚠️ Warning during package installation: {response['stderr']}", level=LogLevel.WARNING)

        # Verify installations
        for package in additional_imports:
            try:
                package_name = package.split("[")[0].split("==")[0].split(">")[0].split("<")[0].strip()
                check_response = self.agent_vm.sandbox_execute_code(f"import {package_name}")
                if not check_response.get("stderr"):
                    self.logger.log(f"✅ Successfully installed and verified: {package}", level=LogLevel.INFO)
                else:
                    self.logger.log(
                        f"⚠️ Failed to import {package_name}: {check_response.get('stderr')}", level=LogLevel.WARNING
                    )
            except Exception as e:
                self.logger.log(f"⚠️ Warning: Package {package} verification failed: {e}", level=LogLevel.WARNING)

        return self.get_installed_packages()

    def get_installed_packages(self) -> List[str]:
        """
        Return a list of currently installed packages in the sandbox.
        """
        try:
            packages = self.agent_vm.sandbox_list_installed_packages()
            return [pkg.get("name") for pkg in packages if "name" in pkg]
        except Exception as e:
            self.logger.log(f"⚠️ Failed to fetch installed packages: {e}", level=LogLevel.WARNING)
            return []

    def cleanup(self):
        """
        Clean up any resources if needed (optional hook).
        """
        self.logger.log("SandboxPythonExecutor cleanup called (no action needed)", level=LogLevel.INFO)

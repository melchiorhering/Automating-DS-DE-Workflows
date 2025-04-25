"""
Set this SandboxPythonExcutor class after initting the smoleagents CodeAgent:
```
agent = CodeAgent(
    **kwargs
)

agent.python_executor = SandboxPythonExecutor(
    **kwargs
)

To overwrite

```
"""

import base64
import logging
import pickle
import re
from typing import Any, List, Tuple

from smolagents.agents import AgentError
from smolagents.remote_executors import RemotePythonExecutor

from .client import VMServerClient


class SandboxPythonExecutor(RemotePythonExecutor):
    """
    Executes Python code using the Sandbox VM Server.

    This executor connects to a running sandbox server that provides
    a secure environment for code execution.
    """

    def __init__(self, additional_imports: List[str], logger: logging.Logger, base_url: str = "http://localhost:8000"):
        """
        Initialize the Sandbox Python Executor.

        Args:
            additional_imports: Additional imports to install.
            logger: Logger to use.
            base_url: URL of the sandbox server.
        """
        super().__init__(additional_imports, logger)
        self.client = VMServerClient(base_url)

        self.logger.log("Checking sandbox server connection...")
        health = self.client.check_health()
        if health.get("status") != "ok":
            raise ConnectionError(f"Cannot connect to sandbox server at {base_url}")

        self.logger.log("Sandbox server connection established")
        self.installed_packages = self.install_packages(additional_imports)
        self.logger.log("Sandbox Python Executor is running")

    def run_code_raise_errors(self, code: str, return_final_answer: bool = False) -> Tuple[Any, str]:
        """
        Execute code in the sandbox and return results.

        Args:
            code: Python code to execute
            return_final_answer: Whether to extract a final answer

        Returns:
            Tuple of (result, stdout)
        """
        # When looking for final_answer, we need to extract it from the code execution result
        if return_final_answer:
            match = self.final_answer_pattern.search(code)
            if match:
                # Extract the expression being passed to final_answer
                result_expr = match.group(1)
                # Create a modified code that evaluates the expression and returns it in a way we can extract
                pre_final_answer_code = self.final_answer_pattern.sub("", code)
                modified_code = f"""{pre_final_answer_code}
import base64
import pickle
_result = {result_expr}
print("FINAL_ANSWER_PICKLE:" + base64.b64encode(pickle.dumps(_result)).decode())
"""
                response = self.client.execute_code(modified_code)
                if "stderr" in response and response["stderr"]:
                    raise AgentError(response["stderr"], self.logger)

                stdout = response.get("stdout", "")
                # Extract the pickled result from stdout
                pickle_pattern = re.compile(r"FINAL_ANSWER_PICKLE:(.+)$", re.MULTILINE)
                pickle_match = pickle_pattern.search(stdout)
                if pickle_match:
                    try:
                        pickle_data = pickle_match.group(1).strip()
                        result = pickle.loads(base64.b64decode(pickle_data))
                        # Remove the pickle line from stdout
                        stdout = pickle_pattern.sub("", stdout)
                        return result, stdout
                    except Exception as e:
                        raise AgentError(f"Failed to unpickle final answer: {e}", self.logger)

        # Regular code execution without final_answer
        response = self.client.execute_code(code)
        if "stderr" in response and response["stderr"]:
            raise AgentError(response["stderr"], self.logger)

        return None, response.get("stdout", "")

    def send_variables(self, variables: dict):
        """
        Send variables to the sandbox environment using pickle.
        """
        pickled_vars = base64.b64encode(pickle.dumps(variables)).decode()
        code = f"""
import pickle, base64
vars_dict = pickle.loads(base64.b64decode('{pickled_vars}'))
locals().update(vars_dict)
"""
        self.run_code_raise_errors(code)

    def install_packages(self, additional_imports: List[str]):
        """
        Install required packages in the sandbox environment using UV.

        Args:
            additional_imports: List of package names to install

        Returns:
            List of installed packages
        """
        if not additional_imports:
            return []

        # The server handles package installation via the 'packages' parameter
        response = self.client.execute_code("", packages=additional_imports)

        if "stderr" in response and response["stderr"]:
            self.logger.log(f"Warning during package installation: {response['stderr']}")

        # Verify installations by checking imports
        installed = []
        for package in additional_imports:
            try:
                # Extract the base package name (remove version specifiers)
                package_name = package.split("[")[0].split("==")[0].split(">")[0].split("<")[0].strip()
                # Check if the package can be imported
                check_response = self.client.execute_code(f"import {package_name}")
                if not check_response.get("stderr"):
                    installed.append(package)
                    self.logger.log(f"Successfully installed: {package}")
                else:
                    self.logger.log(f"Failed to import {package_name}: {check_response.get('stderr')}")
            except Exception as e:
                self.logger.log(f"Warning: Package {package} verification failed: {e}")

        return installed

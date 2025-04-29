# test_sandbox_executor.py

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.configs import SandboxVMConfig
from sandbox.executor import SandboxExecutor
from sandbox.sandbox import SandboxVMManager

logger = logging.getLogger("TestSandboxExecutor")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


def get_default_sandbox_vm_config() -> SandboxVMConfig:
    config = SandboxVMConfig(host_server_dir=Path("sandbox/server/"))
    return config


def main():
    config = get_default_sandbox_vm_config()
    with SandboxVMManager(config=config) as vm_manager:
        logger.info("✅ VM and servers are ready")

        executor = SandboxExecutor(config=config, additional_imports=[], logger=logger)
        logger.info("✅ Connected to Jupyter kernel")

        result, output = executor.run_code_raise_errors("x = 2 + 3\nprint(x)")
        print(f"🧪 stdout:\n{output}")

        code = """
x = 7
y = 8
# FINAL_ANSWER: x * y
"""
        result, output = executor.run_code_raise_errors(code, return_final_answer=True)
        print(f"🎯 Final result: {result}")
        print(f"🖨️ Output:\n{output}")

        installed = executor.get_installed_packages()
        print(f"📦 Installed packages: {installed}")

        executor.cleanup()
        logger.info("🧹 Executor cleanup complete")


if __name__ == "__main__":
    main()

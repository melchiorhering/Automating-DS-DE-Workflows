import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox.configs import SandboxVMConfig
from src.agent.executor import SandboxExecutor

logger = logging.getLogger("TestSandboxExecutor")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


def main():
    config = SandboxVMConfig(host_server_dir=Path("sandbox/server/"))

    # Start executor
    executor = SandboxExecutor(
        config=config,
        additional_imports=["numpy"],
        logger=logger,
        preserve_on_exit=False,
    )

    try:
        # Simple arithmetic
        result, logs = executor.run_code_raise_errors("2 + 3", return_final_answer=True)
        print("✅ Simple Result:", result)
        print("📋 Logs:", logs)

        # NumPy test
        numpy_code = """
import numpy as np
a = np.array([1, 2, 3])
b = np.array([4, 5, 6])
result = np.dot(a, b)
result
"""
        result, logs = executor.run_code_raise_errors(numpy_code, return_final_answer=True)
        print("✅ NumPy Result:", result)
        print("📋 NumPy Logs:", logs)

    finally:
        executor.delete()

    logger.info("🧹 Executor cleanup complete")


if __name__ == "__main__":
    main()

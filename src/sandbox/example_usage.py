from src.sandbox.sandbox_python_executor import SandboxPythonExecutor


# Create a simple logger
class SimpleLogger:
    def log(self, message, level=None):
        print(f"[{level or 'INFO'}] {message}")

    def log_error(self, message):
        print(f"[ERROR] {message}")


# Initialize the executor
additional_imports = ["numpy", "pandas"]
logger = SimpleLogger()
executor = SandboxPythonExecutor(
    additional_imports=additional_imports,
    logger=logger,
    base_url="http://localhost:8000",  # Point to your sandbox server
)

# Define tools
tools = {}  # Add your tools here if needed
executor.send_tools(tools)

# Example usage
try:
    code = """
import numpy as np
arr = np.array([1, 2, 3, 4, 5])
print("Mean:", arr.mean())
final_answer(f"The mean is {arr.mean()}")
"""
    output, logs, is_final_answer = executor(code)
    print(f"Output: {output}")
    print(f"Logs: {logs}")
    print(f"Is final answer: {is_final_answer}")
except Exception as e:
    print(f"Error: {e}")

import sys
from pathlib import Path

from smolagents.monitoring import AgentLogger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.executor import SandboxExecutor
from sandbox.configs import SandboxVMConfig

logger = AgentLogger()


def main():
    config = SandboxVMConfig(host_server_dir=Path("sandbox/server/"))

    # Start executor
    executor = SandboxExecutor(
        config=config,
        logger=logger,
        additional_imports=["numpy"],
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

        pyautogui_code = f"""
import pyautogui

# Move the mouse to the center of the screen
screen_width, screen_height = pyautogui.size()
center_x, center_y = screen_width // 2, screen_height // 2
pyautogui.moveTo(center_x, center_y)
# Take a screenshot
screenshot = pyautogui.screenshot()
screenshot.save('/mnt/{config.container_name}/screenshot.png')


print('Mouse moved to the center of the screen and screenshot saved.')
# Check if the screenshot was saved successfully
screenshot_path = '/mnt/{config.container_name}/screenshot.png'
if os.path.exists(screenshot_path):
    print(f'Screenshot saved at: %s' % screenshot_path)

        """
        result, logs = executor.run_code_raise_errors(pyautogui_code, return_final_answer=True)
        print("✅ Pyautogui Result:", result)
        print("📋 Pyautogui Logs:", logs)

    finally:
        executor.delete()

    logger.info("🧹 Executor cleanup complete")


if __name__ == "__main__":
    main()

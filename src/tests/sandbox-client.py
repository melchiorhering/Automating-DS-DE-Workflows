import logging
import sys
import time
from pathlib import Path

# Allow imports from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.configs import SandboxVMConfig
from sandbox.sandbox import SandboxVMManager

logger = logging.getLogger("TestSandboxVMManager")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


def get_test_vm_config() -> SandboxVMConfig:
    """Return a basic SandboxVMConfig for testing."""
    config = SandboxVMConfig(
        host_server_dir=Path("sandbox/server/"),  # Assuming your FastAPI + Jupyter server is in server/
        force_regenerate_client=True,  # Always regenerate client during test
    )
    return config


def main():
    # Step 1: Create config
    config = get_test_vm_config()

    # Step 2: Start VM inside a context manager
    with SandboxVMManager(config=config, logger=logger) as vm:
        logger.info("✅ VM started successfully")

        # Step 3: Use the dynamically generated client
        health = vm.sandbox_client.health()
        logger.info(f"🔍 Health check response: {health}")

        # Step 4: Try screenshot
        screenshot_result = vm.sandbox_client.take_screenshot()
        logger.info(f"📸 Screenshot taken at: {screenshot_result['screenshot_path']}")

        # Step 5: Try recording start/stop
        start_recording = vm.sandbox_client.start_recording()
        logger.info(f"🔴 Recording started: {start_recording}")

        time.sleep(10)  # simulate some activity

        stop_recording = vm.sandbox_client.stop_recording()
        logger.info(f"🛑 Recording stopped: {stop_recording}")

    # Step 6: After the block, the VM should have cleaned up automatically
    logger.info("🧹 VM Manager context exited cleanly.")


if __name__ == "__main__":
    main()

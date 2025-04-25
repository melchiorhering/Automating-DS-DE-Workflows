import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image
from smolagents import ActionStep, CodeAgent

# Add the parent of `sandbox/` (i.e., ./src/) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox import AgentVMConfig, SandboxPythonExecutor

# ───────────────────────────── Logger Setup ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AgentSandboxExecutorTest")

# ───────────────────────────── VM Configuration ─────────────────────────────
# This configuration is used to set up the VM environment for the agent.
cfg = AgentVMConfig(
    container_name="test-agent-vm",
    host_server_dir=Path("./sandbox/server/"),
    host_sandbox_server_port=8765,
    host_sandbox_server_host="localhost",
    sandbox_server_port=8765,
    sandbox_server_host="0.0.0.0",
    sandbox_server_dir=Path("/home/user/server"),
)


# ───────────────────────────── FastAPI Client Generator ─────────────────────────────
def generate_fastapi_client(openapi_url: str, output_dir: str = "vm_client") -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        openapi_path = Path(tmpdir) / "openapi.json"
        response = requests.get(openapi_url)
        response.raise_for_status()
        openapi_path.write_text(response.text)
        result = subprocess.run(
            [
                "openapi-python-client",
                "generate",
                "--path",
                str(openapi_path),
                "--output",
                output_dir,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Client generation failed:\n{result.stderr}")


# ───────────────────────────── Helpers & Utils ─────────────────────────
def save_screenshot_callback(memory_step: ActionStep, agent: CodeAgent):
    """Enhanced callback that takes screenshots with the FastAPI sandbox client.
    - Waits for animations to complete
    - Cleans up old screenshots to save memory
    - Provides detailed observation information
    """
    # Wait for any animations or UI updates to complete
    time.sleep(4.0)

    # Clean up previous screenshots to save memory
    current_step = memory_step.step_number
    for previous_memory_step in agent.memory.steps:
        if isinstance(previous_memory_step, ActionStep) and previous_memory_step.observations_images is not None:
            if previous_memory_step.step_number <= current_step - 2:
                previous_memory_step.observations_images = None

    # Take the screenshot
    result = client.take_screenshot()
    if "screenshot_path" in result:
        path = str(cfg.container_shared_dir / result["screenshot_path"])
        try:
            image = Image.open(path)
            memory_step.observations_images = [image.copy()]

            # Add detailed observation information
            mouse_info = f"Mouse position: {result['mouse_position']}"
            screen_info = f"Screen resolution: {image.size[0]}x{image.size[1]} pixels"

            # Get additional VM state if available
            vm_state = client.get_vm_state() if hasattr(client, "get_vm_state") else {}
            active_window = vm_state.get("active_window", "Unknown")

            observations = [
                f"🖼️ Screenshot captured at step {current_step}",
                mouse_info,
                screen_info,
                f"Active window: {active_window}",
            ]

            memory_step.observations = "\n".join(observations)
            print(f"Captured a VM screenshot: {image.size[0]}x{image.size[1]} pixels")
        except Exception as e:
            memory_step.observations = f"⚠️ Failed to load screenshot: {e}"


# ───────────────────────────── Test Config ─────────────────────────────


agent = CodeAgent(
    model=model,
    step_callbacks=[save_screenshot_callback],
    max_steps=20,
    verbosity_level=2,
)


agent.python_executor = SandboxPythonExecutor(additional_imports=["numpy", "pandas"], logger=logger)

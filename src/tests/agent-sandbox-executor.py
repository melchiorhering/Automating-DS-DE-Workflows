import sys
from pathlib import Path

from PIL import Image
from smolagents import ActionStep, LiteLLMModel, LogLevel, VLLMModel

# Allow imports from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import CodeAgent
from sandbox.configs import SandboxVMConfig

# ───────────────────────────── Agent Configuration ─────────────────────────────
# MODEL
# model_id = "meta-llama/Llama-3.3-70B-Instruct"
# model = InferenceClientModel(
#     token=os.getenv("HF_TOKEN"),  # model_id=model_id,
# )  # You can choose to not pass any model_id to InferenceClientModel to use a default model


# model = TransformersModel(model_id="Qwen/Qwen2.5-Coder-7B-Instruct", max_new_tokens=4096, device_map="auto")

# model = VLLMModel(model_id="HuggingFaceTB/SmolLM2-1.7B-Instruct")
model = VLLMModel(model_id="HuggingFaceTB/SmolVLM2-2.2B-Instruct")
model = LiteLLMModel(model_id="anthropic/claude-3-5-sonnet-latest", api_key="YOUR_ANTHROPIC_API_KEY")


# ───────────────────────────── Helpers & Utils ─────────────────────────
def take_initial_screenshot(agent: CodeAgent, label: str = "initial") -> None:
    client = agent.sandbox_client
    result = client.take_screenshot()
    if "screenshot_path" in result:
        try:
            host_shared = agent._sandbox_executor.vm.cfg.host_container_shared_dir
            path = str(host_shared / result["screenshot_path"])
            print(f"Image Path: {path}")
            image = Image.open(path)
            # image.show(title="Initial Screenshot")
            print(f"📸 Saved initial screenshot: {label}.png")
        except Exception as e:
            print(f"⚠️ Failed to save initial screenshot: {e}")


def save_screenshot_callback(memory_step: ActionStep, agent: CodeAgent) -> None:
    """Enhanced callback that takes screenshots with the FastAPI sandbox client."""
    # Wait for any animations or UI updates to complete

    # Clean up previous screenshots to save memory
    current_step = memory_step.step_number
    for previous_memory_step in agent.memory.steps:
        if isinstance(previous_memory_step, ActionStep) and previous_memory_step.step_number <= current_step - 2:
            previous_memory_step.observations_images = None

    # Take the screenshot using the sandbox client
    result = agent.sandbox_client.take_screenshot()
    if "screenshot_path" in result:
        print(f"Screenshot result: {result}")
        path = result["screenshot_path"]
        try:
            image = Image.open(path)
            memory_step.observations_images = [image.copy()]

            # Add detailed observation information
            mouse_info = f"Mouse position: {result['mouse_position']}"
            screen_info = f"Screen resolution: {image.size[0]}x{image.size[1]} pixels"

            observations = [
                f"🖼️ Screenshot captured at step {current_step}",
                mouse_info,
                screen_info,
            ]

            memory_step.observations = "\n".join(observations)
            print(f"Captured a VM screenshot: {image.size[0]}x{image.size[1]} pixels")
        except Exception as e:
            memory_step.observations = f"⚠️ Failed to load screenshot: {e}"


# Logger for debug output
config = SandboxVMConfig(container_name="sandbox-test", host_server_dir=Path("sandbox/server/"))
agent = CodeAgent(
    description="This agent runs in a sandboxed environment and can execute code.",
    tools=[],
    model=model,
    executor_type="sandbox",
    executor_kwargs={
        "config": config,
    },
    additional_authorized_imports=["pyautogui"],
    step_callbacks=[save_screenshot_callback],
    verbosity_level=LogLevel.DEBUG,
)
# take_initial_screenshot(agent, label="before_run")
output = agent.run(
    """Using pyautogui, move the mouse to the center of the screen.
Steps:
1. First check the current screen size using `pyautogui.size()`.
2. Use `pyautogui.moveTo(x, y)` to move the mouse to the center.
3. After each code run, a screenshot is taken and given to you as context.
4. To verify if the mouse is centered you can check the screenshot that is passed to your context.

⚠️ Return your code in the following format:

Code:
```python
import pyautogui
import time

# Step 1: Get the screen size
screen_size = pyautogui.size()
width, height = screen_size
# Step 2: Calculate the center position
center_x = width // 2
center_y = height // 2
# Step 3: Move the mouse to the center
pyautogui.moveTo(center_x, center_y)
# Step 4: Wait for a moment to see the mouse move
time.sleep(1)
```""",
    max_steps=2,
)
print(output)

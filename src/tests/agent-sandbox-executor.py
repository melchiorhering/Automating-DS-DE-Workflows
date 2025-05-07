import sys
import time
from pathlib import Path

from PIL import Image
from smolagents import ActionStep, VLLMModel
from smolagents.monitoring import LogLevel

# Allow imports from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import SandboxCodeAgent
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


# ───────────────────────────── Helpers & Utils ─────────────────────────
def take_initial_screenshot(agent: SandboxCodeAgent, label: str = "initial"):
    client = agent.sandbox_client
    result = client.take_screenshot()
    if "screenshot_path" in result:
        try:
            host_shared = agent._sandbox_executor.vm.cfg.host_container_shared_dir
            path = str(host_shared / result["screenshot_path"])
            print(f"Image Path: {path}")
            image = Image.open(path)
            print(image)
            print(f"📸 Saved initial screenshot: {label}.png")
        except Exception as e:
            print(f"⚠️ Failed to save initial screenshot: {e}")


def save_screenshot_callback(memory_step: ActionStep, agent: SandboxCodeAgent):
    """Enhanced callback that takes screenshots with the FastAPI sandbox client."""
    # Wait for any animations or UI updates to complete
    time.sleep(3.0)

    # Clean up previous screenshots to save memory
    current_step = memory_step.step_number
    for previous_memory_step in agent.memory.steps:
        if isinstance(previous_memory_step, ActionStep) and previous_memory_step.observations_images is not None:
            if previous_memory_step.step_number <= current_step - 2:
                previous_memory_step.observations_images = None

    # Take the screenshot using the sandbox client
    client = agent.sandbox_client
    result = client.take_screenshot()
    if "screenshot_path" in result:
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


config = SandboxVMConfig(container_name="sandbox-test", host_server_dir=Path("sandbox/server/"))
agent = SandboxCodeAgent(
    tools=[],
    model=model,
    executor_type="sandbox",
    executor_kwargs={
        "config": config,
        "preserve_on_exit": False,
    },
    additional_authorized_imports=["pyautogui"],
    step_callbacks=[save_screenshot_callback],
    verbosity_level=LogLevel.DEBUG,
)
take_initial_screenshot(agent, label="before_run")
# # Run the agent with the desired task
# agent_output = agent.run(
#     "move the mouse with pyautogui to the center of the screen, the current screen resolution is 1400x1050; use pyautogui.moveTo(x, y) to move the mouse",
#     max_steps=4,
#     stream=False,
# )

# print(
#     f"Agent output:{agent_output[0].model_output},\n",
# )


# time.sleep(5.0)


output = agent.run(
    """Using pyautogui, move the mouse to the center of the screen. The current screen resolution is 1400x1050. Use pyautogui.moveTo(x, y) to move the mouse.""",
    max_steps=4,
)


print("Docker executor result:", output)

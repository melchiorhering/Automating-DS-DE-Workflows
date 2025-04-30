import sys
import time
from pathlib import Path

from PIL import Image
from smolagents import ActionStep, VLLMModel

# Allow imports from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.sandbox_agent import SandboxCodeAgent
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
    client = agent.python_executor.vm.sandbox_client
    result = client.take_screenshot()
    if "screenshot_path" in result:
        path = result["screenshot_path"]
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


agent = SandboxCodeAgent(
    tools=[],
    model=model,
    executor_type="sandbox",
    executor_kwargs={
        "config": SandboxVMConfig(host_server_dir=Path("sandbox/server/")),
        "preserve_on_exit": False,
    },
    additional_authorized_imports=["pyautogui", "numpy", "pandas", "matplotlib"],
    step_callbacks=[save_screenshot_callback],
)


# Run the agent with the desired task
agent.run(
    "Give me some simple example of a python script that uses numpy and pandas to create a dataframe with random numbers and then plot it."
)

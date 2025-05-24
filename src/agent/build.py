from PIL import Image
from smolagents import ActionStep, LogLevel

from .sandbox_agent import SandboxCodeAgent


def take_initial_screenshot(agent: SandboxCodeAgent) -> None:
    host_shared = agent.python_executor.vm.cfg.host_container_shared_dir
    client = agent.sandbox_client
    result = client.take_screenshot()
    if "screenshot_path" in result:
        try:
            path = str(host_shared / result["screenshot_path"])
            image = Image.open(path)
            initial_step = ActionStep(
                step_number=0,
                model_output="Initial environment state.",
                observations="📸 Initial screenshot before execution.",
                observations_images=[image.copy()],
            )
            agent.logger.log(f"📸 Saved initial screenshot: {path}", level=LogLevel.DEBUG)
            # Adding it to memory as first step
            agent.memory.steps.append(initial_step)
        except Exception as e:
            initial_step.observations = f"⚠️ Failed to save initial screenshot: {e}"


def observation_screenshot_callback(memory_step: ActionStep, agent: SandboxCodeAgent) -> None:
    """Enhanced callback that takes screenshots with the FastAPI sandbox client."""
    host_shared = agent.python_executor.vm.cfg.host_container_shared_dir

    # Clean up previous screenshots to save memory
    current_step = memory_step.step_number
    for previous_memory_step in agent.memory.steps:
        if isinstance(previous_memory_step, ActionStep) and previous_memory_step.step_number <= current_step - 2:
            previous_memory_step.observations_images = None

    # Take the screenshot using the sandbox client
    result = agent.sandbox_client.take_screenshot()
    if "screenshot_path" in result:
        path = str(host_shared / result["screenshot_path"])
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
            agent.logger.log(
                f"""📸 Screenshot saved
            → Path: {path}
            → Observations:
                - {mouse_info}
                - {screen_info}""",
                level=LogLevel.DEBUG,
            )
            memory_step.observations = "\n".join(observations)
            # print(f"Captured a VM screenshot: {image.size[0]}x{image.size[1]} pixels")
        except Exception as e:
            memory_step.observations = f"⚠️ Failed to load screenshot: {e}"

import os
from pathlib import Path

from PIL import Image
from smolagents import ActionStep, LiteLLMModel

from agent import CodeAgent
from agent.utils.port_pool import PORT_MANAGER
from sandbox.configs import SandboxVMConfig

MODEL = LiteLLMModel(model_id="openai/o4-mini-2025-04-16", api_key=os.getenv("OPENAI_API_KEY"))


def _take_initial_screenshot(step: ActionStep, agent: CodeAgent):
    res = agent.sandbox_client.take_screenshot()
    shared = agent.python_executor.vm.cfg.host_container_shared_dir
    try:
        img = Image.open(shared / res["screenshot_path"])
        step.observations_images = [img.copy()]
    except Exception as exc:
        step.observations = f"⚠️ failed to load screenshot: {exc}"


def _observation_callback(step: ActionStep, agent: CodeAgent):
    for prev in agent.memory.steps:
        if isinstance(prev, ActionStep) and prev.step_number <= step.step_number - 2:
            prev.observations_images = None
    res = agent.sandbox_client.take_screenshot()
    try:
        img = Image.open(res["screenshot_path"])
        step.observations_images = [img.copy()]
        step.observations = (
            f"🖼️ Screenshot @ step {step.step_number}\n"
            f"Mouse {res['mouse_position']} | Screen {img.size[0]}×{img.size[1]}"
        )
    except Exception as exc:
        step.observations = f"⚠️ failed to load screenshot: {exc}"


def build_agent(container_name: str) -> CodeAgent:
    ports = PORT_MANAGER.get_ports(container_name)
    cfg = SandboxVMConfig(
        container_name=container_name,
        host_ssh_port=ports["ssh"],
        host_vnc_port=ports["vnc"],
        host_sandbox_server_port=ports["sandbox_server"],
        host_sandbox_jupyter_kernel_port=ports["jupyter"],
        host_services_dir=Path("sandbox/services/"),
    )
    agent = CodeAgent(
        description=f"Agent {container_name}",
        tools=[],
        model=MODEL,
        additional_authorized_imports=["pyautogui"],
        step_callbacks=[_observation_callback],
        executor_type="sandbox",
        executor_kwargs={"config": cfg},
    )
    first = ActionStep(0, "initial state", "📸 initial")
    _take_initial_screenshot(first, agent)
    agent.memory.steps.append(first)
    return agent

import logging
import os
import sys
import time
from pathlib import Path

from PIL import Image
from smolagents import ActionStep, CodeAgent, InferenceClientModel, PythonExecutor

# Add the parent of `sandbox/` (i.e., ./src/) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox import AgentVMConfig, AgentVMManager, SandboxPythonExecutor

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


# ───────────────────────────── Agent Configuration ─────────────────────────────
# MODEL
# model_id = "meta-llama/Llama-3.3-70B-Instruct"
model = InferenceClientModel(
    token=os.getenv("HF_TOKEN"),  # model_id=model_id,
)  # You can choose to not pass any model_id to InferenceClientModel to use a default model


class SandboxCodeAgent(CodeAgent):
    """
    A subclass of CodeAgent that adds support for the SandboxPythonExecutor.
    """

    def __init__(self, *args, agent_vm: AgentVMManager, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent_vm = agent_vm  # Store the agent_vm instance

    def create_python_executor(self) -> PythonExecutor:
        match self.executor_type:
            case "sandbox":
                # Retrieve agent_vm from executor_kwargs
                agent_vm = self.executor_kwargs.get("agent_vm")
                if not agent_vm:
                    raise ValueError("`agent_vm` must be provided in `executor_kwargs` for the sandbox executor.")
                return SandboxPythonExecutor(
                    agent_vm=agent_vm,
                    additional_imports=self.additional_authorized_imports,
                    logger=self.logger,
                )
            case _:  # Delegate to the parent class for other executor types
                return super().create_python_executor()


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
    client = agent.agent_vm.sandbox_client
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


# ───────────────────────────── Test Config ─────────────────────────────


def create_python_executor(
    executor_type: str, agent_vm: AgentVMManager, logger: logging.Logger
) -> SandboxPythonExecutor:
    """
    Create the appropriate Python executor based on the executor type.

    Args:
        executor_type: The type of executor to create (e.g., "sandbox").
        agent_vm: The running AgentVMManager instance.
        logger: Logger to use.

    Returns:
        An instance of the appropriate Python executor.
    """
    match executor_type:
        case "sandbox":
            return SandboxPythonExecutor(
                agent_vm=agent_vm,
                additional_imports=["numpy", "pandas"],
                logger=logger,
            )
        case _:  # if applicable
            raise ValueError(f"Unsupported executor type: {executor_type}")


# Initialize and start the AgentVMManager
with AgentVMManager(cfg, logger=logger, preserve_on_exit=True) as agent_vm:
    logger.info("✅ Agent VM is running and ready for use.")

    # Create the agent with the sandbox executor type
    agent = SandboxCodeAgent(
        tools=[],
        model=model,
        executor_type="sandbox",
        executor_kwargs={"agent_vm": agent_vm},  # Pass agent_vm in executor_kwargs
        agent_vm=agent_vm,  # Pass the agent_vm instance to the agent
        step_callbacks=[save_screenshot_callback],
        max_steps=20,
        verbosity_level=2,
    )

    # Run the agent with the desired task
    agent.run(
        "Give me some simple example of a python script that uses numpy and pandas to create a dataframe with random numbers and then plot it."
    )

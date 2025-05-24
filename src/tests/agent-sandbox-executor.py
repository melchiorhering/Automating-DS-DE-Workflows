import importlib
import os
import sys
from pathlib import Path

import yaml
from smolagents import LiteLLMModel, LogLevel

# Allow imports from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import SandboxCodeAgent, observation_screenshot_callback, take_initial_screenshot
from sandbox.configs import SandboxVMConfig

# ───────────────────────────── Agent Configuration ─────────────────────────────
# MODEL
# model_id = "meta-llama/Llama-3.3-70B-Instruct"
# model = InferenceClientModel(
#     token=os.getenv("HF_TOKEN"),  # model_id=model_id,
# )  # You can choose to not pass any model_id to InferenceClientModel to use a default model


# model = TransformersModel(model_id="Qwen/Qwen2.5-Coder-7B-Instruct", max_new_tokens=4096, device_map="auto")
# model = VLLMModel(model_id="HuggingFaceTB/SmolVLM2-2.2B-Instruct")
model = LiteLLMModel(model_id="openai/o4-mini", api_key=os.getenv("OPENAI_API_KEY"))


# ──────────────── Sandbox Executor Configuration ────────────────
# NORMAL CODE AGENT PROMPT TEMPLATE
prompt_templates = yaml.safe_load(importlib.resources.files("agent.prompts").joinpath("code_agent.yaml").read_text())
# STRUCTURED CODE AGENT PROMPT TEMPLATE NOT YET ADDED TO RELEASE SMOLEAGENT
# structured_prompt_templates = yaml.safe_load(
#     importlib.resources.files("agent.prompts").joinpath("structured_code_agent.yaml").read_text()
# )

config = SandboxVMConfig(container_name="sandbox-test", host_services_dir=Path("sandbox/services/"))
agent = SandboxCodeAgent(
    description="This agent runs in a sandboxed environment and can execute code.",
    tools=[],
    model=model,
    # add_base_tools=True,
    additional_authorized_imports=["pyautogui"],
    step_callbacks=[observation_screenshot_callback],
    executor_type="sandbox",
    executor_kwargs={
        "config": config,
    },
    prompt_templates=prompt_templates,
    verbosity_level=LogLevel.INFO,
)
# Take the screenshot and attach image data
take_initial_screenshot(
    agent,
)


# agent.ssh SETUP SPECIFIC


try:
    #     possible_task = agent.run(
    #         """Using pyautogui, move the mouse to the center of the screen.
    # Steps:
    # 1. First check the current screen size using `pyautogui.size()`.
    # 2. Use `pyautogui.moveTo(x, y)` to move the mouse to the center.
    # 3. After each code run, a screenshot is taken and given to you as context.
    # 4. To verify if the mouse is centered you can check the screenshot that is passed to your context.

    # ⚠️ Return your code in the following format:

    # Code:
    # ```python
    # import pyautogui
    # import time

    # # Step 1: Get the screen size
    # screen_size = pyautogui.size()
    # width, height = screen_size
    # # Step 2: Calculate the center position
    # center_x = width // 2
    # center_y = height // 2
    # # Step 3: Move the mouse to the center
    # pyautogui.moveTo(center_x, center_y)
    # # Step 4: Wait for a moment to see the mouse move
    # time.sleep(1)
    # ```

    # It helps to print the screensize and location of the mouse, then you can easily calculate if you are in the center.
    # When the mouse is in the center you are done, just run the final_answer function!
    # """,
    #         max_steps=7,
    #         stream=False,
    #     )
    #     print("Final output:")
    #     print(possible_task)

    hits_max_steps = agent.run("""FIND SOME API KEYS FOR ME IN YOUR SANDBOX ENVIRONMENT""", stream=False, max_steps=2)
    print("Final output:")
    print(hits_max_steps)

except KeyboardInterrupt:
    print("Keyboard interrupt detected. Exiting early.")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
finally:
    agent.cleanup()


# print(list(agent.memory.steps)[:-2])

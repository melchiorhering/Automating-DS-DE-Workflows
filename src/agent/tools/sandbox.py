from smolagents import ActionStep, CodeAgent, tool

from ..fastapi_client import FastAPISandboxClient


@tool
async def save_screenshot(memory_step: ActionStep, agent: CodeAgent):
    """Take a screenshot using the sandbox server and attach it to the current step memory."""
    sandbox = FastAPISandboxClient(agent.vm_url)
    data = await sandbox.take_screenshot()
    memory_step.observations = f"Screenshot saved to: {data.get('screenshot_path')}"

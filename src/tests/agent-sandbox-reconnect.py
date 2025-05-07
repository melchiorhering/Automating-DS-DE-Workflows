import logging
import sys
from pathlib import Path

import requests
from smolagents.models import ChatMessage
from smolagents.monitoring import AgentLogger, LogLevel
from smolagents.tools import Tool

# Allow imports from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import SandboxCodeAgent
from sandbox.configs import SandboxVMConfig


# Dummy model that returns a canned response
def dummy_model(messages):
    return ChatMessage(role="assistant", content="Test complete.")


# Dummy tool list (no tools used in this test)
tools: list[Tool] = []

# Basic logger setup
logger = logging.getLogger("sandbox-test")
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())

# Load config
config = SandboxVMConfig(
    container_name="sandbox-test",  # use a fixed name to support reconnect
    host_ssh_port=2222,
    host_sandbox_server_port=8765,
    host_sandbox_jupyter_kernel_port=8888,
    host_server_dir=Path("sandbox/server/"),
)

logger = AgentLogger(level=LogLevel.DEBUG)

# Create the agent with reconnect enabled
agent = SandboxCodeAgent(
    tools=tools,
    model=dummy_model,
    additional_authorized_imports=[],
    executor_kwargs={
        "config": config,
        "preserve_on_exit": True,
        "reconnect": True,
    },
    auto_cleanup=False,
    logger=logger,
)

# Test SSH command
print("🔐 Testing SSH command...")
try:
    result = agent.ssh.exec_command("echo 'sandbox-ok'")
    assert "sandbox-ok" in result["stdout"]
    print("✅ SSH test passed:", result["stdout"].strip())
except Exception as e:
    print("❌ SSH test failed:", e)

# Directly test the health endpoint
print("🌐 Testing /health endpoint directly...")
try:
    health_resp = requests.get(
        f"http://{config.host_sandbox_server_host}:{config.host_sandbox_server_port}/health",
        timeout=5,
    )
    health_resp.raise_for_status()
    print("✅ /health endpoint OK:", health_resp.json())
except requests.RequestException as e:
    print("❌ /health endpoint request failed:", e)

# Directly test the screenshot endpoint
print("🖼️ Testing /screenshot endpoint directly...")
try:
    screenshot_resp = requests.get(
        f"http://{config.host_sandbox_server_host}:{config.host_sandbox_server_port}/screenshot",
        params={"method": "pyautogui"},
        timeout=5,
    )
    screenshot_resp.raise_for_status()
    print("✅ /screenshot endpoint OK:", screenshot_resp.json())
except requests.RequestException as e:
    print("❌ /screenshot endpoint request failed:", e)

import sys
import time
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

# Logger for debug output
logger = AgentLogger(level=LogLevel.DEBUG)

# Load config with a fixed container name to allow reconnect
config = SandboxVMConfig(
    container_name="sandbox-test",
    host_ssh_port=2222,
    host_sandbox_server_port=8765,
    host_sandbox_jupyter_kernel_port=8888,
    host_server_dir=Path("sandbox/server/"),
)

# Create agent with reconnect enabled
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

# Test SSH
print("🔐 Testing SSH connection...")
try:
    result = agent.ssh.exec_command("echo 'sandbox-ok'")
    assert "sandbox-ok" in result["stdout"]
    print("✅ SSH test passed:", result["stdout"].strip())
except Exception as e:
    print("❌ SSH test failed:", e)

# Test /health
print("🌐 Testing /health endpoint...")
try:
    health = agent.sandbox_client.health()
    print("✅ /health response:", health)
except requests.RequestException as e:
    print("❌ /health endpoint failed:", e)

# Test /screenshot
print("📸 Testing /screenshot endpoint...")
try:
    screenshot = agent.sandbox_client.take_screenshot(method="pyautogui")
    print("✅ Screenshot taken:", screenshot)
except requests.RequestException as e:
    print("❌ /screenshot endpoint failed:", e)

# Simulate downtime and reconnect scenario
print("⏳ Waiting to simulate reconnect...")
time.sleep(20)

# Optional: run another test after waiting
print("🔁 Re-testing /health after wait...")
try:
    health = agent.sandbox_client.health()
    print("✅ /health (post-wait):", health)
except requests.RequestException as e:
    print("❌ /health (post-wait) failed:", e)

# Cleanup
agent.cleanup()

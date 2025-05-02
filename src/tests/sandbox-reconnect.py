import logging
import sys
from pathlib import Path

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

# Test FastAPI client
print("🌐 Testing FastAPI health check...")
try:
    health = agent.sandbox_client.health()
    print(health)
    assert health.get("status") == "ok"
    print("✅ FastAPI health check passed:", health)
except Exception as e:
    print("❌ FastAPI health check failed:", e)

# Clean up (optional)
# agent.cleanup()

import asyncio
import json
import logging
import socket
from pathlib import Path

from websockets import connect

from sandbox.virtualmachine import AgentVMConfig, AgentVMManager

# ───────────────────────────── Configuration ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("NotebookAgentVM")

SERVER_URI = "ws://localhost:8765"
host = SERVER_URI.split("://")[1].split(":")[0]
port = int(SERVER_URI.split(":")[2])

TEST_ACTIONS = [
    {
        "name": "Simple print",
        "action": "EXECUTE_CODE",
        "code": "print('Hello, world from test with no packages')",
    },
    {
        "name": "Requests import",
        "action": "EXECUTE_CODE",
        "code": "import requests\nprint('Requests version:', requests.__version__)",
        "packages": ["requests"],
    },
    {
        "name": "GUI move + click",
        "action": "EXECUTE_GUI_CODE",
        "code": "import pyautogui\npyautogui.moveTo(100, 200)\npyautogui.click(clicks=2)\nprint('PyAutoGUI test complete')",
        "packages": ["pyautogui"],
    },
    {
        "name": "Screenshot only",
        "action": "SCREENSHOT",
    },
]


def is_ws_up(host="localhost", port=8765, timeout=3):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


async def run_ws_tests():
    test_results = {}

    if not is_ws_up(host, port):
        logger.error(f"❌ WebSocket server is not running at {SERVER_URI}")
        return

    async with connect(SERVER_URI) as websocket:
        logger.info(f"✅ Connected to WebSocket server at {SERVER_URI}")

        for task in TEST_ACTIONS:
            name = task["name"]
            action = task["action"]
            try:
                logger.info(f"▶️  Sending test: {name}")
                await websocket.send(json.dumps(task))

                response_raw = await websocket.recv()
                response = json.loads(response_raw)

                logger.info(f"✅ Response received for '{name}'")
                logger.debug(json.dumps(response, indent=2))
                test_results[name] = {"action": action, "response": response}

            except Exception as e:
                logger.error(f"❌ Error during test '{name}': {e}")
                test_results[name] = {"action": action, "error": str(e)}

    logger.info("📋 Test Summary:\n")
    print(json.dumps(test_results, indent=2))


# ───────────────────────────── Main Orchestration ─────────────────────────────
if __name__ == "__main__":
    try:
        logger.info("🚀 Starting Agent VM and running test actions...")

        cfg = AgentVMConfig(
            container_name="test-agent-vm",
            host_server_dir=Path("server/"),
            host_sandbox_server_port=port,
            host_sandbox_server_host=host,
            sandbox_server_port=port,
            sandbox_server_host="0.0.0.0",
            sandbox_server_dir=Path("/home/user/server"),
        )

        with AgentVMManager(cfg, logger=logger) as vm:
            logger.info("✅ Agent VM is ready. Running tests...")
            asyncio.run(run_ws_tests())

    except Exception as e:
        logger.exception(f"❌ Error during Agent VM setup or test run: {e}")

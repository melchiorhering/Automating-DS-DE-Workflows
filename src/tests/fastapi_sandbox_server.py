# ./src/tests/fastapi_sandbox_server.py

import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Add the parent of `sandbox/` (i.e., ./src/) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox import AgentVMConfig, AgentVMManager

# ───────────────────────────── Logger Setup ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FastAPITest")

# ───────────────────────────── Test Config ─────────────────────────────

TEST_CASES = [
    {
        "name": "Basic code execution",
        "endpoint": "/execute",  # <- FIXED
        "payload": {"code": "print('Hello from FastAPI')"},
    },
    {
        "name": "Code with package install",
        "endpoint": "/execute",  # <- FIXED
        "payload": {
            "code": "import requests\nprint('Requests version:', requests.__version__)",
            "packages": ["requests"],
        },
    },
    {
        "name": "GUI interaction with screenshot",
        "endpoint": "/execute_gui",  # <- FIXED
        "payload": {
            "code": "import pyautogui\npyautogui.moveTo(100, 100)\nprint('GUI interaction done')",
            "packages": ["pyautogui"],
        },
    },
    {
        "name": "Screenshot only",
        "endpoint": "/screenshot",
    },
]


# ───────────────────────────── HTTP Test Logic ─────────────────────────────
def send_post(base_url: str, endpoint: str, data: dict) -> dict:
    url = f"{base_url}{endpoint}"
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(data).encode("utf-8")

    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.error(f"❌ HTTP error {e.code}: {e.reason}")
        return {"error": e.reason, "status": e.code}
    except urllib.error.URLError as e:
        logger.error(f"❌ URL error: {e.reason}")
        return {"error": str(e.reason)}
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return {"error": str(e)}


def run_tests(base_url: str = "http://localhost:8765") -> None:
    logger.info("🚀 Running FastAPI sandbox tests...\n")
    for test in TEST_CASES:
        logger.info(f"▶️  {test['name']}")
        result = send_post(base_url, test["endpoint"], test["payload"])
        print(json.dumps(result, indent=2))
        print()


# ───────────────────────────── Entry Point ─────────────────────────────
if __name__ == "__main__":
    cfg = AgentVMConfig(
        container_name="test-agent-vm",
        host_server_dir=Path("server/"),
        host_sandbox_server_port=8765,
        host_sandbox_server_host="localhost",
        sandbox_server_port=8765,
        sandbox_server_host="0.0.0.0",
        sandbox_server_dir=Path("/home/user/server"),
    )

    logger.info("🧪 Starting agent VM and launching tests...")
    try:
        with AgentVMManager(cfg, logger=logger) as vm:
            base_url = f"http://{cfg.host_sandbox_server_host}:{cfg.host_sandbox_server_port}"

            logger.info("✅ VM and FastAPI server are running — launching tests!")
            run_tests(base_url)
            logger.info("🧹 Finished tests. VM will shut down after this.")
            time.sleep(60)

    except Exception as e:
        logger.error(f"❌ Failed to set up and run VM tests: {e}")

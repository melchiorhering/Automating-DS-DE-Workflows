import json
import logging
import sys
import time
from pathlib import Path

# Add the parent of `sandbox/` (i.e., ./src/) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox import AgentVMConfig, AgentVMManager, VMServerClient

# ───────────────────────────── Logger Setup ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FastAPITest")

# ───────────────────────────── Test Config ─────────────────────────────
TEST_CASES = [
    {
        "name": "Basic code execution",
        "method": "execute_code",
        "args": {"code": "print('Hello from FastAPI')"},
    },
    {
        "name": "Code with package install",
        "method": "execute_code",
        "args": {
            "code": "import requests\nprint('Requests version:', requests.__version__)",
            "packages": ["requests"],
        },
    },
    {
        "name": "GUI interaction with screenshot",
        "method": "execute_gui_code",
        "args": {
            "code": """import pyautogui
            import time
            pyautogui.move(35, 60)
            time.sleep(5)
            pyautogui.click(clicks=2, interval=0.25)
            time.sleep(5)
            print('GUI interaction done')""",
            "packages": ["pyautogui"],
        },
    },
    {
        "name": "Screenshot only",
        "method": "take_screenshot",
        "args": {},
    },
    {
        "name": "List installed packages",
        "method": "list_packages",
        "args": {},
    },
    {
        "name": "Record user interaction",
        "method": "record_start",
        "args": {},
        "followup": {
            "delay": 10,
            "method": "record_stop",
            "args": {},
        },
    },
]


# ───────────────────────────── Run Tests ─────────────────────────────
def run_tests(client: VMServerClient) -> None:
    logger.info("🚀 Running FastAPI sandbox tests...\n")
    for test in TEST_CASES:
        logger.info(f"▶️  {test['name']}")
        method = getattr(client, test["method"], None)
        if not method:
            logger.error(f"❌ Unknown method: {test['method']}")
            continue

        try:
            result = method(**test["args"])
            print(json.dumps(result, indent=2))

            followup = test.get("followup")
            if followup:
                if test["name"].lower().startswith("record"):
                    input("🕹️  Press [Enter] when recording is finished...")
                else:
                    time.sleep(followup.get("delay", 10))

                logger.info(f"🔁 Follow-up: {followup['method']}")
                followup_method = getattr(client, followup["method"])
                result = followup_method(**followup.get("args", {}))
                print(json.dumps(result, indent=2))

        except Exception as e:
            logger.error(f"❌ Error running test '{test['name']}': {e}")
        print()


# ───────────────────────────── Entry Point ─────────────────────────────
if __name__ == "__main__":
    cfg = AgentVMConfig(
        container_name="test-agent-vm",
        host_server_dir=Path("./sandbox/server/"),
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
            client = VMServerClient(base_url)

            logger.info("✅ VM and FastAPI server are running — launching tests!")
            run_tests(client)

            # logger.info("💬 Entering interactive IPython shell — use `vm`, `vm.ssh`, or `client` interactively.")
            # embed(header="🧪 Interactive mode: test your agent VM manually here.")

    except Exception as e:
        logger.error(f"❌ Failed to set up and run VM tests: {e}")

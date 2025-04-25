import json
import logging
import sys
import time
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
        "name": "Screenshot only",
        "method": "take_screenshot",
        "args": {},
    },
    {
        "name": "List installed packages",
        "method": "list_installed_packages",
        "args": {},
    },
    {
        "name": "Record user interaction",
        "method": "start_recording",
        "args": {},
        "followup": {
            "delay": 60,
            "method": "stop_recording",
            "args": {},
        },
    },
]


# ───────────────────────────── Run Tests ─────────────────────────────
def run_tests(vm: AgentVMManager) -> None:
    logger.info("🚀 Running FastAPI sandbox tests...\n")
    client = vm.sandbox_client  # <── NEW: use the sandbox_client

    for test in TEST_CASES:
        logger.info(f"▶️  {test['name']}")
        method_name = test["method"]

        try:
            method = getattr(client, method_name, None)  # <── use client instead of vm
            if not method:
                logger.error(f"❌ Unknown method: {method_name}")
                continue

            args = test.get("args", {})
            result = method(**args)

            print(json.dumps(result, indent=2, default=str))

            followup = test.get("followup")
            if followup:
                time.sleep(followup.get("delay", 10))
                logger.info(f"🔁 Follow-up: {followup['method']}")
                followup_method = getattr(client, followup["method"], None)
                if followup_method:
                    followup_result = followup_method(**followup.get("args", {}))
                    print(json.dumps(followup_result, indent=2, default=str))
                else:
                    logger.error(f"❌ Follow-up method not found: {followup['method']}")

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
        force_regenerate_client=True,
    )

    logger.info("🧪 Starting agent VM and launching tests...")
    try:
        with AgentVMManager(cfg, logger=logger, preserve_on_exit=True) as vm:
            logger.info("✅ VM and FastAPI server are running — launching tests!")
            run_tests(vm)

            # time.sleep(180)  # Keep the VM running for a while to observe
            # any potential issues or for manual inspection
    except Exception as e:
        logger.error(f"❌ Failed to set up and run VM tests: {e}")

import json
import logging
import sys
import time
from pathlib import Path

# Add the parent of `sandbox/` (i.e., ./src/) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.agent import AgentVMConfig, AgentVMManager

# ───────────────────────────── Logger Setup ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SSHTest")


# ───────────────────────────── SSH Test ─────────────────────────────
def test_uv_list_packages(vm: AgentVMManager):
    logger.info("🚀 Running SSH uv pip list test...")

    try:
        result = vm.ssh.exec_command("uv pip list --format json")
        packages_json = result["stdout"]
        packages = json.loads(packages_json)

        logger.info(f"✅ Retrieved {len(packages)} packages via SSH.")
        print(json.dumps(packages, indent=2))

    except Exception as e:
        logger.error(f"❌ SSH test failed: {e}")


# ───────────────────────────── Entry Point ─────────────────────────────
if __name__ == "__main__":
    cfg = AgentVMConfig(
        container_name="test-agent-vm",
        host_server_dir=Path("./sandbox/server/"),
        host_sandbox_server_port=8765,
        host_sandbox_server_host="localhost",
        force_regenerate_client=False,
    )

    logger.info("🧪 Starting agent VM for SSH test...")
    try:
        with AgentVMManager(cfg, logger=logger, preserve_on_exit=True) as vm:
            logger.info("✅ VM is running — launching SSH test!")
            test_uv_list_packages(vm)

            time.sleep(180)

    except Exception as e:
        logger.error(f"❌ Failed to set up and run SSH test: {e}")

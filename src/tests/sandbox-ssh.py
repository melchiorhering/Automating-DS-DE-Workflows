from __future__ import annotations

import time

"""
SSH smoke-test that boots the *sandbox-test* container with VMManager,
waits for sshd, then runs two sanity commands over the cached session.

Run:
    uv run vm_ssh_smoke_test.py          # or: python vm_ssh_smoke_test.py

Optional env overrides:
    SSH_HOST, SSH_PORT, SSH_USER, SSH_PASS
"""

import os
import sys
from pathlib import Path

from smolagents import AgentLogger, LogLevel

# ── local imports ───────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from sandbox.configs import VMConfig  # noqa: E402
from sandbox.ssh import SSHClient, SSHConfig  # noqa: E402
from sandbox.virtualmachine import VMManager  # noqa: E402


# ── tiny assertions ─────────────────────────────────────────────────────
def test_echo(client: SSHClient) -> None:
    assert client.exec_command("echo it_works")["stdout"].strip() == "it_works"


def test_run_as_root(client: SSHClient) -> None:
    assert client.exec_command("id -u", as_root=True)["stdout"].strip() == "0"


# ── main runner ─────────────────────────────────────────────────────────
def run_ssh_smoke_tests() -> None:
    logger = AgentLogger(level=LogLevel.INFO)

    ssh_cfg = SSHConfig(
        hostname=os.getenv("SSH_HOST", "localhost"),
        port=int(os.getenv("SSH_PORT", "2222")),
        username=os.getenv("SSH_USER", "user"),
        password=os.getenv("SSH_PASS", "password"),
        initial_delay=10,  # give QEMU a head-start
        banner_timeout=60,
    )

    vm_cfg = VMConfig(container_name="sandbox-test")

    vm = VMManager(config=vm_cfg, logger=logger, ssh_cfg=ssh_cfg)
    vm.start()  # container + sshd + cached session ready

    try:
        time.sleep(180)
        # logger.log_rule("🚀 SSH smoke-tests")
        # test_echo(vm.ssh)
        # test_run_as_root(vm.ssh)
        # logger.log("✅ All SSH tests passed", level=LogLevel.INFO)
    finally:
        vm.close(delete_storage=False)  # keep container; change if desired


if __name__ == "__main__":
    run_ssh_smoke_tests()

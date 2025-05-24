# simplified_setup_helpers.py
# Minimal helper utilities to support updated orchestrator VM setup

import platform
import shutil
from pathlib import Path
from typing import Union

from smolagents import AgentLogger, LogLevel

from sandbox.configs import SandboxVMConfig
from sandbox.virtualmachine import SSHClient  # your updated class

default_logger = AgentLogger()


def upload_and_execute_script(
    ssh: SSHClient,
    cfg: SandboxVMConfig,
    local_path: Union[str, Path],
    remote_path: str = "/home/user/setup.sh",
    logger: AgentLogger = default_logger,
):
    local_path = Path(local_path).resolve()
    if logger:
        logger.log(f"📁 Uploading setup script: {local_path} → {remote_path}", level=LogLevel.DEBUG)
    ssh.put_file(local_path, remote_path)

    envs = {
        "SHARED_DIR": f"/mnt/{cfg.container_name}",
        "TASK_SETUP_LOG": str(cfg.sandbox_task_setup_log),
    }

    if logger:
        logger.log("🚀 Executing remote setup script with environment vars:")
        for k, v in envs.items():
            logger.log(f"   {k} = {v}")

    try:
        ssh.exec_command(f"chmod +x {remote_path} && bash {remote_path}", env=envs)
        logger.log("✅ Script execution result", level=LogLevel.DEBUG)
    except Exception as e:
        logger.log_error(f"❌ Setup script failed: {e}")
        raise


def upload_file_to_vm(
    ssh: SSHClient, local_path: Union[str, Path], remote_path: str, logger: AgentLogger | None = None
):
    local_path = Path(local_path).resolve()
    if logger:
        logger.log(f"📤 Uploading file to VM: {local_path} → {remote_path}")
    ssh.put_file(local_path, remote_path)


def download_file_from_vm(
    ssh: SSHClient, remote_path: str, local_path: Union[str, Path], logger: AgentLogger | None = None
):
    local_path = Path(local_path).resolve()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if logger:
        logger.log(f"📥 Downloading file from VM: {remote_path} → {local_path}")
    ssh.get_file(remote_path, local_path)


def copy_dir_if_missing(src: Union[str, Path], dst: Union[str, Path]):
    """Copy a directory tree if it doesn't already exist at the destination."""
    src, dst = Path(src), Path(dst)
    if not dst.exists():
        shutil.copytree(src, dst)


def convert_line_endings_unix(file_path: Union[str, Path]):
    """Ensure a file has Unix (LF) line endings."""
    file_path = Path(file_path)
    if platform.system() == "Windows":
        with open(file_path, "rb") as f:
            content = f.read().replace(b"\r\n", b"\n")
        with open(file_path, "wb") as f:
            f.write(content)

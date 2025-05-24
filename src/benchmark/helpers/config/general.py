# simplified_setup_helpers.py
# Minimal helper utilities to support updated orchestrator VM setup

import platform
import shutil
from pathlib import Path
from typing import Union

from smolagents import LogLevel

from agent.sandbox_agent import SandboxCodeAgent
from orchestrator import TaskSpec


def upload_and_execute_script(
    agent: SandboxCodeAgent,
    local_path: Union[str, Path],
    remote_path: str = "/home/user/setup.sh",
):
    local_path = Path(local_path).resolve()
    agent.logger.log(f"📁 Uploading setup script: {local_path} → {remote_path}", level=LogLevel.DEBUG)
    agent.ssh.put_file(local_path, remote_path)

    envs = {
        "SHARED_DIR": f"/mnt/{agent.python_executor.vm.cfg.container_name}",
        "TASK_SETUP_LOG": str(agent.python_executor.vm.cfg.sandbox_task_setup_log),
    }

    agent.logger.log("🚀 Executing remote setup script with environment vars:")
    for k, v in envs.items():
        agent.logger.log(f"   {k} = {v}")

    try:
        agent.ssh.exec_command(f"chmod +x {remote_path} && bash {remote_path}", env=envs)
        agent.logger.log("✅ Script execution result", level=LogLevel.DEBUG)
    except Exception as e:
        agent.logger.log(f"❌ Setup script failed: {e}", level=LogLevel.ERROR)
        raise


def upload_file_to_vm(task: TaskSpec, agent: SandboxCodeAgent, local_path: Union[str, Path], remote_path: str):
    local_path = (task.folder / Path(local_path)).resolve()
    agent.logger.log(f"📤 Uploading file to VM: {local_path} → {remote_path}")
    agent.ssh.put_file(local_path, remote_path)


def download_file_from_vm(task: TaskSpec, agent: SandboxCodeAgent, remote_path: str, local_path: Union[str, Path]):
    local_path = (task.folder / Path(local_path)).resolve()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    agent.logger.log(f"📥 Downloading file from VM: {remote_path} → {local_path}")
    agent.ssh.get_file(remote_path, local_path)


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

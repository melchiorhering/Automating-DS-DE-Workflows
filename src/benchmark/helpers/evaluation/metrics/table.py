import shutil
from pathlib import Path
from typing import Iterable

from smolagents import LogLevel

from agent.sandbox_agent import SandboxCodeAgent
from benchmark.helpers import download_file_from_vm
from orchestrator import TaskSpec


def compare_csv(
    task: TaskSpec,
    agent: SandboxCodeAgent,
    vm_result: str,
    local_expected: str,
    **options,
) -> float:
    task_id = task.uid
    out_dir = task.result / task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    local_vm_path = out_dir / Path(vm_result).name
    local_expected_path = out_dir / Path(local_expected).name

    # Download and prepare comparison files
    download_file_from_vm(task, agent, vm_result, local_vm_path)
    shutil.copy2(task.folder / local_expected, local_expected_path)

    # Read files
    with open(local_vm_path, encoding="utf-8", errors="ignore") as f:
        result_lines: Iterable[str] = f.read().splitlines()
    with open(local_expected_path, encoding="utf-8", errors="ignore") as f:
        expected_lines: Iterable[str] = f.read().splitlines()

    # Normalize
    if not options.get("strict", True):
        result_lines = map(str.strip, result_lines)
        expected_lines = map(str.strip, expected_lines)
    if options.get("ignore_case", False):
        result_lines = map(str.lower, result_lines)
        expected_lines = map(str.lower, expected_lines)
    if options.get("ignore_order", False):
        result_lines = sorted(result_lines)
        expected_lines = sorted(expected_lines)

    equal = list(result_lines) == list(expected_lines)
    agent.logger.log(f"📊 Evaluation complete — Match: {equal}", level=LogLevel.INFO)
    return float(equal)

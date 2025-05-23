import logging
import shutil
from pathlib import Path
from typing import Iterable

from benchmark.helpers import download_file_from_vm

logger = logging.getLogger("sandbox.metric.compare")


def compare_csv(
    ssh, folder: Path, vm_result: str, local_expected: str, output_dir: str = "results", **options
) -> float:
    """
    Compare two CSV files: one fetched from a VM, one local.

    Args:
        ssh: SSH client to the VM
        folder: Path to the task folder (used to name the results directory)
        vm_result: Path to the file on the VM
        local_expected: Path to the local expected file
        output_dir: Optional override for saving downloaded files
        options: Comparison modifiers like ignore_case, ignore_order, strict
    """
    task_id = folder.name
    out_dir = Path(output_dir) / task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    local_vm_path = out_dir / Path(vm_result).name
    local_expected_path = out_dir / Path(local_expected).name

    # Download the remote file and copy the local one
    download_file_from_vm(ssh, vm_result, local_vm_path)
    shutil.copy2(local_expected, local_expected_path)

    # Read both files
    with open(local_vm_path, encoding="utf-8", errors="ignore") as f:
        result_lines: Iterable[str] = f.read().splitlines()
    with open(local_expected_path, encoding="utf-8", errors="ignore") as f:
        expected_lines: Iterable[str] = f.read().splitlines()

    # Apply normalization options
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
    return float(equal)

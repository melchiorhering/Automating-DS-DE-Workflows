from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Union

from .errors import VMCreationError

# ────────────────────────────── Logging Setup ──────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ────────────────────────────── Configs ──────────────────────────────
@dataclass
class VMConfig:
    """Configuration for a QEMU virtual machine running in a Docker container.

    This class defines all basic parameters for setting up and running a VM,
    including container settings, VM hardware configuration, and filesystem paths.
    """

    # ──────────────── Container Settings ────────────────
    container_image: str = "qemux/qemu"  # Docker image for the VM container
    container_name: str = "qemu"  # Container name
    unique_container_name: bool = False  # Whether to generate unique container name with timestamp
    restart_policy: str = "always"  # Docker restart policy

    # ──────────────── VM Hardware Configuration ────────────────
    vm_ram: str = "4G"  # Amount of RAM for the VM
    vm_cpu_cores: int = 4  # Number of CPU cores for the VM

    # ──────────────── Network Configuration ────────────────
    host_vnc_port: int = 8006  # Host port for VNC access
    host_ssh_port: int = 2223  # Host port for SSH access
    extra_ports: Dict[Union[str, int], int] = field(default_factory=dict)  # Additional port mappings

    # ──────────────── Paths and Directories ────────────────
    root_dir: Path = Path("docker")  # Root directory for all VM resources
    guest_shared_dir: Path = Path("/shared")  # Shared directory path in guest

    # ──────────────── Other Settings ────────────────
    enable_debug: bool = True  # Enable debug mode
    extra_env: Dict[str, str] = field(default_factory=dict)  # Additional environment variables

    def __post_init__(self):
        # Generate unique container name if requested
        if self.unique_container_name:
            self.container_name = f"{self.container_name}_{int(time.time())}"

        # Resolve paths to absolute paths
        self.root_dir = self.root_dir.resolve()
        self.shared_root = self.root_dir / "shared"

        # Set up VM paths
        self.vms_dir = self.root_dir / "vms"
        self.vm_base_dir = self.vms_dir / "ubuntu-base"
        self.snapshots_dir = self.vms_dir / "snapshots"
        self.base_data = self.vm_base_dir / "data.img"

        # Set up container paths
        self.host_container_shared_dir = self.shared_root / self.container_name
        self.host_container_dir = self.snapshots_dir / self.container_name
        self.host_container_data = self.host_container_dir / "data.img"

        # Create required directories
        for p in (
            self.vm_base_dir,
            self.snapshots_dir,
            self.shared_root,
            self.host_container_dir,
            self.host_container_shared_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)
            # if os.access(p, os.W_OK | os.X_OK):
            #     os.chmod(p, 0o777)

        # Validate base VM files exist
        if not self.base_data.exists():
            raise VMCreationError("Missing base data.img")


# ────────────────────────────── Config ──────────────────────────────
@dataclass
class SandboxVMConfig(VMConfig):
    """Configuration for the Sandbox QEMU virtual machine running in a Docker container with sandbox capabilities."""

    host_sandbox_server_host: str = "localhost"
    host_sandbox_server_port: int = 8765
    host_sandbox_jupyter_kernel_host: str = "localhost"
    host_sandbox_jupyter_kernel_port: int = 8888
    host_services_dir: Optional[Path] = Path("services/")

    # Pyautogui settings
    sandbox_server_display: str = ":0"
    sandbox_server_xauth: str = "/run/user/1000/gdm/Xauthority"
    # FastAPI server
    sandbox_server_host: str = "0.0.0.0"
    sandbox_server_port: int = 8765  # Port for FastAPI server
    sandbox_services_dir: Optional[Path] = Path("/home/user/server")
    sandbox_services_log: str = "sandbox-services.log"
    # Jupyter kernel
    sandbox_jupyter_kernel_name: str = "sandbox-kernel"
    sandbox_jupyter_kernel_host: str = "0.0.0.0"
    sandbox_jupyter_kernel_port: int = 8888  # Port for Jupyter kernel
    sandbox_jupyter_kernel_log: str = "jupyter-kernel.log"

    # Extra's
    runtime_env: Dict[str, str] = field(default_factory=dict)
    additional_ports: Dict[int, int] = field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()
        # Add core ports
        self.extra_ports[self.sandbox_server_port] = self.host_sandbox_server_port
        self.extra_ports[self.sandbox_jupyter_kernel_port] = self.host_sandbox_jupyter_kernel_port

        # Add any user-defined extra ports
        self.extra_ports.update(self.additional_ports)

        self.runtime_env.setdefault("DISPLAY", self.sandbox_server_display)
        self.runtime_env.setdefault("XAUTHORITY", self.sandbox_server_xauth)

        if self.host_services_dir:
            self.host_services_dir = self.host_services_dir.resolve()
            if not self.host_services_dir.is_dir():
                raise VMCreationError(f"server_host_dir not found: {self.host_services_dir}")
            start_sh = self.host_services_dir / "start.sh"
            if not (start_sh.is_file() and os.access(start_sh, os.X_OK)):
                raise VMCreationError(f"start.sh missing or not executable: {start_sh}")

        self.client_output_dir = self.host_container_dir / "sandbox-client"

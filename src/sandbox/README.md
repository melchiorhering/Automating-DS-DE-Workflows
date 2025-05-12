# 🧪 Sandbox Environment: FastAPI + Jupyter Kernel Execution

## 🚀 Overview

This sandbox environment spins up a full virtual machine (VM) using QEMU in a Docker container. Inside the VM:

- A **FastAPI server** offers automation endpoints (e.g., screenshot, recording).
- A **Jupyter Kernel Gateway** enables safe Python code execution via WebSockets.

The entire system is orchestrated by the `SandboxVMManager`, and Python code can be run interactively via `SandboxExecutor`.

---

## 🧰 Architecture Summary

```
[ Orchestrator ] ⇄ [ SandboxVMManager ]
                          │
         ┌────────────────┴──────────────┐
         │ Inside the VM (Docker+QEMU)  │
         │                              │
         │  🧠 FastAPI server            │
         │    └── REST APIs             │
         │  🐍 Jupyter Kernel Gateway   │
         │    └── /api/kernels + WS     │
         └──────────────────────────────┘
```

---

## 📦 Directory Structure

```
📦sandbox/
 ┣ 📂server/                 # FastAPI + Kernel code inside the VM
 ┃ ┣📂 src/                  # Source modules (pyxcursor, recording)
 ┃ ┣📜 main.py               # FastAPI + kernel startup script
 ┃ ┣📜 start.sh              # Entrypoint script run by VM
 ┃ ┣📜 pyproject.toml        # uv/Poetry config
 ┃ ┗📜 uv.lock               # Dependency lockfile
 ┣📜 configs.py              # SandboxVMConfig: ports, paths, env
 ┣📜 sandbox.py              # SandboxVMManager: boot VM, start server, load client
 ┣📜 ssh.py                  # SSH client abstraction
 ┣📜 virtualmachine.py       # Base VM container + QEMU setup
 ┣📜 errors.py               # Custom exceptions
 ┣📜 README.md               # This file
 ┗📜 __init__.py
```

---

## 🧠 Features

### ✅ FastAPI Server

Auto-generated OpenAPI client provides typed access to:

| **Endpoint**             | **Client Module**                       | **Manager Method**          |
| ------------------------ | --------------------------------------- | --------------------------- |
| `GET /health`            | `health_check_health_get.py`            | `sandbox_health()`          |
| `GET /screenshot`        | `screenshot_endpoint_screenshot_get.py` | `sandbox_take_screenshot()` |
| `GET /record?mode=start` | `record_record_get.py`                  | `sandbox_start_recording()` |
| `GET /record?mode=stop`  | `record_record_get.py`                  | `sandbox_stop_recording()`  |

> The `SandboxClient` class in `sandbox.py` maps these automatically for you.

---

### 🧪 Jupyter Kernel Gateway

- Exposes `/api/kernels` to create kernels
- Uses WebSocket `/api/kernels/:id/channels` for code execution
- Final results are sent via a `print("RESULT_PICKLE:...")` payload

`SandboxExecutor` is responsible for:

- Installing packages with `uv`
- Connecting via HTTP and WS
- Executing code and capturing outputs
- Returning deserialized Python objects via base64+pickle

---

## 🧾 Kernel Execution Protocol

Example of returning a Python object:

```python
code = '''
from math import sqrt
x = 9
_result = sqrt(x)
print("RESULT_PICKLE:" + base64.b64encode(pickle.dumps(_result)).decode())
'''
```

This gets executed via WebSocket, and `_result` is returned as a native Python object.

---

## 🔧 Config Options (configs.py)

```python
from sandbox.configs import SandboxVMConfig

config = SandboxVMConfig(
    # Host → Container Port Mappings
    host_sandbox_server_host="localhost",
    host_sandbox_server_port=8765,
    host_sandbox_jupyter_kernel_host="localhost",
    host_sandbox_jupyter_kernel_port=8888,

    # FastAPI Server inside the container
    sandbox_server_host="0.0.0.0",
    sandbox_server_port=8765,
    sandbox_server_dir=Path("/home/user/server"),
    sandbox_server_log="sandbox-server.log",

    # Jupyter Kernel Gateway inside the container
    sandbox_jupyter_kernel_name="sandbox-kernel",
    sandbox_jupyter_kernel_host="0.0.0.0",
    sandbox_jupyter_kernel_port=8888,
    sandbox_jupyter_kernel_log="jupyter-kernel.log",

    # PyAutoGUI and display forwarding
    sandbox_server_display=":0",
    sandbox_server_xauth="/run/user/1000/gdm/Xauthority",

    # Local path to bind-mount server code into the VM
    host_server_dir=Path("server/"),

    # Docker/QEMU container options
    container_image="qemux/qemu",
    container_name="qemu",
    unique_container_name=True,
    restart_policy="always",
    vm_ram="4G",
    vm_cpu_cores=4,
    vm_disk_size="16G",
    vm_boot_image="ubuntu",

    # Networking (VNC, SSH, and additional ports)
    host_vnc_port=8006,
    host_ssh_port=2222,
    extra_ports={
        "8765/tcp": 8765,
        "8888/tcp": 8888,
        5900: 5900,  # e.g. VNC
    },

    # Paths
    root_dir=Path("docker"),
    guest_shared_dir=Path("/shared"),

    # Runtime customization
    enable_debug=True,
    runtime_env={"FOO": "BAR"},
)

```

All file paths and ports are mounted into the container via `extra_ports` and `Mount(...)`.

---

## 🧼 Cleanup and Exit

- `SandboxExecutor.cleanup()` shuts down the kernel and VM
- You can set `preserve_on_exit=True` to keep containers running
- Logs are stored in the shared folder and can be tailed via `sandbox.tail_server_logs()`

---

## 🔮 Future-Proof

- New API endpoints? Just restart the VM.
- New WS logic? Update `executor.py`, no client changes needed.
- Multiple agents? Each gets an isolated container, shared mount, and port mappings.

---

## 🏁 Conclusion

> The sandboxed VM environment gives you complete **isolation**, **typed control**, and **code execution** — wrapped in a single class.

Run arbitrary Python code, automate UIs, record interactions, and generate reproducible results — all from your orchestrator.

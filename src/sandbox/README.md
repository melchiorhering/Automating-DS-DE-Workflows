# 📄 Sandbox FastAPI Client Mapping

## Overview

The `AgentVMManager` dynamically generates and loads an OpenAPI Python client to interact with the sandbox server running inside the virtual machine.
This client provides typed methods for all API endpoints.

The table below describes the mapping between:

- **FastAPI server endpoints**,
- **Auto-generated OpenAPI client modules**, and
- **Convenience methods in `AgentVMManager`**.

```
sandbox/
 ┣ server/                 # FastAPI server running inside the VM
 ┃ ┣ src/                  # Source code for the server (pyxcursor, recording, utils)
 ┃ ┣ .python-version       # Python version for server
 ┃ ┣ README.md             # Instructions for the FastAPI server
 ┃ ┣ main.py               # Server entrypoint
 ┃ ┣ pyproject.toml        # Poetry/uv project config
 ┃ ┣ start.sh              # Script to start the server
 ┃ ┗ uv.lock               # Lockfile for packages
 ┣ README.md                # Main README (for the sandbox system)
 ┣ __init__.py              # Python module marker
 ┣ agent.py                 # 🧠 AgentVMManager (sandbox client API wrapper)
 ┣ errors.py                # 🚨 Custom exceptions (VMCreationError, VMOperationError, etc.)
 ┣ python_executer.py       # 🛠️ Utility for executing Python code safely inside the VM
 ┣ ssh.py                   # 📡 SSHClient abstraction for connecting to VM
 ┗ virtualmachine.py        # 🖥️  VMManager, VMConfig (low-level container + QEMU management)
```

---

## 🗺️ Endpoint → Client File → Manager Method Mapping

| **Sandbox Endpoint**     | **Generated Client File**                | **Manager Method**                     |
| :----------------------- | :--------------------------------------- | :------------------------------------- |
| `GET /health`            | `health_check_health_get.py`             | `sandbox_health()`                     |
| `POST /execute`          | `run_code_execute_post.py`               | `sandbox_execute_code(code, packages)` |
| `GET /screenshot`        | `screenshot_endpoint_screenshot_get.py`  | `sandbox_take_screenshot(method)`      |
| `GET /packages`          | `get_installed_packages_packages_get.py` | `sandbox_list_installed_packages()`    |
| `GET /record?mode=start` | `record_record_get.py`                   | `sandbox_start_recording()`            |
| `GET /record?mode=stop`  | `record_record_get.py`                   | `sandbox_stop_recording()`             |

---

## 🛠️ Technical Details

- After VM startup, the FastAPI server's `/openapi.json` is fetched.
- A client is generated using [`openapi-python-client`](https://github.com/openapi-generators/openapi-python-client) and dynamically imported.
- The methods always use `.sync_detailed(client=...).parsed` to ensure clean, parsed Python objects are returned.
- Client regeneration is **automatic** if the `AgentVMConfig.force_regenerate_client = True` flag is set.

---

## 📦 Directory Layout of the Client

Example structure of the generated client:

```
sandbox-client/
┗ sandbox_rest_server_client/
┣ api/
┃ ┗ default/
┃ ┣ health_check_health_get.py
┃ ┣ run_code_execute_post.py
┃ ┣ screenshot_endpoint_screenshot_get.py
┃ ┣ get_installed_packages_packages_get.py
┃ ┣ record_record_get.py
┃ ┗ __init__.py
┣ models/
┣ client.py
┣ types.py
┗ errors.py
```

---

## 🎯 Future-Proof

- New endpoints? → Auto-regenerate client — no need to manually update imports.
- Changes in OpenAPI spec? → Just rerun `start_agent_vm()` — everything stays synced.
- Multiple VMs? → Each VM uses its own isolated `sandbox-client/` directory.

---

## 📢 Note

If you want to **completely control** the OpenAPI client version or template, you can configure `openapi-python-client` via a custom `config.json` later (optional).

---

# 🚀 Conclusion

> 🔥 The sandbox VM framework is now **zero-maintenance** and **dynamic**!
> Auto-fetch OpenAPI schema → generate client → run typed API calls → full control from the orchestrator!

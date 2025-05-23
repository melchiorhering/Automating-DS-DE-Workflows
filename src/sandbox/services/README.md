# 🧪 Sandbox Services

A Python-based service suite that powers sandboxed virtual machines for secure code execution, GUI automation, and interactive control using WebSockets and REST APIs.

---

## 📦 Project Structure

```

📦services
┣ 📂src                    # Utility modules
┃ ┣ 📜__init__.py
┃ ┣ 📜pyxcursor.py         # Extracts mouse cursor bitmap using XFixes/X11
┃ ┣ 📜recording.py         # Handles input event recording (keyboard/mouse)
┃ ┗ 📜utils.py             # Shared helpers for screenshots, overlays, etc.
┣ 📜main.py                # FastAPI server entry point
┣ 📜start.sh               # Launch script for all services (GUI + API)
┣ 📜pyproject.toml         # Project metadata and dependencies (via uv)
┣ 📜uv.lock                # uv lockfile for reproducible environments
┣ 📜.python-version        # Python version pin (e.g. 3.11)
┗ 📜README.md              # You are here

```

---

## ⚙️ Features

- ✅ **FastAPI Server** for screenshotting and recording
- 🧠 **Jupyter Kernel Gateway** for executing Python (pyautogui) code via WebSocket
- 🖱️ **pyautogui + pyxcursor** for desktop automation and annotated screenshots
- 📸 Screenshot + cursor overlay
- 🎥 Input event recording via `pynput`
- 📦 On-demand dependency management with `uv`
- 🖥️ GUI automation using X11 (Wayland not supported)

---

## 🚀 How to Run

This is handled automatically inside the VM by `start.sh`. To run manually:

### 1. Start your X11 desktop environment inside the VM

Ensure that `DISPLAY` and `XAUTHORITY` are correctly set and accessible to your user.

### 2. Run the startup script

```bash
./start.sh
```

This script will:

- Create and activate a Python environment using `uv`
- Install dependencies listed in `pyproject.toml`
- Start the Jupyter Kernel Gateway on port `8888`
- Start the FastAPI server on port `8765`
- Log all output to `sandbox-services.log` and `jupyter-kernel.log` in the shared mount path

> Ports can be configured via environment variables (`PORT`, `JUPYTER_KERNEL_GATEWAY_APP_PORT`, etc.)

---

## 🌐 API Overview

### FastAPI Endpoints

| Method | Path                                        | Description                |
| ------ | ------------------------------------------- | -------------------------- |
| GET    | `/health`                                   | Liveness check             |
| GET    | `/screenshot`                               | Capture desktop screenshot |
| GET    | `/record?mode=start` or `/record?mode=stop` | Start/stop input recording |

### Jupyter Kernel Gateway

The Jupyter WebSocket API listens on:

```
ws://<host>:8888/api/kernels/<kernel_id>/channels
```

This is used by agents to stream code execution requests and capture outputs.

---

## 🧠 Notes

- `start.sh` assumes the VM user has access to an X11 session and is **not running Wayland**.
- The services require access to `.Xauthority` and `DISPLAY`, and use `xhost` to permit local connections.
- SSH server setup is optional, but recommended for remote control and testing.

---

## 🔒 Security Considerations

These services are designed to run **inside isolated VM sandboxes only**.
Never expose them to untrusted users or open networks without proper isolation.

---

## 📜 License

MIT License — see `LICENSE` for details.

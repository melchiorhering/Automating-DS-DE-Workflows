# src/server/src/websocket.py
"""
SandboxWebsocketServer
=================
A WebSocket server for executing Python code in a sandboxed environment.
ADD 'websocket' to the requirements.txt or pyproject.toml file.
"""

import asyncio
import contextlib
import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import numpy as np
import pyautogui
import websockets
from PIL import Image, ImageDraw, ImageFont, ImageGrab

from src.pyxcursor import Xcursor

# ───────────────────── Logger Setup ─────────────────────
# NEEDS TO GO TO os.getenv("SERVER_LOG", "sandbox-server.log")
log_path = os.path.join(os.getenv("SHARED_DIR", "/tmp/sandbox-server"), os.getenv("SERVER_LOG", "sandbox-server.log"))

os.makedirs(os.path.dirname(log_path), exist_ok=True)

logger = logging.getLogger("SandboxServer")
logger.setLevel(logging.DEBUG if os.getenv("DEBUG") == "1" else logging.INFO)

# Log to file
file_handler = logging.FileHandler(log_path)
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(file_handler)

# Optional: Also log to stdout (can remove in production)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(stream_handler)

logger.info("🔧 Logging to: %s", log_path)


# ───────────────────── Sandbox Server ─────────────────────
class SandboxServer:
    MAX_MESSAGE_SIZE = 2**27  # 128 MB

    def __init__(self, host: str, port: int, shared_dir: str):
        self.host = host
        self.port = port
        self.shared_dir = shared_dir
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
            "EXECUTE_CODE": self._handle_execute_code,
            "EXECUTE_GUI_CODE": self._handle_execute_gui_code,
            "SCREENSHOT": self._handle_screenshot,
        }

        logger.info(f"📸 Using screenshots path: {self.shared_dir}")
        os.makedirs(self.shared_dir, exist_ok=True)

        # Cursor handling
        try:
            self.cursor = Xcursor()
            logger.info("✅ Xcursor initialized successfully")
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize Xcursor: {e}")
            self.cursor = None

        # Detect screen size
        try:
            self.screen_width, self.screen_height = pyautogui.size()
            logger.info(f"🖥️ Screen size detected: {self.screen_width}x{self.screen_height}")
        except Exception as e:
            logger.warning(f"⚠️ pyautogui size fallback: {e}")
            self.screen_width, self.screen_height = 1920, 1080

    # ───────────────────── Request Handlers ─────────────────────
    async def _handle_execute_code(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self.run_code(data.get("code", ""), data.get("packages", []))

    async def _handle_execute_gui_code(self, data: Dict[str, Any]) -> Dict[str, Any]:
        code = data.get("code", "")
        packages = data.get("packages", [])
        client_id = data.get("client_id", "default")

        if "import pyautogui" not in code:
            code = "import pyautogui\n" + code

        result = await self.run_code(code, packages)
        if not result["stderr"]:
            screenshot_result = await self._handle_screenshot({"client_id": client_id})
            result["screenshot"] = screenshot_result.get("screenshot_path")
        return result

    async def _handle_screenshot(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client_id = data.get("client_id", "default")
            client_dir = os.path.join(self.shared_dir, client_id)
            os.makedirs(client_dir, exist_ok=True)

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
            filename = f"pyautogui-{timestamp}.png"
            container_screenshot_path = os.path.join(client_dir, filename)

            screenshot = ImageGrab.grab()
            screenshot_array = np.array(screenshot)
            screenshot_img = Image.fromarray(screenshot_array)
            draw = ImageDraw.Draw(screenshot_img)

            # Draw mouse
            mouse_x, mouse_y = pyautogui.position()
            box_size = 50
            draw.rectangle(
                [mouse_x - box_size // 2, mouse_y - box_size // 2, mouse_x + box_size // 2, mouse_y + box_size // 2],
                outline="red",
                width=2,
            )
            font = ImageFont.load_default()
            draw.text((mouse_x - box_size // 2, mouse_y - box_size // 2 - 20), "mouse", fill="red", font=font)

            # Optional cursor overlay
            if self.cursor:
                try:
                    cursor_array = self.cursor.getCursorImageArrayFast()
                    if cursor_array is not None:
                        cursor_img = Image.fromarray(cursor_array)
                        cursor_x = mouse_x - cursor_img.width // 2
                        cursor_y = mouse_y - cursor_img.height // 2
                        screenshot_img.paste(cursor_img, (cursor_x, cursor_y), cursor_img)
                except Exception as e:
                    logger.warning(f"⚠️ Cursor overlay failed: {e}")

            screenshot_img.save(container_screenshot_path)

            return {
                "screenshot_path": os.path.join(client_id, filename),
                "mouse_position": [mouse_x, mouse_y],
                "screen_size": [self.screen_width, self.screen_height],
                "client_id": client_id,
            }
        except Exception as e:
            logger.error(f"❌ Error taking screenshot: {e}")
            return {"status": "error", "message": str(e)}

    # ───────────────────── Code Execution ─────────────────────
    async def install_packages(self, packages: List[str]) -> Optional[str]:
        if not packages:
            return None
        try:
            result = await asyncio.create_subprocess_exec(
                "uv", "add", *packages, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await result.communicate()
            if result.returncode == 0:
                return None

            logger.warning("⚠️ 'uv add' failed, falling back to 'uv pip install'")
            result = await asyncio.create_subprocess_exec(
                "uv", "pip", "install", *packages, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await result.communicate()
            if result.returncode == 0:
                return None

            return f"❌ Failed to install: {', '.join(packages)}\n{stderr.decode().strip()}"
        except Exception as e:
            return f"❌ Package install error: {str(e)}"

    async def run_code(self, code: str, packages: Optional[List[str]] = None) -> Dict[str, str]:
        if not code.strip():
            return {"stdout": "", "stderr": "No code provided"}

        if packages:
            error = await self.install_packages(packages)
            if error:
                return {"stdout": "", "stderr": error}

        # Normalize indentation
        lines = code.splitlines()
        indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
        if indents:
            min_indent = min(indents)
            code = "\n".join([line[min_indent:] if line.strip() else line for line in lines])

        output_buffer, error_buffer = io.StringIO(), io.StringIO()

        def execute():
            try:
                with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(error_buffer):
                    exec(code, {})
            except Exception as e:
                error_buffer.write(str(e))

        await asyncio.to_thread(execute)

        return {
            "stdout": output_buffer.getvalue(),
            "stderr": error_buffer.getvalue(),
        }

    # ───────────────────── WebSocket Server ─────────────────────
    async def handle_client(self, websocket: websockets.server.ServerProtocol) -> None:
        logger.info(f"🌐 Client connected: {websocket.remote_address}")
        try:
            async for message in websocket:
                response = await self._process_message(message)
                if response:
                    await websocket.send(json.dumps(response))
        except websockets.exceptions.ConnectionClosedError:
            logger.warning("⚠️ Client disconnected unexpectedly.")
        except Exception as e:
            logger.exception(f"❌ Error in handle_client: {e}")
        finally:
            logger.info(f"🔌 Disconnected: {websocket.remote_address}")

    async def _process_message(self, raw_message: str) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(raw_message)
            action = data.get("action")
            if not action:
                return {"error": "No action specified"}
            if action not in self.handlers:
                return {"error": f"Unknown action: {action}"}
            return await self.handlers[action](data)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON"}
        except Exception as e:
            logger.exception(f"❌ Error in _process_message: {e}")
            return {"error": str(e)}

    async def start(self) -> None:
        logger.info(f"🚀 Starting WebSocket server at ws://{self.host}:{self.port}")
        async with websockets.serve(
            self.handle_client,
            self.host,
            self.port,
            max_size=self.MAX_MESSAGE_SIZE,
            write_limit=self.MAX_MESSAGE_SIZE,
            ping_interval=20,
            ping_timeout=30,
        ):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                logger.info("👋 Server shutdown initiated...")

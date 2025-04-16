# src/server/main.py
"""
SandboxServer
=================
A WebSocket server for executing Python code in a sandboxed environment. This server functions as an AI-agent tool for executing code in a sandboxed environment, as well as taking screenshots and recording actions.
The server is designed to be run in a containerized environment, with the ability to install packages and execute code securely. It uses pyautogui for GUI interactions and Xcursor for cursor overlay in screenshots.
It is designed to be used as a backend for a client application that communicates with it over WebSocket.
"""

import argparse
import asyncio
import contextlib
import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import numpy as np
import pyautogui
import websockets
from PIL import Image, ImageDraw, ImageFont, ImageGrab

from src.pyxcursor import Xcursor


class SandboxServer:
    """A modular WebSocket server for sandboxed code execution and optional GUI interaction."""

    MAX_MESSAGE_SIZE = 2**27  # 128 MB limit

    def __init__(self, host: str, port: int, screenshots_path: str):
        self.host = host
        self.port = port
        self.screenshots_path = screenshots_path

        self.logger = self._setup_logging()
        self.logger.info(f"Using screenshots path: {self.screenshots_path}")

        self.handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
            "EXECUTE_CODE": self._handle_execute_code,
            "EXECUTE_GUI_CODE": self._handle_execute_gui_code,
            "SCREENSHOT": self._handle_screenshot,
        }

        os.makedirs(self.screenshots_path, exist_ok=True)

        try:
            self.cursor = Xcursor()
            self.logger.info("Xcursor initialized successfully")
        except Exception as e:
            self.logger.warning(f"Failed to initialize Xcursor: {e}. Screenshots will not have cursor overlay.")
            self.cursor = None

        try:
            screen = ImageGrab.grab()
            self.screen_width, self.screen_height = screen.size
            self.logger.info(f"Screen size detected: {self.screen_width}x{self.screen_height}")
        except Exception as e:
            self.logger.warning(f"Failed to get screen size: {e}. Using default values.")
            self.screen_width, self.screen_height = 1920, 1080

    def _setup_logging(self) -> logging.Logger:
        logger = logging.getLogger("SandboxServer")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(console_handler)
        return logger

    async def _handle_execute_code(self, data: Dict[str, Any]) -> Dict[str, Any]:
        code = data.get("code", "")
        packages = data.get("packages", [])
        return await self.run_code(code, packages)

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
            client_dir = os.path.join(self.screenshots_path, client_id)
            os.makedirs(client_dir, exist_ok=True)

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
            filename = f"pyautogui-{timestamp}.png"
            screenshot_path = os.path.join(client_id, filename)
            container_screenshot_path = os.path.join(client_dir, filename)

            screenshot = ImageGrab.grab()
            screenshot_array = np.array(screenshot)
            # Directly use pyautogui to get the current mouse position.
            mouse_x, mouse_y = pyautogui.position()

            screenshot_img = Image.fromarray(screenshot_array)
            draw = ImageDraw.Draw(screenshot_img)
            box_size = 50
            draw.rectangle(
                [mouse_x - box_size // 2, mouse_y - box_size // 2, mouse_x + box_size // 2, mouse_y + box_size // 2],
                outline="red",
                width=2,
            )
            font = ImageFont.load_default()
            draw.text((mouse_x - box_size // 2, mouse_y - box_size // 2 - 20), "mouse", fill="red", font=font)

            if self.cursor is not None:
                try:
                    cursor_array = self.cursor.getCursorImageArrayFast()
                    if cursor_array is not None:
                        cursor_img = Image.fromarray(cursor_array)
                        cursor_x = mouse_x - cursor_img.width // 2
                        cursor_y = mouse_y - cursor_img.height // 2
                        screenshot_img.paste(cursor_img, (cursor_x, cursor_y), cursor_img)
                except Exception as e:
                    self.logger.warning(f"Failed to overlay cursor: {e}")

            screenshot_img.save(container_screenshot_path)

            return {
                "screenshot_path": screenshot_path,
                "mouse_position": [mouse_x, mouse_y],
                "screen_size": [self.screen_width, self.screen_height],
                "client_id": client_id,
            }
        except Exception as e:
            self.logger.error(f"Error taking screenshot: {e}")
            return {"status": "error", "message": str(e)}

    async def install_packages(self, packages: List[str]) -> Optional[str]:
        if not packages:
            return None
        try:
            process = await asyncio.create_subprocess_exec(
                "uv", "add", *packages, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
            if process.returncode == 0:
                return None

            self.logger.warning("'uv add' failed, trying 'uv pip install'")
            process = await asyncio.create_subprocess_exec(
                "uv", "pip", "install", *packages, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
            if process.returncode == 0:
                return None

            return f"Failed to install packages: {', '.join(packages)}\nError: {stderr.decode().strip()}"
        except Exception as e:
            return f"Installation error: {str(e)}"

    async def run_code(self, code: str, packages: Optional[List[str]] = None) -> Dict[str, str]:
        if not code.strip():
            return {"stdout": "", "stderr": "No code provided"}

        if packages:
            error = await self.install_packages(packages)
            if error:
                return {"stdout": "", "stderr": error}

        lines = code.split("\n")
        if len(lines) > 1:
            indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
            if indents:
                min_indent = min(indents)
                lines = [line[min_indent:] if line.strip() else line for line in lines]
            code = "\n".join(lines)

        output_buffer = io.StringIO()
        error_buffer = io.StringIO()

        def execute():
            # Execute code without providing additional controller objects.
            exec_globals = {}
            with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(error_buffer):
                exec(code, exec_globals)

        try:
            await asyncio.to_thread(execute)
            return {"stdout": output_buffer.getvalue(), "stderr": error_buffer.getvalue()}
        except Exception as e:
            return {"stdout": "", "stderr": f"Execution error: {str(e)}"}

    async def handle_client(self, websocket: websockets.ServerProtocol) -> None:
        self.logger.info(f"Client connected: {websocket.remote_address}")
        try:
            async for message in websocket:
                response = await self._process_message(message)
                if response is not None:
                    await websocket.send(json.dumps(response))
        except websockets.exceptions.ConnectionClosedError:
            self.logger.warning("Client disconnected unexpectedly.")
        except Exception as e:
            self.logger.error(f"Error in handle_client: {e}")
        finally:
            self.logger.info(f"Client {websocket.remote_address} disconnected.")

    async def _process_message(self, raw_message: str) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(raw_message)
            action = data.get("action")
            if not action:
                return {"error": "No action specified"}

            handler = self.handlers.get(action)
            if handler is None:
                return {"error": f"Unknown action: {action}"}

            return await handler(data)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in request"}
        except Exception as e:
            self.logger.error(f"_process_message error: {e}")
            return {"error": str(e)}

    def get_mouse_position(self) -> Tuple[int, int]:
        return pyautogui.position()

    async def start(self) -> None:
        self.logger.info(f"Starting WebSocket server on ws://{self.host}:{self.port}")
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
                self.logger.info("Server shutdown initiated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sandbox WebSocket Server")
    parser.add_argument("--host", type=str, default=os.getenv("HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8765")))
    parser.add_argument("--screenshots-path", type=str)
    args = parser.parse_args()

    # Log the screenshots path to verify it matches the expected shared path inside the container
    logging.info(f"Starting SandboxServer with screenshots path: {args.screenshots_path}")

    server = SandboxServer(args.host, args.port, args.screenshots_path)
    asyncio.run(server.start())

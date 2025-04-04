import argparse
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
from PIL import Image, ImageDraw, ImageFont

from server.pyxcursor import Xcursor
from server.recorder import ActionRecorder


class SandboxServer:
    """A modular WebSocket server for sandboxed code execution and optional GUI interaction."""

    SANDBOX_SCREENSHOTS = "/tmp/screenshots"  # Update this to match the container mount point
    MAX_MESSAGE_SIZE = 2**27  # 128 MB limit for inbound/outbound messages

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.recorder = ActionRecorder()

        # Updated handlers dictionary with the new "EXECUTE_GUI_CODE" action
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
            "EXECUTE_CODE": self._handle_execute_code,
            "EXECUTE_GUI_CODE": self._handle_execute_gui_code,
            "START_RECORDING": self._handle_start_recording,
            "STOP_RECORDING": self._handle_stop_recording,
            "SCREENSHOT": self._handle_screenshot,  # new
        }

        self._setup_logging()
        os.makedirs(self.SANDBOX_SCREENSHOTS, exist_ok=True)

        # Initialize Xcursor for screenshot cursor overlay
        try:
            self.cursor = Xcursor()
            self.logger.info("Xcursor initialized successfully")
        except Exception as e:
            self.logger.warning(f"Failed to initialize Xcursor: {e}. Screenshots will not have cursor overlay.")
            self.cursor = None

    def _setup_logging(self) -> None:
        """Configure the server's logging settings."""
        self.logger = logging.getLogger("SandboxServer")
        self.logger.setLevel(logging.INFO)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self.logger.addHandler(console_handler)

    async def _handle_execute_code(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle general code execution requests."""
        code = data.get("code", "")
        packages = data.get("packages", [])
        return await self.run_code(code, packages)

    async def _handle_execute_gui_code(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle code execution requests restricted to PyAutoGUI usage.
        After running the code, automatically take a screenshot.
        """
        code = data.get("code", "")
        packages = data.get("packages", [])
        client_id = data.get("client_id", "default")

        # Add import statement at the beginning if not present
        if "import pyautogui" not in code:
            code = "import pyautogui\n" + code

        result = await self.run_code(code, packages)
        if not result["stderr"]:
            # Only take a screenshot if code ran successfully
            screenshot_result = await self._handle_screenshot({"client_id": client_id})
            result["screenshot"] = screenshot_result.get("screenshot_path")
        return result

    async def _handle_screenshot(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Take a screenshot and return path plus metadata."""
        try:
            client_id = data.get("client_id", "default")

            # Create client-specific screenshot directory in container
            client_dir = os.path.join(self.SANDBOX_SCREENSHOTS, client_id)
            os.makedirs(client_dir, exist_ok=True)

            # Screenshot file name includes client ID and timestamp
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
            filename = f"pyautogui-{timestamp}.png"
            screenshot_path = os.path.join(client_id, filename)
            container_screenshot_path = os.path.join(client_dir, filename)

            # Take screenshot and process
            screenshot = pyautogui.screenshot()
            screenshot_array = np.array(screenshot)

            # Get cursor position
            mouse_x, mouse_y = pyautogui.position()

            # Convert to PIL Image and add annotations
            screenshot_img = Image.fromarray(screenshot_array)
            draw = ImageDraw.Draw(screenshot_img)

            # Draw box around cursor
            box_size = 50
            draw.rectangle(
                [mouse_x - box_size // 2, mouse_y - box_size // 2, mouse_x + box_size // 2, mouse_y + box_size // 2],
                outline="red",
                width=2,
            )

            # Add "mouse" label
            font = ImageFont.load_default()
            draw.text((mouse_x - box_size // 2, mouse_y - box_size // 2 - 20), "mouse", fill="red", font=font)

            # Overlay cursor if available
            if self.cursor is not None:
                try:
                    cursor_array = self.cursor.getCursorImageArrayFast()
                    if cursor_array is not None:
                        cursor_img = Image.fromarray(cursor_array)
                        # Get cursor dimensions to properly center it
                        cursor_width, cursor_height = cursor_img.size
                        # Offset by half the cursor size to center it at the mouse position
                        cursor_x = mouse_x - cursor_width // 2
                        cursor_y = mouse_y - cursor_height // 2
                        screenshot_img.paste(cursor_img, (cursor_x, cursor_y), cursor_img)
                except Exception as e:
                    self.logger.warning(f"Failed to overlay cursor: {e}")

            # Save the final image
            screenshot_img.save(container_screenshot_path)
            width, height = pyautogui.size()

            return {
                "screenshot_path": screenshot_path,
                "mouse_position": [mouse_x, mouse_y],
                "screen_size": [width, height],
                "client_id": client_id,
            }
        except Exception as e:
            self.logger.error(f"Error taking screenshot with metadata: {e}")
            return {"status": "error", "message": str(e)}

    async def install_packages(self, packages: List[str]) -> Optional[str]:
        """
        Install Python packages using UV with fallback mechanism.
        First tries 'uv add', then falls back to 'uv pip install' if that fails.

        Args:
            packages: List of package names to install

        Returns:
            None if successful, error message string if failed
        """
        if not packages:
            return None

        # Try uv add first
        try:
            process = await asyncio.create_subprocess_exec(
                "uv", "add", *packages, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                self.logger.info(f"Successfully installed packages with 'uv add': {', '.join(packages)}")
                return None

            # If uv add failed, try uv pip install
            self.logger.warning("'uv add' failed, trying 'uv pip install'")
            process = await asyncio.create_subprocess_exec(
                "uv", "pip", "install", *packages, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                self.logger.info(f"Successfully installed packages with 'uv pip install': {', '.join(packages)}")
                return None

            # Both methods failed
            error_msg = stderr.decode().strip()
            self.logger.error(f"Failed to install packages: {error_msg}")
            return f"Failed to install packages: {', '.join(packages)}\nError: {error_msg}"

        except Exception as e:
            self.logger.error(f"Exception during package installation: {str(e)}")
            return f"Installation error: {str(e)}"

    async def run_code(self, code: str, packages: Optional[List[str]] = None) -> Dict[str, str]:
        """
        Execute Python code with optional package installation.
        Returns a dict with 'stdout' and 'stderr'.
        """
        if not code.strip():
            return {"stdout": "", "stderr": "No code provided"}

        if packages:
            error = await self.install_packages(packages)
            if error:
                return {"stdout": "", "stderr": error}

        # Clean up the code indentation more aggressively
        lines = code.split("\n")
        # Remove any common indentation
        if len(lines) > 1:
            # Find minimum non-zero indentation
            indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
            if indents:
                min_indent = min(indents)
                # Remove that amount of indentation from all lines
                lines = [line[min_indent:] if line.strip() else line for line in lines]
            code = "\n".join(lines)

        output_buffer = io.StringIO()
        error_buffer = io.StringIO()

        def execute():
            with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(error_buffer):
                exec(code, {})

        try:
            # Offload CPU-bound execution to a separate thread
            await asyncio.to_thread(execute)
            return {"stdout": output_buffer.getvalue(), "stderr": error_buffer.getvalue()}
        except Exception as e:
            self.logger.error(f"Exception during code execution: {e}")
            return {"stdout": "", "stderr": f"Execution error: {str(e)}"}

    async def handle_client(self, websocket: websockets.ServerConnection) -> None:
        """
        Main loop for each connected client.
        Receives messages, parses JSON, and calls the appropriate handler.
        """
        self.logger.info(f"Client connected: {websocket.remote_address}")

        try:
            async for message in websocket:
                response = await self._process_message(message)
                # Send response back to the client
                if response is not None:
                    await websocket.send(json.dumps(response))
        except websockets.exceptions.ConnectionClosedError:
            self.logger.warning("Client disconnected unexpectedly.")
        except Exception as e:
            self.logger.error(f"Error in handle_client: {e}")
        finally:
            self.logger.info(f"Client {websocket.remote_address} disconnected.")

    async def _process_message(self, raw_message: str) -> Optional[Dict[str, Any]]:
        """
        Parse the incoming message as JSON, route to the appropriate handler based on 'action'.
        Returns a dict to send back as a response, or None if no response is needed.
        """
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

    async def _handle_start_recording(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Start action recording."""
        if not self.recorder.is_recording:
            self.recorder.start_recording()
            self.logger.info("Recording started.")
        return {"status": "recording started"}

    async def _handle_stop_recording(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Stop action recording."""
        if self.recorder.is_recording:
            self.recorder.stop_recording()
            self.logger.info("Recording stopped.")
            self.recorder = ActionRecorder()  # give yourself a fresh instance
        return {"status": "recording stopped"}

    async def start(self) -> None:
        """Start the WebSocket server and run indefinitely until canceled."""
        self.logger.info(f"Starting WebSocket server on ws://{self.host}:{self.port}")
        async with websockets.serve(
            self.handle_client,
            self.host,
            self.port,
            max_size=self.MAX_MESSAGE_SIZE,
            write_limit=self.MAX_MESSAGE_SIZE,
            ping_interval=20,  # Keep connections alive
            ping_timeout=30,
        ):
            try:
                await asyncio.Future()  # Run forever
            except asyncio.CancelledError:
                self.logger.info("Server shutdown initiated.")
                if self.recorder.is_recording:
                    self.recorder.stop_recording()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sandbox WebSocket Server")
    parser.add_argument("--host", type=str, default=os.getenv("HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8765")))
    args = parser.parse_args()

    server = SandboxServer(args.host, args.port)
    asyncio.run(server.start())

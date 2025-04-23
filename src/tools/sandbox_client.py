# src/sandbox_client.py
"""
SandboxClient
=================
A client to interact with a sandbox server using WebSocket.
This client allows you to execute Python code, take screenshots, and record user actions. It is designed for using it as a client inside a HuggingFace AI Agent TOOL
"""

import json
import logging
import textwrap
import uuid
from typing import Any, Dict, List, Optional

import websockets


class SandboxClient:
    """
    A client to interact with the sandbox server using WebSocket.
    """

    def __init__(
        self,
        uri: str,
        client_id: Optional[str] = None,
        logger: logging.Logger = None,
    ):
        """
        Initialize the client.

        Args:
            uri (str): The WebSocket server URI.
            client_id (Optional[str]): Custom client identifier. If None, generates UUID.
        """
        self.uri = uri
        self.websocket = None
        self.client_id = client_id if client_id is not None else str(uuid.uuid4())
        self.log = logger or logging.Logger

    async def __aenter__(self):
        """Support for async context manager."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup for async context manager."""
        await self.disconnect()

    async def connect(self) -> None:
        """
        Connect to the WebSocket server.
        """
        try:
            self.websocket = await websockets.connect(self.uri)
            self.log.info(f"Connected to WebSocket server at {self.uri}")
        except websockets.InvalidURI:
            self.log.error(f"Invalid WebSocket URI: {self.uri}")
            raise
        except websockets.InvalidHandshake as e:
            self.log.error(f"Failed WebSocket handshake with server: {e}")
            raise
        except Exception as e:
            self.log.error(f"Failed to connect to WebSocket server: {e}")
            raise

    async def disconnect(self) -> None:
        """
        Disconnect from the WebSocket server.
        """
        if self.websocket:
            await self.websocket.close()
            self.log.info("Disconnected from WebSocket server")

    async def send_command(self, action: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Send a command to the sandbox server.

        Args:
            action: The action to perform (e.g., "EXECUTE_CODE", "SCREENSHOT")
            data: Additional data to send with the command

        Returns:
            The server's response as a dictionary
        """
        if not self.websocket:
            raise RuntimeError("WebSocket connection is not established.")

        try:
            # Create payload with action and client_id
            payload = {"action": action, "client_id": self.client_id}

            # Add any additional data if provided
            if data:
                payload.update(data)

            await self.websocket.send(json.dumps(payload))
            self.log.info(f"Sent: {action}")

            response = await self.websocket.recv()
            return json.loads(response)
        except Exception as e:
            self.log.error(f"Error sending command '{action}': {e}")
            return {"status": "error", "message": str(e)}

    async def run_code(self, code: str, packages: Optional[List[str]] = None) -> Dict[str, str]:
        """
        Execute arbitrary Python code on the sandbox server.

        Args:
            code: The Python code to execute
            packages: Optional list of packages to install before execution

        Returns:
            A dictionary containing 'stdout' and 'stderr' from execution
        """
        # Remove common leading whitespace to prevent indentation errors
        code = textwrap.dedent(code)

        payload = {"code": code}
        if packages:
            payload["packages"] = packages

        return await self.send_command("EXECUTE_CODE", payload)

    async def run_pyautogui_code(self, code: str, packages: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Execute PyAutoGUI code on the sandbox server and automatically take a screenshot.

        Args:
            code: The PyAutoGUI code to execute
            packages: Optional list of packages to install before execution

        Returns:
            A dictionary containing execution results and screenshot path
        """
        # Remove common leading whitespace to prevent indentation errors
        code = textwrap.dedent(code)

        payload = {"code": code}
        if packages:
            payload["packages"] = packages

        return await self.send_command("EXECUTE_GUI_CODE", payload)

    async def get_screenshot_with_metadata(self) -> Dict[str, Any]:
        """
        Retrieve a screenshot along with mouse position and screen size.
        """
        try:
            # Use the SCREENSHOT action that server expects
            response = await self.send_command("SCREENSHOT")
            return response
        except Exception as e:
            self.log.error(f"Error retrieving screenshot with metadata: {e}")
            return {"status": "error", "message": str(e)}

    async def start_recording(self) -> Dict[str, Any]:
        """
        Start recording user actions.
        """
        # Changed from "RECORD" to "START_RECORDING" to match server expectations
        return await self.send_command("START_RECORDING")

    async def stop_recording(self) -> Dict[str, Any]:
        """
        Stop recording user actions and get the recorded actions.
        """
        # Changed from "RECORD" to "STOP_RECORDING" to match server expectations
        return await self.send_command("STOP_RECORDING")

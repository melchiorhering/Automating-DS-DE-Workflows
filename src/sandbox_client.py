import json
import logging
from typing import Any, Dict, List

import websockets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class SandboxClient:
    """
    A client to interact with the sandbox server using WebSocket.
    """

    def __init__(self, uri: str):
        """
        Initialize the client.

        Args:
            uri (str): The WebSocket server URI.
        """
        self.uri = uri
        self.websocket = None

    async def connect(self) -> None:
        """
        Connect to the WebSocket server.
        """
        try:
            self.websocket = await websockets.connect(self.uri)
            logging.info(f"Connected to WebSocket server at {self.uri}")
        except websockets.InvalidURI:
            logging.error(f"Invalid WebSocket URI: {self.uri}")
            raise
        except websockets.InvalidHandshake as e:
            logging.error(f"Failed WebSocket handshake with server: {e}")
            raise
        except Exception as e:
            logging.error(f"Failed to connect to WebSocket server: {e}")
            raise

    async def disconnect(self) -> None:
        """
        Disconnect from the WebSocket server.
        """
        if self.websocket:
            await self.websocket.close()
            logging.info("Disconnected from WebSocket server")

    async def send_command(self, command: str, args: List[Any]) -> Dict[str, Any]:
        if not self.websocket:
            raise RuntimeError("WebSocket connection is not established.")

        try:
            # Change "command" -> "action"
            payload = {"action": command, "args": args}
            await self.websocket.send(json.dumps(payload))
            logging.info(f"Sent: {payload}")

            response = await self.websocket.recv()
            logging.info(f"Received: {response}")
            return json.loads(response)
        except Exception as e:
            logging.error(f"Error sending command '{command}': {e}")
            return {"status": "error", "message": str(e)}

    async def get_screenshot_with_metadata(self) -> Dict[str, Any]:
        """
        Retrieve a screenshot along with mouse position and screen size.
        """
        try:
            # Now the server will receive action="SCREENSHOT"
            response = await self.send_command("SCREENSHOT", [])
            return response
        except Exception as e:
            logging.error(f"Error retrieving screenshot with metadata: {e}")
            return {"status": "error", "message": str(e)}

    async def start_recording(self) -> Dict[str, Any]:
        """
        Start recording user actions.

        Returns:
            Dict[str, Any]: The server's response.
        """
        return await self.send_command("RECORD", {"command": "start"})

    async def stop_recording(self) -> Dict[str, Any]:
        """
        Stop recording user actions and get the recorded actions.

        Returns:
            Dict[str, Any]: The server's response including recorded actions.
        """
        return await self.send_command("RECORD", {"command": "stop"})

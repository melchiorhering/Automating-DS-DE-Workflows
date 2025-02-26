import asyncio
import json
import logging

from websockets import connect

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

SERVER_URI = "ws://localhost:8765"

TEST_ACTIONS = [
    # Execute code without packages
    {"action": "EXECUTE_CODE", "code": "print('Hello, world from test with no packages')"},
    # Execute code with package installation
    {
        "action": "EXECUTE_CODE",
        "code": "import requests\nprint('Requests version:', requests.__version__)",
        "packages": ["requests"],
    },
    # Execute GUI code (PyAutoGUI)
    {
        "action": "EXECUTE_GUI_CODE",
        "code": "import pyautogui\npyautogui.moveTo(100, 200)\npyautogui.click(clicks=2)\nprint('PyAutoGUI test complete')",
        "packages": ["pyautogui"],
    },
    # Start recording
    {"action": "START_RECORDING"},
    # Stop recording
    {"action": "STOP_RECORDING"},
    # Get screenshot with metadata
    {"action": "SCREENSHOT"},
]


async def test_actions():
    async with connect(SERVER_URI) as websocket:
        logging.info("Connected to the sandbox server")
        test_results = {}

        for action in TEST_ACTIONS:
            try:
                logging.info(f"Sending action: {json.dumps(action)}")
                await websocket.send(json.dumps(action))

                response = await websocket.recv()
                response_data = json.loads(response)
                logging.info(f"Received response: {json.dumps(response_data, indent=2)}")

                # Store the response keyed by the action type
                test_results[action["action"]] = response_data
            except Exception as e:
                test_results[action["action"]] = {"status": "error", "message": str(e)}
                logging.error(f"Error testing action {action['action']}: {e}")

        logging.info("Test results:")
        print(json.dumps(test_results, indent=2))


if __name__ == "__main__":
    asyncio.run(test_actions())

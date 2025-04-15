import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import ImageGrab
from pynput import keyboard, mouse


@dataclass
class BaseAction:
    action_type: str
    description: str


@dataclass
class MouseAction(BaseAction):
    x: int
    y: int


@dataclass
class ClickAction(MouseAction):
    button: str


@dataclass
class DragAction(MouseAction):
    start_x: int
    start_y: int
    end_x: int
    end_y: int


@dataclass
class ScrollAction(MouseAction):
    delta_x: int
    delta_y: int


@dataclass
class KeyAction(BaseAction):
    key: str


class ActionRecorder:
    """Records user interactions including mouse and keyboard events."""

    def __init__(self, bounding_box_size: int = 100):
        self.logger = logging.getLogger(__name__)
        self.is_recording = False
        self.recorded_actions: List[Dict[str, Any]] = []
        self._bounding_box_size = max(50, min(bounding_box_size, 500))  # Constrain box size
        self._last_click_time = 0
        self._drag_start: Optional[Tuple[int, int]] = None

        # Get screen size using pynput instead of pyautogui
        self._mouse_controller = mouse.Controller()
        # Use a fallback method for screen size
        try:
            # Try using PIL to get screen size
            screen = ImageGrab.grab()
            self._screen_width, self._screen_height = screen.size
        except Exception:
            # Fallback values if screen size can't be determined
            self.logger.warning("Unable to get screen size, using default values")
            self._screen_width, self._screen_height = 1920, 1080

        # Initialize listeners but don't start them yet
        self._setup_listeners()

    def _setup_listeners(self) -> None:
        """Initialize mouse and keyboard listeners."""
        self._mouse_listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self._keyboard_listener = keyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release)

    def validate_actions(self) -> List[str]:
        """Validate recorded actions for potential issues."""
        issues = []

        for action in self.recorded_actions:
            data = action["data"]

            if action["action_type"] in ("CLICK", "DRAG"):
                x = data.get("x", 0)
                y = data.get("y", 0)
                if not self.validate_coordinates(x, y):
                    issues.append(
                        f"Warning: Coordinates ({x}, {y}) outside screen bounds "
                        f"({self._screen_width}x{self._screen_height})"
                    )

            if action["action_type"] == "DRAG":
                if not self.validate_coordinates(data["start_x"], data["start_y"]):
                    issues.append(
                        f"Warning: Drag start coordinates ({data['start_x']}, {data['start_y']}) outside screen bounds"
                    )

        return issues

    def validate_coordinates(self, x: int, y: int) -> bool:
        """Check if coordinates are within screen bounds."""
        return 0 <= x <= self._screen_width and 0 <= y <= self._screen_height

    def optimize_recording(self) -> None:
        """Combine similar consecutive actions to reduce playback time."""
        if not self.recorded_actions:
            return

        optimized = []
        current = self.recorded_actions[0]

        for next_action in self.recorded_actions[1:]:
            # Combine consecutive keypresses
            if (
                current["action_type"] == "KEY_PRESS"
                and next_action["action_type"] == "KEY_PRESS"
                and len(current["data"]["key"]) == 1
                and len(next_action["data"]["key"]) == 1
            ):
                current["data"]["key"] += next_action["data"]["key"]
                current["description"] = f"Keys pressed: {current['data']['key']}"
            # Combine consecutive scrolls in the same direction
            elif (
                current["action_type"] == "SCROLL"
                and next_action["action_type"] == "SCROLL"
                and abs(current["data"]["x"] - next_action["data"]["x"]) < 10
                and abs(current["data"]["y"] - next_action["data"]["y"]) < 10
            ):
                current["data"]["delta_y"] += next_action["data"]["delta_y"]
                current["description"] = f"Scrolled at ({current['data']['x']}, {current['data']['y']})"
            else:
                optimized.append(current)
                current = next_action

        optimized.append(current)
        self.recorded_actions = optimized

    def start_recording(self) -> None:
        """Start recording user interactions."""
        if self.is_recording:
            return

        try:
            self.is_recording = True
            self.recorded_actions = []
            self._mouse_listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
            self._keyboard_listener = keyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release)
            self._mouse_listener.start()
            self._keyboard_listener.start()
        except Exception as e:
            self.is_recording = False
            raise RuntimeError(f"Failed to start recording: {e}")

    def stop_recording(self) -> List[Dict[str, Any]]:
        """Stop recording and return the recorded actions."""
        if not self.is_recording:
            return self.recorded_actions

        self.is_recording = False
        try:
            if self._mouse_listener.is_alive():
                self._mouse_listener.stop()
            if self._keyboard_listener.is_alive():
                self._keyboard_listener.stop()
        except Exception as e:
            print(f"Error stopping listeners: {e}")

        return self.recorded_actions

    def _capture_bounding_box(self, x: int, y: int) -> Dict[str, Any]:
        """Capture a screenshot of the area around the given coordinates using PIL instead of pyautogui."""
        half_size = self._bounding_box_size // 2
        box_left = max(0, x - half_size)
        box_top = max(0, y - half_size)
        box_right = min(self._screen_width, box_left + self._bounding_box_size)
        box_bottom = min(self._screen_height, box_top + self._bounding_box_size)

        try:
            # Use PIL's ImageGrab instead of pyautogui
            screenshot = ImageGrab.grab(bbox=(box_left, box_top, box_right, box_bottom))
            return {
                "box_left": box_left,
                "box_top": box_top,
                "box_width": box_right - box_left,
                "box_height": box_bottom - box_top,
                "screenshot": screenshot,
            }
        except Exception as e:
            self.logger.error(f"Failed to capture bounding box: {e}")
            return {}

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        """Handle mouse click events."""
        if not self.is_recording:
            return

        # Debounce clicks
        current_time = time.time()
        if current_time - self._last_click_time < 0.1:
            return

        try:
            if pressed:
                self._drag_start = (x, y)
            else:
                if self._drag_start and (self._drag_start != (x, y)):
                    action = DragAction(
                        action_type="DRAG",
                        description=f"Mouse dragged from {self._drag_start} to ({x}, {y})",
                        start_x=self._drag_start[0],
                        start_y=self._drag_start[1],
                        end_x=x,
                        end_y=y,
                        x=x,
                        y=y,
                    )
                    self._add_action(action, include_box=True)
                    self._drag_start = None
                else:
                    action = ClickAction(
                        action_type="CLICK",
                        description=f"Mouse clicked at ({x}, {y})",
                        x=x,
                        y=y,
                        button=button.name.lower(),
                    )
                    self._add_action(action, include_box=True)

            self._last_click_time = current_time
        except Exception as e:
            self.logger.error(f"Error handling click event: {e}")

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        """Handle mouse scroll events."""
        if not self.is_recording:
            return

        try:
            action = ScrollAction(
                action_type="SCROLL", description=f"Scrolled at ({x}, {y})", x=x, y=y, delta_x=dx, delta_y=dy
            )
            self._add_action(action)
        except Exception as e:
            self.logger.error(f"Error handling scroll event: {e}")

    def _on_key_press(self, key) -> None:
        """Handle key press events."""
        if not self.is_recording:
            return

        try:
            key_char = key.char if hasattr(key, "char") else str(key).replace("Key.", "")
            action = KeyAction(action_type="KEY_PRESS", description=f"Key pressed: {key_char}", key=key_char)
            self._add_action(action)
        except AttributeError:
            pass
        except Exception as e:
            self.logger.error(f"Error handling key press event: {e}")

    def _on_key_release(self, key) -> None:
        """Handle key release events."""
        if not self.is_recording:
            return

        try:
            key_char = key.char if hasattr(key, "char") else str(key).replace("Key.", "")
            action = KeyAction(action_type="KEY_RELEASE", description=f"Key released: {key_char}", key=key_char)
            self._add_action(action)
        except AttributeError:
            pass
        except Exception as e:
            self.logger.error(f"Error handling key release event: {e}")

    def _add_action(self, action: BaseAction, include_box: bool = False) -> None:
        """Add an action to the recorded actions list with validation."""
        try:
            # Validate coordinates for mouse actions
            if isinstance(action, MouseAction):
                if not self.validate_coordinates(action.x, action.y):
                    self.logger.warning(f"Action coordinates ({action.x}, {action.y}) outside screen bounds")

            action_log = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action_type": action.action_type,
                "description": action.description,
                "data": vars(action),
            }

            if include_box and isinstance(action, MouseAction):
                box_data = self._capture_bounding_box(action.x, action.y)
                if box_data:  # Only include if capture was successful
                    action_log["bounding_box"] = box_data

            self.recorded_actions.append(action_log)
            self.logger.debug(f"Added action: {action.description}")

        except Exception as e:
            self.logger.error(f"Error adding action to log: {e}")
            raise

    def generate_pynput_script(
        self,
        output_file: Optional[Path] = None,
        screenshot_dir: Optional[Path] = None,
        action_delay: float = 0.5,
        screenshot_delay: float = 1.0,
    ) -> str:
        """
        Convert recorded actions to executable pynput commands with screenshots after each action.

        Args:
            output_file: Optional path to save the generated script
            screenshot_dir: Directory to save screenshots (defaults to /tmp/sandbox_screenshots)
            action_delay: Delay between actions
            screenshot_delay: Delay before taking screenshots

        Returns:
            String containing the generated Python script
        """
        script_lines = [
            "from pynput import mouse, keyboard",
            "from PIL import ImageGrab",
            "import time",
            "from datetime import datetime",
            "from pathlib import Path",
            "",
            "# Initialize controllers",
            "mouse_controller = mouse.Controller()",
            "keyboard_controller = keyboard.Controller()",
            "",
            "# Safety settings",
            f"ACTION_DELAY = {action_delay}  # Delay between actions",
            f"SCREENSHOT_DELAY = {screenshot_delay}  # Delay before screenshots",
            "",
            "# Setup screenshot directory",
            "SCREENSHOT_DIR = Path('/tmp/sandbox_screenshots')",
            "SCREENSHOT_DIR.mkdir(exist_ok=True)",
            "",
            "def take_action_screenshot(action_type: str) -> str:",
            "    '''Take a screenshot after an action and return the filepath'''",
            "    timestamp = datetime.now().strftime('%Y%m%dT%H%M%S')",
            "    filename = f'pynput_{action_type}_{timestamp}.png'",
            "    filepath = SCREENSHOT_DIR / filename",
            "    # Wait for any UI changes to settle",
            "    time.sleep(SCREENSHOT_DELAY)",
            "    screenshot = ImageGrab.grab()",
            "    screenshot.save(filepath)",
            "    return str(filepath)",
            "",
        ]

        for idx, action in enumerate(self.recorded_actions):
            action_type = action["action_type"]
            data = action["data"]

            # Add comment to separate actions
            script_lines.append(f"\n# Action {idx + 1}: {action['description']}")

            if action_type == "CLICK":
                script_lines.extend(
                    [
                        "# Move to position and click",
                        f"mouse_controller.position = ({data['x']}, {data['y']})",
                        "time.sleep(0.1)  # Short pause before click",
                        f"mouse_controller.click(mouse.Button.{data['button']})",
                        "screenshot_path = take_action_screenshot('click')",
                        "print(f'Click screenshot saved: {screenshot_path}')",
                    ]
                )

            elif action_type == "DRAG":
                script_lines.extend(
                    [
                        "# Drag operation from start to end",
                        f"mouse_controller.position = ({data['start_x']}, {data['start_y']})",
                        "time.sleep(0.1)",
                        "mouse_controller.press(mouse.Button.left)",
                        "time.sleep(0.1)",
                        "# Gradually move to target position",
                        "steps = 10",
                        "for i in range(1, steps + 1):",
                        "    progress = i / steps",
                        f"    current_x = int({data['start_x']} + ({data['end_x']} - {data['start_x']}) * progress)",
                        f"    current_y = int({data['start_y']} + ({data['end_y']} - {data['start_y']}) * progress)",
                        "    mouse_controller.position = (current_x, current_y)",
                        "    time.sleep(0.02)",
                        "mouse_controller.release(mouse.Button.left)",
                        "screenshot_path = take_action_screenshot('drag')",
                        "print(f'Drag screenshot saved: {screenshot_path}')",
                    ]
                )

            elif action_type == "SCROLL":
                script_lines.extend(
                    [
                        "# Move to position and scroll",
                        f"mouse_controller.position = ({data['x']}, {data['y']})",
                        "time.sleep(0.1)",
                        f"mouse_controller.scroll(0, {data['delta_y']})",
                        "screenshot_path = take_action_screenshot('scroll')",
                        "print(f'Scroll screenshot saved: {screenshot_path}')",
                    ]
                )

            elif action_type == "KEY_PRESS":
                key = data["key"]
                if len(key) == 1:  # Single character
                    script_lines.extend(
                        [
                            f"# Type character: '{key}'",
                            f"keyboard_controller.press('{key}')",
                            f"keyboard_controller.release('{key}')",
                            "screenshot_path = take_action_screenshot('key_press')",
                            "print(f'Keypress screenshot saved: {screenshot_path}')",
                        ]
                    )
                else:  # Special key
                    script_lines.extend(
                        [
                            f"# Press special key: {key}",
                            "try:",
                            "    # Try to use it as a special key constant",
                            f"    keyboard_controller.press(keyboard.Key.{key})",
                            f"    keyboard_controller.release(keyboard.Key.{key})",
                            "except AttributeError:",
                            "    # If not a special key, press it as a string",
                            f"    keyboard_controller.press('{key}')",
                            f"    keyboard_controller.release('{key}')",
                            "screenshot_path = take_action_screenshot('special_key')",
                            "print(f'Special key screenshot saved: {screenshot_path}')",
                        ]
                    )

            # Add a delay between actions
            script_lines.append("time.sleep(ACTION_DELAY)")

        script = "\n".join(script_lines)

        if output_file:
            output_file.write_text(script)

        return script

    def save_recording(self, filepath: Path) -> None:
        """
        Save the recorded actions to a JSON file.

        Args:
            filepath: Path where to save the recording
        """
        try:
            # Convert any non-serializable data (like screenshots) to strings
            serializable_actions = []
            for action in self.recorded_actions:
                action_copy = action.copy()
                if "bounding_box" in action_copy:
                    if "screenshot" in action_copy["bounding_box"]:
                        # Remove the PIL Image object as it's not JSON serializable
                        del action_copy["bounding_box"]["screenshot"]
                serializable_actions.append(action_copy)

            with open(filepath, "w") as f:
                json.dump(
                    {"recorded_at": datetime.now(timezone.utc).isoformat(), "actions": serializable_actions},
                    f,
                    indent=2,
                )
        except Exception as e:
            self.logger.error(f"Error saving recording: {e}")

    def load_recording(self, filepath: Path) -> None:
        """
        Load recorded actions from a JSON file.

        Args:
            filepath: Path to the recording file
        """
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
                self.recorded_actions = data["actions"]
        except Exception as e:
            self.logger.error(f"Error loading recording: {e}")

    def __enter__(self):
        self.start_recording()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_recording()

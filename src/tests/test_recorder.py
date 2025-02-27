import time
import unittest

from controllers.recorder import ActionRecorder


# Dummy objects for simulation
class DummyButton:
    name = "left"


class DummyKey:
    # Simulate key event with both 'char' and 'name'
    def __init__(self, char=None, name=None):
        self.char = char
        self.name = name or char


class TestActionRecorder(unittest.TestCase):
    def setUp(self):
        self.recorder = ActionRecorder(bounding_box_size=50)
        self.recorder.start_recording()
        # Ensure _last_click_time is in the past to avoid debounce issues.
        self.recorder._last_click_time = time.time() - 1

    def tearDown(self):
        self.recorder.stop_recording()

    def test_click_recording(self):
        # Simulate press and release at the same coordinates (regular click)
        x, y = 100, 150
        dummy_button = DummyButton()
        self.recorder._on_click(x, y, dummy_button, pressed=True)
        self.recorder._on_click(x, y, dummy_button, pressed=False)
        self.assertTrue(
            any(action["action_type"] == "CLICK" for action in self.recorder.recorded_actions),
            "Click action should be recorded.",
        )

    def test_key_recording(self):
        # Simulate key press and release for key 'a'
        dummy_key = DummyKey(char="a")
        self.recorder._on_key_press(dummy_key)
        self.recorder._on_key_release(dummy_key)
        key_actions = [
            action for action in self.recorder.recorded_actions if action["action_type"] in ["KEY_DOWN", "KEY_UP"]
        ]
        self.assertEqual(len(key_actions), 2, "Both key down and key up should be recorded.")

    def test_scroll_recording(self):
        # Simulate a scroll event at a given location
        x, y, dx, dy = 100, 150, 0, -1
        self.recorder._on_scroll(x, y, dx, dy)
        self.assertTrue(
            any(action["action_type"] == "SCROLL" for action in self.recorder.recorded_actions),
            "Scroll action should be recorded.",
        )


if __name__ == "__main__":
    unittest.main()

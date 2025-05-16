import threading
from datetime import datetime, timezone
from time import time

from pynput import keyboard, mouse

from src.utils import flush_typing_sequence

recording = False
recorded_actions = []
_mouse_listener = None
_keyboard_listener = None
_typing_buffer = []  # Stores (char, timestamp)
_typing_timeout = 1.5
_last_key_time = None


def _record_user_actions():
    global _mouse_listener, _keyboard_listener

    def on_click(x, y, button, pressed):
        if recording:
            action = {
                "event": "click",
                "x": x,
                "y": y,
                "button": str(button),
                "pressed": pressed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            recorded_actions.append(action)

    def on_press(key):
        global _last_key_time, _typing_buffer

        now = time()
        try:
            char = key.char
        except AttributeError:
            char = str(key)

        # Flush old typing buffer if user paused typing
        if _last_key_time is None or (now - _last_key_time) > _typing_timeout:
            flush_typing_sequence(recorded_actions, _typing_buffer)

        if len(char) == 1:
            _typing_buffer.append((char, datetime.now(timezone.utc)))
        else:
            flush_typing_sequence(recorded_actions, _typing_buffer)
            recorded_actions.append(
                {
                    "event": "hotkey",
                    "key": char,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        _last_key_time = now

    _mouse_listener = mouse.Listener(on_click=on_click)
    _keyboard_listener = keyboard.Listener(on_press=on_press)
    _mouse_listener.start()
    _keyboard_listener.start()


def start_recording():
    global recording, _recording_thread
    recording = True
    recorded_actions.clear()
    _recording_thread = threading.Thread(target=_record_user_actions, daemon=True)
    _recording_thread.start()


def stop_recording():
    global recording, _mouse_listener, _keyboard_listener
    recording = False

    if _mouse_listener:
        _mouse_listener.stop()
        _mouse_listener = None

    if _keyboard_listener:
        _keyboard_listener.stop()
        _keyboard_listener = None

    flush_typing_sequence(recorded_actions, _typing_buffer)

    return recorded_actions.copy()

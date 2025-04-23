# server/src/utils.py
from datetime import datetime, timezone


def flush_typing_sequence(recorded_actions, buffer):
    if not buffer:
        return

    combined = "".join(char for char, _ in buffer)
    timestamp = buffer[-1][1] if buffer else datetime.now(timezone.utc)

    recorded_actions.append(
        {
            "event": "typed_sequence",
            "text": combined,
            "timestamp": timestamp.isoformat(),
        }
    )
    buffer.clear()

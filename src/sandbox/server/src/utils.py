# server/src/utils.py
import textwrap
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


def normalize_code(code: str) -> str:
    """Clean and dedent code string, preserving indentation inside functions etc."""
    code = code.strip()
    return textwrap.dedent(code)

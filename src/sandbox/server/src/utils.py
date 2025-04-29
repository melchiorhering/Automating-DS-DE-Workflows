# server/src/utils.py
import ast
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
    """Clean, dedent, and validate a Python code string.

    Args:
        code: The Python code string to normalize.

    Returns:
        A cleaned and validated Python code string.

    Raises:
        SyntaxError: If the code contains invalid Python syntax.
    """
    # Step 1: Strip and dedent the code
    code = code.strip()
    code = textwrap.dedent(code)

    # Step 2: Validate syntax using the `ast` module
    try:
        ast.parse(code)
    except SyntaxError as e:
        raise SyntaxError(f"Invalid Python syntax: {e.msg} at line {e.lineno}") from e

    # # Step 3: Ensure imports for common libraries (optional)
    # required_imports = ["import pyautogui"]
    # for imp in required_imports:
    #     if imp not in code:
    #         code = f"{imp}\n{code}"

    return code

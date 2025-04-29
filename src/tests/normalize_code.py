import sys
from pathlib import Path

# Add the parent of `sandbox/` (i.e., ./src/) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox.server.src.utils import normalize_code


def run_tests():
    print("Running tests for normalize_code...\n")

    # Test 1: Valid code
    try:
        code = """
def greet():
    print("Hello, World!")
greet()
"""
        print("Test 1: Valid code")
        result = normalize_code(code)
        print("Result:\n", result)
        print("-" * 50)
    except Exception as e:
        print("Test 1 Failed:", e)

    # Test 2: Missing import
    try:
        code = """
def move_mouse():
    pyautogui.moveTo(100, 100)
move_mouse()
"""
        print("Test 2: Missing import")
        result = normalize_code(code)
        print("Result:\n", result)
        print("-" * 50)
    except Exception as e:
        print("Test 2 Failed:", e)

    # Test 3: Invalid syntax
    try:
        code = """
def invalid_code(
    print("This is invalid")
"""
        print("Test 3: Invalid syntax")
        result = normalize_code(code)
        print("Result:\n", result)
        print("-" * 50)
    except SyntaxError as e:
        print("Test 3 Passed: Caught SyntaxError:", e)
    except Exception as e:
        print("Test 3 Failed:", e)

    # Test 4: Ruff formatting
    try:
        code = """
def greet(): print("Hello, World!")
greet()
"""
        print("Test 4: Ruff formatting")
        result = normalize_code(code)
        print("Result:\n", result)
        print("-" * 50)
    except Exception as e:
        print("Test 4 Failed:", e)

    # Test 5: Ruff failure simulation (requires manual mocking)
    try:
        print("Test 5: Ruff failure simulation (skipped, requires mocking)")
        print("-" * 50)
    except Exception as e:
        print("Test 5 Failed:", e)


if __name__ == "__main__":
    run_tests()

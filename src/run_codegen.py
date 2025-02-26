import os
import platform
import subprocess
import sys
import webbrowser
from datetime import datetime
from typing import Literal, Optional


def get_os_default_browser() -> Optional[Literal["firefox", "chromium", "webkit"]]:
    """Retrieves the system's default browser using OS-specific methods."""
    system_name = platform.system()

    if system_name == "Darwin":  # macOS
        result = subprocess.run(
            ["defaults", "read", "com.apple.LaunchServices/com.apple.launchservices.secure", "LSHandlers"],
            capture_output=True,
            text=True,
        )
        if "firefox" in result.stdout.lower():
            return "firefox"
        elif "chrome" in result.stdout.lower():
            return "chromium"
        elif "safari" in result.stdout.lower():
            return "webkit"

    elif system_name == "Windows":
        result = subprocess.run(
            [
                "reg",
                "query",
                "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\http\\UserChoice",
            ],
            capture_output=True,
            text=True,
        )
        if "firefox" in result.stdout.lower():
            return "firefox"
        elif "chrome" in result.stdout.lower():
            return "chromium"
        elif "safari" in result.stdout.lower():
            return "webkit"

    elif system_name == "Linux":
        result = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True)
        if "firefox" in result.stdout.lower():
            return "firefox"
        elif "chrome" in result.stdout.lower() or "chromium" in result.stdout.lower():
            return "chromium"
        elif "epiphany" in result.stdout.lower():
            return "webkit"

    # If we reach here, fallback to Python's webbrowser module
    browser_path = webbrowser.get().name.lower()
    if "firefox" in browser_path:
        return "firefox"
    elif "chrome" in browser_path or "chromium" in browser_path:
        return "chromium"
    elif "safari" in browser_path:
        return "webkit"

    return None  # No match found


def get_playwright_browser() -> Optional[Literal["chromium", "firefox", "webkit"]]:
    """Checks for installed Playwright browsers and returns the best available one."""
    try:
        result = subprocess.run(["playwright", "install", "--dry-run"], capture_output=True, text=True, check=True)
        installed_browsers = result.stdout.lower()

        # Prioritize Playwright browsers in this order
        if "chromium" in installed_browsers:
            return "chromium"
        elif "firefox" in installed_browsers:
            return "firefox"
        elif "webkit" in installed_browsers:
            return "webkit"

    except subprocess.CalledProcessError:
        print("❌ Could not determine Playwright installed browsers.")

    return None  # No Playwright browser found


def get_best_browser() -> Literal["chromium", "firefox", "webkit"]:
    """Decides the best browser to use based on Playwright and OS defaults."""
    playwright_browser = get_playwright_browser()
    if playwright_browser:
        return playwright_browser

    os_browser = get_os_default_browser()
    if os_browser:
        print(f"ℹ️ Using OS default browser: {os_browser}")
        return os_browser

    print("❌ No suitable browser found. Please install Playwright browsers with:")
    print("   playwright install chromium firefox webkit")
    sys.exit(1)


# Ensure a test name is provided
if len(sys.argv) < 2:
    print("Usage: python run_codegen.py <test_name> [browser] [start_url]")
    sys.exit(1)

# Get test name from command-line argument
test_name = sys.argv[1]

# Check if a browser is provided; otherwise, determine the best available browser
browser = sys.argv[2] if len(sys.argv) > 2 else get_best_browser()

# Check if a start URL is provided
start_url = sys.argv[3] if len(sys.argv) > 3 else None

# Define structured directory format
date_dir = datetime.now().strftime("%Y-%m-%d")  # YYYY-MM-DD format
timestamp = datetime.now().strftime("%H%M%S")  # HH-MM-SS format
output_dir = os.path.join("codegen_tests", date_dir)
output_file = os.path.join(output_dir, f"{timestamp}_{test_name}.py")

# Ensure the output directory exists
os.makedirs(output_dir, exist_ok=True)

# Prepare Playwright command
cmd = ["playwright", "codegen", "--target=python", "--browser", browser, "--output", output_file]

# Add start URL if provided
if start_url:
    cmd.append(start_url)

# Run Playwright Codegen with the selected browser and store output
subprocess.run(cmd)

print(f"✅ Playwright script saved to: {output_file} (Browser: {browser})")
if start_url:
    print(f"🌍 Started with URL: {start_url}")

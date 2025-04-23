# server/main.py
import asyncio
import contextlib
import datetime
import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

import numpy as np
import pyautogui
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont, ImageGrab
from pydantic import BaseModel

from src.pyxcursor import Xcursor
from src.recording import recorded_actions, start_recording, stop_recording
from src.utils import normalize_code

# ───────────────────── Logger Setup ─────────────────────
log_path = os.path.join(os.getenv("SHARED_DIR", "/tmp/sandbox-server"), os.getenv("SERVER_LOG", "sandbox-server.log"))
os.makedirs(os.path.dirname(log_path), exist_ok=True)

logger = logging.getLogger("SandboxServer")
logger.setLevel(logging.DEBUG if os.getenv("DEBUG") == "1" else logging.INFO)

file_handler = logging.FileHandler(log_path)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler()
logger.addHandler(stream_handler)

logger.info("🔧 FastAPI Server logging to: %s", log_path)


# ───────────────────── Models ─────────────────────
class CodeRequest(BaseModel):
    code: str
    packages: Optional[List[str]] = None


# ───────────────────── FastAPI Setup ─────────────────────
app = FastAPI(
    title="Sandbox REST Server",
    description="API for executing code, taking screenshots, and recording user GUI actions in a sandboxed VM environment.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

shared_dir = os.getenv("SHARED_DIR", "/tmp/sandbox-server")
os.makedirs(shared_dir, exist_ok=True)

# ───────────────────── Cursor & Screen ─────────────────────
try:
    cursor = Xcursor()
    logger.info("✅ Cursor image saved successfully")
except Exception as e:
    logger.warning(f"⚠️ Failed to initialize Xcursor: {e}")
    cursor = None

try:
    screen_width, screen_height = pyautogui.size()
    logger.info(f"🖥️ Screen size: {screen_width}x{screen_height}")
except Exception as e:
    logger.warning(f"⚠️ pyautogui fallback: {e}")
    screen_width, screen_height = 1920, 1080


# ───────────────────── Utilities ─────────────────────
async def install_packages(packages: List[str]) -> Optional[str]:
    try:
        result = await asyncio.create_subprocess_exec(
            "uv", "add", *packages, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await result.communicate()
        if result.returncode == 0:
            return None

        logger.warning("⚠️ 'uv add' failed, falling back to 'uv pip install'")
        result = await asyncio.create_subprocess_exec(
            "uv", "pip", "install", *packages, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await result.communicate()
        if result.returncode == 0:
            return None

        return f"❌ Failed to install: {', '.join(packages)}\n{stderr.decode().strip()}"
    except Exception as e:
        return f"❌ Package install error: {str(e)}"


async def execute_code(code: str, packages: Optional[List[str]] = None) -> Dict[str, str]:
    if packages:
        error = await install_packages(packages)
        if error:
            return {"stdout": "", "stderr": error}

    output_buffer, error_buffer = io.StringIO(), io.StringIO()

    def run():
        try:
            with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(error_buffer):
                exec(code, {})
        except Exception as e:
            error_buffer.write(str(e))

    await asyncio.to_thread(run)

    return {
        "stdout": output_buffer.getvalue(),
        "stderr": error_buffer.getvalue(),
    }


def take_screenshot(method: Literal["pyautogui", "pillow"] = "pyautogui") -> Dict[str, str]:
    try:
        screenshot_dir = os.path.join(shared_dir, "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        filename = f"{method}-{timestamp}.png"
        filepath = os.path.join(screenshot_dir, filename)

        if method == "pyautogui":
            pyautogui.screenshot(imageFilename=filepath)
            img = Image.open(filepath)
        elif method == "pillow":
            img = ImageGrab.grab()
        else:
            raise ValueError(f"Unknown screenshot method: {method}")

        arr = np.array(img)
        screenshot_img = Image.fromarray(arr)
        draw = ImageDraw.Draw(screenshot_img)

        mouse_x, mouse_y = pyautogui.position()
        draw.rectangle([mouse_x - 25, mouse_y - 25, mouse_x + 25, mouse_y + 25], outline="red", width=2)
        draw.text(
            (mouse_x - 25, mouse_y - 35),
            f"mouse: mouse_x_{mouse_x}, mouse_y_{mouse_y}",
            fill="red",
            font=ImageFont.load_default(),
        )

        if cursor:
            try:
                cursor_arr = cursor.getCursorImageArrayFast()
                if cursor_arr is not None:
                    cursor_img = Image.fromarray(cursor_arr)
                    screenshot_img.paste(
                        cursor_img, (mouse_x - cursor_img.width // 2, mouse_y - cursor_img.height // 2), cursor_img
                    )
            except Exception as e:
                logger.warning(f"⚠️ Cursor overlay failed: {e}")

        screenshot_img.save(filepath)

        return {
            "screenshot_path": filename,
            "mouse_position": [mouse_x, mouse_y],
            "screen_size": [screen_width, screen_height],
        }

    except Exception as e:
        logger.error(f"❌ Screenshot error: {e}")
        return {"status": "error", "message": str(e)}


async def list_installed_packages() -> List[Dict[str, str]]:
    try:
        process = await asyncio.create_subprocess_exec(
            "uv",
            "pip",
            "list",
            "--format",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error(f"❌ Failed to list packages: {stderr.decode().strip()}")
            return []

        packages = stdout.decode("utf-8").strip()
        return json.loads(packages)
    except Exception as e:
        logger.error(f"❌ Error while listing packages: {e}")
        return []


# ───────────────────── API Endpoints ─────────────────────
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/packages")
async def get_installed_packages():
    return await list_installed_packages()


@app.get("/screenshot")
async def screenshot_endpoint(method: str = Query(default="pyautogui", enum=["pyautogui", "pillow"])):
    return take_screenshot(method=method)


@app.post("/execute")
async def run_code(request: CodeRequest):
    return await execute_code(request.code, request.packages)


@app.post("/execute_gui")
async def run_gui_code(request: CodeRequest):
    cleaned_code = normalize_code(request.code)

    if "import pyautogui" not in cleaned_code:
        cleaned_code = "import pyautogui\n" + cleaned_code

    result = await execute_code(cleaned_code, request.packages)

    if not result["stderr"]:
        result["screenshot"] = take_screenshot()

    return result


@app.get("/record")
async def record(mode: Literal["start", "stop"]):
    recordings_dir = os.path.join(shared_dir, "recordings")
    os.makedirs(recordings_dir, exist_ok=True)

    if mode == "start":
        if recorded_actions:
            return {"status": "already_recording"}
        start_recording()
        return {"status": "recording_started"}

    elif mode == "stop":
        if not recorded_actions:
            return {"status": "not_recording"}

        actions = stop_recording()

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        filename = f"recording-{timestamp}.json"
        filepath = os.path.join(recordings_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(actions, f, indent=2)
        except Exception as e:
            logger.error(f"❌ Failed to save recording: {e}")
            return {"status": "error", "message": str(e)}

        return {
            "status": "recording_stopped",
            "recording_file": filename,
            "num_actions": len(actions),
        }

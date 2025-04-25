# server/main.py
import asyncio
import contextlib
import datetime
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
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
# Logger setup
log_path = os.path.join(os.getenv("SHARED_DIR", "/tmp/sandbox-server"), os.getenv("SERVER_LOG", "sandbox-server.log"))
os.makedirs(os.path.dirname(log_path), exist_ok=True)

logger = logging.getLogger("SandboxServer")
logger.setLevel(logging.DEBUG if os.getenv("DEBUG") == "1" else logging.INFO)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

file_handler = logging.FileHandler(log_path)
file_handler.setFormatter(formatter)

# stream_handler = logging.StreamHandler()
# stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
# logger.addHandler(stream_handler)

logger.info(f"🔧 FastAPI Server logging to: {log_path}")


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

shared_dir = Path(os.getenv("SHARED_DIR", "/tmp/sandbox-server"))
shared_dir.mkdir(parents=True, exist_ok=True)

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
                # Always import pyautogui
                code_with_imports = "import pyautogui\n" + normalize_code(code)
                exec(code_with_imports, {"__builtins__": __builtins__}, {})

        except Exception as e:
            error_buffer.write(str(e))

    await asyncio.to_thread(run)

    return {
        "stdout": output_buffer.getvalue(),
        "stderr": error_buffer.getvalue(),
    }


def take_screenshot(method: Literal["pyautogui", "pillow"] = "pyautogui") -> Dict[str, str]:
    try:
        screenshot_dir = shared_dir / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        filename = f"{method}-{timestamp}.png"
        filepath = screenshot_dir / filename

        if method == "pyautogui":
            pyautogui.screenshot(imageFilename=str(filepath))
            img = Image.open(filepath)
        elif method == "pillow":
            img = ImageGrab.grab()
        else:
            raise ValueError(f"Unknown screenshot method: {method}")

        arr = np.array(img)
        screenshot_img = Image.fromarray(arr)
        draw = ImageDraw.Draw(screenshot_img)

        mouse_x, mouse_y = pyautogui.position()

        # Box and text settings
        box_half_size = 20
        text_padding = 5
        font = ImageFont.load_default()

        # Apply a small cursor offset (adjust if needed)
        cursor_offset_x = -2
        cursor_offset_y = -3

        adjusted_x = mouse_x + cursor_offset_x
        adjusted_y = mouse_y + cursor_offset_y

        # Draw red box around cursor
        draw.rectangle(
            [
                adjusted_x - box_half_size,
                adjusted_y - box_half_size,
                adjusted_x + box_half_size,
                adjusted_y + box_half_size,
            ],
            outline="red",
            width=2,
        )

        # Text content and size
        text = f"mouse: x={mouse_x} y={mouse_y}"
        bbox = font.getbbox(text)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Centered above the box
        text_x = adjusted_x - text_width // 2
        text_y = adjusted_y - box_half_size - text_height - text_padding

        draw.text((text_x, text_y), text, fill="red", font=font)

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


@app.get("/record")
async def record(mode: Literal["start", "stop"]):
    recordings_dir = shared_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

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
        filepath = recordings_dir / filename

        try:
            filepath.write_text(json.dumps(actions, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"❌ Failed to save recording: {e}")
            return {"status": "error", "message": str(e)}

        return {
            "status": "recording_stopped",
            "recording_file": filename,
            "num_actions": len(actions),
        }

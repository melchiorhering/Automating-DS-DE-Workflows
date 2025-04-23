import asyncio
import contextlib
import io
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pyautogui
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont, ImageGrab
from pydantic import BaseModel

from src.pyxcursor import Xcursor

# ───────────────────── Logger Setup ─────────────────────
log_path = os.path.join(os.getenv("SHARED_DIR", "/tmp/sandbox-server"), os.getenv("SERVER_LOG", "sandbox-server.log"))
os.makedirs(os.path.dirname(log_path), exist_ok=True)

logger = logging.getLogger("SandboxServer")
logger.setLevel(logging.DEBUG if os.getenv("DEBUG") == "1" else logging.INFO)

file_handler = logging.FileHandler(log_path)
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(stream_handler)

logger.info("🔧 FastAPI Server logging to: %s", log_path)


# ───────────────────── Models ─────────────────────
class CodeRequest(BaseModel):
    code: str
    packages: Optional[List[str]] = None
    client_id: Optional[str] = "default"


class ScreenshotRequest(BaseModel):
    client_id: Optional[str] = "default"


# ───────────────────── Server Setup ─────────────────────
app = FastAPI(title="Sandbox REST Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

shared_dir = os.getenv("SHARED_DIR", "/tmp/sandbox-server")
os.makedirs(shared_dir, exist_ok=True)

try:
    cursor = Xcursor()
    logger.info("✅ Xcursor initialized successfully")
except Exception as e:
    logger.warning(f"⚠️ Failed to initialize Xcursor: {e}")
    cursor = None

try:
    screen_width, screen_height = pyautogui.size()
    logger.info(f"🖥️ Screen size: {screen_width}x{screen_height}")
except Exception as e:
    logger.warning(f"⚠️ pyautogui fallback: {e}")
    screen_width, screen_height = 1920, 1080


# ───────────────────── Helpers ─────────────────────
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


def take_screenshot(client_id: str = "default") -> Dict[str, str]:
    try:
        client_dir = os.path.join(shared_dir, client_id)
        os.makedirs(client_dir, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        filename = f"pyautogui-{timestamp}.png"
        filepath = os.path.join(client_dir, filename)

        img = ImageGrab.grab()
        arr = np.array(img)
        screenshot_img = Image.fromarray(arr)
        draw = ImageDraw.Draw(screenshot_img)

        mouse_x, mouse_y = pyautogui.position()
        draw.rectangle([mouse_x - 25, mouse_y - 25, mouse_x + 25, mouse_y + 25], outline="red", width=2)
        draw.text((mouse_x - 25, mouse_y - 45), "mouse", fill="red", font=ImageFont.load_default())

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
            "screenshot_path": os.path.join(client_id, filename),
            "mouse_position": [mouse_x, mouse_y],
            "screen_size": [screen_width, screen_height],
        }
    except Exception as e:
        logger.error(f"❌ Screenshot error: {e}")
        return {"status": "error", "message": str(e)}


# ───────────────────── API Endpoints ─────────────────────
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/execute")
async def run_code(request: CodeRequest):
    return await execute_code(request.code, request.packages)


@app.post("/execute_gui")
async def run_gui_code(request: CodeRequest):
    code = request.code
    if "import pyautogui" not in code:
        code = "import pyautogui\n" + code
    result = await execute_code(code, request.packages)
    if not result["stderr"]:
        result["screenshot"] = take_screenshot(request.client_id)
    return result


@app.post("/screenshot")
async def screenshot_endpoint(request: ScreenshotRequest):
    return take_screenshot(request.client_id)

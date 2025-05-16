# server/main.py
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Literal

import numpy as np
import pyautogui
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont, ImageGrab

from src.pyxcursor import Xcursor
from src.recording import recorded_actions, start_recording, stop_recording

# ───────────────────── Logger Setup ─────────────────────
log_path = os.path.join(
    os.getenv("SHARED_DIR", "/tmp/sandbox-server"), os.getenv("SERVICES_LOG", "services-server.log")
)
os.makedirs(os.path.dirname(log_path), exist_ok=True)

logger = logging.getLogger("SandboxServer")
logger.setLevel(logging.DEBUG if os.getenv("DEBUG") == "1" else logging.INFO)
# formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler = logging.FileHandler(log_path)
# file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.info(f"🔧 FastAPI Server logging to: {log_path}")


# ───────────────────── Lifespan Event Setup ─────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    host = os.getenv(
        "HOST", "localhost"
    )  # This is for inside the container but since we are forwarding we use localhost
    port = os.getenv("PORT", "8000")
    logger.info(f"🛠️ FastAPI server is starting up on http://localhost:{port}")
    yield
    logger.info("🧹 FastAPI server is shutting down...")


# ───────────────────── FastAPI Setup ─────────────────────
app = FastAPI(
    title="Sandbox REST Server",
    description="API for screenshots and recording GUI actions in a sandboxed VM.",
    lifespan=lifespan,  # ✅ Modern event hook
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

# ───────────────────── Jupyter Kernel Config ─────────────────────
kernel_host = os.getenv("JUPYTER_KERNEL_GATEWAY_APP_HOST", "0.0.0.0")
kernel_port = os.getenv("JUPYTER_KERNEL_GATEWAY_APP_PORT", 8888)
kernel_url = f"http://{kernel_host}:{kernel_port}"

# ───────────────────── Cursor & Screen Info ─────────────────────
try:
    cursor = Xcursor()
    logger.info("✅ Cursor initialized")
except Exception as e:
    logger.warning(f"⚠️ Failed to initialize Xcursor: {e}")
    cursor = None

try:
    screen_width, screen_height = pyautogui.size()
    logger.info(f"🖥️ Screen size: {screen_width}x{screen_height}")
except Exception as e:
    logger.warning(f"⚠️ pyautogui fallback: {e}")
    screen_width, screen_height = 1920, 1080


# ───────────────────── Screenshot Utility ─────────────────────
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
        cursor_offset_x, cursor_offset_y = -2, -3
        adjusted_x = mouse_x + cursor_offset_x
        adjusted_y = mouse_y + cursor_offset_y

        # Draw red box around cursor
        box_half_size = 20
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

        # Draw position text
        font = ImageFont.load_default()
        text = f"mouse: x={mouse_x} y={mouse_y}"
        bbox = font.getbbox(text)
        text_x = adjusted_x - (bbox[2] - bbox[0]) // 2
        text_y = adjusted_y - box_half_size - (bbox[3] - bbox[1]) - 5
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
            "screenshot_path": str(filepath.relative_to(shared_dir)),
            "mouse_position": [mouse_x, mouse_y],
            "screen_size": [screen_width, screen_height],
        }

    except Exception as e:
        logger.error(f"❌ Screenshot error: {e}")
        return {"status": "error", "message": str(e)}


# ───────────────────── API Endpoints ─────────────────────
@app.get("/health")
def health_check():
    try:
        r = requests.get(f"{kernel_url}/api", timeout=2)
        if r.status_code != 200:
            raise RuntimeError("Kernel /api not ready")

        return {"status": "ok", "kernel_status": "reachable"}
    except Exception as e:
        logger.error(f"❌ Kernel healthcheck failed: {e}")
        return {"status": "error", "kernel_status": "unreachable", "error": str(e)}


@app.get("/screenshot")
async def screenshot_endpoint(method: str = Query(default="pyautogui", enum=["pyautogui", "pillow"])):
    return take_screenshot(method=method)


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
        filename = f"recording-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S')}.json"
        filepath = recordings_dir / filename

        try:
            filepath.write_text(json.dumps(actions, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"❌ Failed to save recording: {e}")
            return {"status": "error", "message": str(e)}

        return {
            "status": "recording_stopped",
            "recording_file": str(filepath.relative_to(shared_dir)),
            "num_actions": len(actions),
        }

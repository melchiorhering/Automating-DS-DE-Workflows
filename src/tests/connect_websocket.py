import asyncio
import base64
import ssl

import websockets


async def connect_to_kasmvnc():
    uri = "wss://localhost:6901/websockify"
    username = "kasm_user"
    password = "password"

    auth_header = f"Basic {base64.b64encode(f'{username}:{password}'.encode()).decode()}"

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    subprotocols = ["binary"]

    try:
        async with websockets.connect(
            uri,
            ssl=ssl_context,
            additional_headers={
                "Authorization": auth_header,
                "Origin": "https://localhost:6901",
            },
            subprotocols=subprotocols,
        ) as websocket:
            print("WebSocket connection to NoVNC opened")

            # Get screen size
            width, height = await get_screen_size(websocket)
            print(f"Screen size: {width}x{height}")

            # Move the mouse to the center of the screen
            center_x, center_y = width // 2, height // 2
            await move_mouse(websocket, center_x, center_y)

    except Exception as e:
        print(f"Error: {e}")


async def move_mouse(websocket, x, y):
    message = bytearray([5, 0]) + x.to_bytes(2, "big") + y.to_bytes(2, "big")
    await websocket.send(message)
    print(f"Moved mouse to ({x}, {y})")


async def get_screen_size(websocket):
    set_pixel_format = bytearray([0]) + (0).to_bytes(3, "big") + bytearray([32, 24, 0, 1, 255, 255, 255, 16, 8, 0, 0])
    await websocket.send(set_pixel_format)

    framebuffer_request = (
        bytearray([3, 0, 0, 0])
        + (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + (65535).to_bytes(2, "big")
        + (65535).to_bytes(2, "big")
    )
    await websocket.send(framebuffer_request)

    response = await websocket.recv()

    width = int.from_bytes(response[4:6], "big")
    height = int.from_bytes(response[6:8], "big")
    print(f"Screen size: {width}x{height}")
    return width, height


if __name__ == "__main__":
    asyncio.run(connect_to_kasmvnc())

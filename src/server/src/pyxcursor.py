import array
import ctypes
import ctypes.util
import os
import struct

import numpy as np
from PIL import Image
from pynput import mouse

# Define ctypes version of XFixesCursorImage structure
PIXEL_DATA_PTR = ctypes.POINTER(ctypes.c_ulong)
Atom = ctypes.c_ulong


class XFixesCursorImage(ctypes.Structure):
    """
    XFixesCursorImage structure from X11/extensions/Xfixes.h
    """

    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
        ("xhot", ctypes.c_ushort),
        ("yhot", ctypes.c_ushort),
        ("cursor_serial", ctypes.c_ulong),
        ("pixels", PIXEL_DATA_PTR),
        ("atom", Atom),
        ("name", ctypes.c_char_p),
    ]


class Display(ctypes.Structure):
    pass


class Xcursor:
    display = None

    def __init__(self):
        # Try to get the DISPLAY environment variable
        try:
            display = os.environ.get("DISPLAY", ":0").encode("utf-8")
        except KeyError:
            raise Exception("$DISPLAY not set.")

        # Try to get the XAUTHORITY environment variable
        self.xauth_path = os.environ.get("XAUTHORITY", os.path.expanduser("~/.Xauthority"))

        # For Wayland/XWayland, check for Mutter auth file
        mutter_path = "/run/user/{uid}/.mutter-Xwaylandauth.*".format(uid=os.getuid())
        import glob

        mutter_files = glob.glob(mutter_path)
        if mutter_files:
            self.xauth_path = mutter_files[0]
            os.environ["XAUTHORITY"] = self.xauth_path

        # Load required libraries
        XFixes = ctypes.util.find_library("Xfixes")
        if not XFixes:
            raise Exception("No XFixes library found.")
        self.XFixeslib = ctypes.cdll.LoadLibrary(XFixes)

        x11 = ctypes.util.find_library("X11")
        if not x11:
            raise Exception("No X11 library found.")
        self.xlib = ctypes.cdll.LoadLibrary(x11)

        # Define functions
        XFixesGetCursorImage = self.XFixeslib.XFixesGetCursorImage
        XFixesGetCursorImage.restype = ctypes.POINTER(XFixesCursorImage)
        XFixesGetCursorImage.argtypes = [ctypes.POINTER(Display)]
        self.XFixesGetCursorImage = XFixesGetCursorImage

        XOpenDisplay = self.xlib.XOpenDisplay
        XOpenDisplay.restype = ctypes.POINTER(Display)
        XOpenDisplay.argtypes = [ctypes.c_char_p]

        # Open the display
        self.display = XOpenDisplay(display)
        if not self.display:
            raise Exception("Cannot open display. Check DISPLAY and XAUTHORITY env variables.")

    def argbdata_to_pixdata(self, data, data_len):
        """Convert ARGB data to RGBA pixel data"""
        if data is None or data_len < 1:
            return None

        # Create byte array
        b = array.array("b", b"\x00" * 4 * data_len)

        offset, i = 0, 0
        while i < data_len:
            argb = data[i] & 0xFFFFFFFF
            # Convert ARGB to RGBA
            rgba = (argb << 8) | (argb >> 24)
            b1 = (rgba >> 24) & 0xFF
            b2 = (rgba >> 16) & 0xFF
            b3 = (rgba >> 8) & 0xFF
            b4 = rgba & 0xFF

            struct.pack_into("=BBBB", b, offset, b1, b2, b3, b4)
            offset = offset + 4
            i = i + 1

        return b

    def get_cursor_image_data(self):
        """Get the raw cursor image data from XFixes"""
        cursor_data = self.XFixesGetCursorImage(self.display)

        if not (cursor_data and cursor_data[0]):
            raise Exception("Cannot read XFixesGetCursorImage()")

        # cursor_data is a pointer, take cursor_data[0]
        return cursor_data[0]

    def get_cursor_image_array(self):
        """Get cursor image as numpy array (slow but reliable method)"""
        data = self.get_cursor_image_data()
        height, width = data.height, data.width

        bytearr = self.argbdata_to_pixdata(data.pixels, height * width)
        imgarray = np.array(bytearr, dtype=np.uint8)
        imgarray = imgarray.reshape(height, width, 4)

        return imgarray

    def get_cursor_image_array_fast(self):
        """Get cursor image as numpy array (faster method)"""
        data = self.get_cursor_image_data()
        height, width = data.height, data.width

        try:
            bytearr = ctypes.cast(data.pixels, ctypes.POINTER(ctypes.c_ulong * height * width))[0]
            imgarray = np.array(bytearray(bytearr))
            imgarray = imgarray.reshape(height, width, 8)[:, :, (0, 1, 2, 3)]
            return imgarray
        except Exception:
            # Fall back to slower method if fast method fails
            return self.get_cursor_image_array()

    def save_image(self, imgarray, filename):
        """Save numpy array as image"""
        img = Image.fromarray(imgarray)
        img.save(filename)
        return img

    def get_cursor_position(self):
        """Get cursor position using pynput"""
        return mouse.Controller().position


class CursorTracker:
    """Class to track cursor using pynput"""

    def __init__(self, save_path="cursor_captures"):
        self.cursor = Xcursor()
        self.save_path = save_path
        self.mouse_controller = mouse.Controller()
        self.count = 0

        # Create save directory if it doesn't exist
        if not os.path.exists(save_path):
            os.makedirs(save_path)

    def on_move(self, x, y):
        """Callback function for mouse movement"""
        print(f"Mouse moved to ({x}, {y})")

    def on_click(self, x, y, button, pressed):
        """Callback function for mouse clicks"""
        if pressed:
            print(f"Mouse clicked at ({x}, {y}) with {button}")
            self.capture_cursor()

    def capture_cursor(self):
        """Capture and save cursor image"""
        try:
            # Get cursor position from pynput
            x, y = self.mouse_controller.position

            # Get cursor image
            img_array = self.cursor.get_cursor_image_array_fast()

            # Save image
            filename = f"{self.save_path}/cursor_{self.count}_{x}_{y}.png"
            self.cursor.save_image(img_array, filename)
            print(f"Saved cursor image to {filename}")
            self.count += 1

        except Exception as e:
            print(f"Error capturing cursor: {e}")

    def start_tracking(self):
        """Start tracking mouse movements and clicks"""
        print("Starting cursor tracking. Click to capture cursor image. Press Ctrl-C to exit.")
        with mouse.Listener(on_move=self.on_move, on_click=self.on_click) as listener:
            try:
                listener.join()
            except KeyboardInterrupt:
                print("Tracking stopped.")


def capture_single_cursor():
    """Capture a single cursor image"""
    try:
        cursor = Xcursor()
        imgarray = cursor.get_cursor_image_array_fast()
        cursor.save_image(imgarray, "cursor_image.png")
        print("Cursor image saved to cursor_image.png")
    except Exception as e:
        print(f"Error: {e}")

        # Try to diagnose X11 connection issues
        print("\nDiagnosing X11 connection:")
        print(f"DISPLAY: {os.environ.get('DISPLAY', 'Not set')}")
        print(f"XAUTHORITY: {os.environ.get('XAUTHORITY', 'Not set')}")

        # Check for Wayland
        if "WAYLAND_DISPLAY" in os.environ:
            print("Wayland detected. Make sure XWayland is running.")

        # Try to check X11 connection with xdpyinfo
        try:
            import subprocess

            result = subprocess.run(["xdpyinfo"], capture_output=True, text=True)
            if result.returncode == 0:
                print("xdpyinfo succeeded - X11 connection works")
            else:
                print("xdpyinfo failed - X11 connection issue")
                print(result.stderr)
        except:
            print("Could not run xdpyinfo")


if __name__ == "__main__":
    # Capture a single cursor image
    capture_single_cursor()

    # Uncomment to start tracking
    # tracker = CursorTracker()
    # tracker.start_tracking()

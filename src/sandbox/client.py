import time
from typing import Dict, List, Literal, Optional

import requests


class VMServerClient:
    def __init__(self, base_url: str, retries: int = 3, retry_delay: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.retry_delay = retry_delay

    def _request_with_retries(self, method: str, endpoint: str, *, params=None, json=None) -> Dict:
        url = f"{self.base_url}{endpoint}"
        for attempt in range(self.retries):
            try:
                response = requests.request(method.upper(), url, params=params, json=json, timeout=10)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                if attempt < self.retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    return {"status": "error", "message": str(e)}

    def take_screenshot(self, method: Literal["pyautogui", "pillow"] = "pyautogui") -> Dict:
        """Capture and return metadata for a screenshot taken in the sandbox VM."""
        return self._request_with_retries("GET", "/screenshot", params={"method": method})

    def execute_code(self, code: str, packages: Optional[List[str]] = None) -> Dict:
        """Run arbitrary Python code inside the sandbox VM."""
        payload = {"code": code, "packages": packages or []}
        return self._request_with_retries("POST", "/execute", json=payload)

    def execute_gui_code(self, code: str, packages: Optional[List[str]] = None) -> Dict:
        """Run GUI-interaction Python code and return screenshot metadata."""
        payload = {"code": code, "packages": packages or []}
        return self._request_with_retries("POST", "/execute_gui", json=payload)

    def list_packages(self) -> List[Dict[str, str]]:
        """Return a list of installed packages in the sandbox environment."""
        return self._request_with_retries("GET", "/packages")

    def check_health(self) -> Dict:
        """Check whether the sandbox server is reachable and healthy."""
        return self._request_with_retries("GET", "/health")

    def record_start(self) -> Dict:
        """Start recording mouse and keyboard actions inside the sandbox."""
        return self._request_with_retries("GET", "/record", params={"mode": "start"})

    def record_stop(self) -> Dict:
        """Stop recording and return metadata about the saved recording file."""
        return self._request_with_retries("GET", "/record", params={"mode": "stop"})

import json
import socket
import threading
from pathlib import Path
from typing import Dict

PORTMAP_FILE = Path("portmap.json")
PORT_KEYS = ["ssh", "vnc", "sandbox_server", "jupyter"]
START_PORT = 60000


class PortManager:
    def __init__(self, start: int = START_PORT):
        self._next = start
        self._lock = threading.Lock()
        self._portmap: Dict[str, Dict[str, int]] = self._load_portmap()

    def get_ports(self, container_name: str) -> Dict[str, int]:
        """Return the ports assigned to a container. Create new ones if not found."""
        with self._lock:
            if container_name in self._portmap:
                return self._portmap[container_name]

            ports = {}
            for key in PORT_KEYS:
                port = self._find_open_port()
                ports[key] = port

            self._portmap[container_name] = ports
            self._save_portmap()
            return ports

    def _find_open_port(self) -> int:
        """Return the next available open port."""
        while not self._is_port_free(self._next):
            self._next += 1
        port = self._next
        self._next += 1
        return port

    @staticmethod
    def _is_port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return s.connect_ex(("127.0.0.1", port)) != 0

    def _load_portmap(self) -> Dict[str, Dict[str, int]]:
        if PORTMAP_FILE.exists():
            try:
                return json.loads(PORTMAP_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _save_portmap(self):
        PORTMAP_FILE.write_text(json.dumps(self._portmap, indent=2))

    def reset(self):
        """Clear all mappings (e.g., for test/debug)."""
        with self._lock:
            self._portmap = {}
            self._save_portmap()


# Create a global instance
PORT_MANAGER = PortManager()

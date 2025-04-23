from __future__ import annotations

# ──────────────────────────── Error Types ────────────────────────────


class VMManagerError(Exception):
    pass


class VMCreationError(VMManagerError):
    pass


class VMOperationError(VMManagerError):
    pass


class SSHError(VMManagerError):
    pass


class RemoteCommandError(VMOperationError):
    def __init__(self, cmd: str, status: int, stderr: str):
        super().__init__(f"Command '{cmd}' failed (exit {status}): {stderr}")
        self.cmd = cmd
        self.status = status
        self.stderr = stderr

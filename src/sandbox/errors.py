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
        message = (
            f"\n📛 Remote command execution failed!\n"
            f"   ↳ Command: {cmd}\n"
            f"   ↳ Exit Code: {status}\n"
            f"   ↳ Stderr:\n{stderr.strip() or '(empty)'}"
        )
        super().__init__(message)
        self.cmd = cmd
        self.status = status
        self.stderr = stderr

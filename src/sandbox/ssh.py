from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import paramiko
from tenacity import retry, stop_after_attempt, wait_exponential

from sandbox.errors import RemoteCommandError, SSHError, VMOperationError

# ───────────────────────


@dataclass
class SSHConfig:
    hostname: str = "localhost"
    port: int = 2222
    username: str = "user"
    password: str = "password"
    key_filename: Optional[str] = None
    connect_timeout: int = 30
    max_retries: int = 5
    retry_delay: int = 5
    command_timeout: int = 60
    initial_delay: int = 15
    idle_timeout: int = 300


# ──────────────────────────── SSH Client ──────────────────────────────


class SSHClient:
    def __init__(self, cfg: SSHConfig, logger: logging.Logger):
        self.cfg = cfg
        self.log = logger or logging.Logger
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._last_used = 0.0

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(min=1, max=30), reraise=True)
    def _establish(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.cfg.hostname,
            port=self.cfg.port,
            username=self.cfg.username,
            password=self.cfg.password,
            key_filename=self.cfg.key_filename,
            timeout=self.cfg.connect_timeout,
        )
        self._last_used = time.time()
        self.log.info("SSH connection established")
        return client

    def connect(self) -> paramiko.SSHClient:
        if self._client and self._client.get_transport() and self._client.get_transport().is_active():
            self._last_used = time.time()
            return self._client
        time.sleep(self.cfg.initial_delay)
        try:
            self._client = self._establish()
        except Exception as e:
            raise SSHError("SSH connection failed") from e
        return self._client

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._client:
            self._client.close()
            self.log.info("SSH connection closed")
            self._client = None

    def exec_command(self, cmd: str, cwd: str = "", env: Dict[str, str] | None = None) -> Dict[str, Any]:
        full = f"cd {cwd} && {cmd}" if cwd else cmd
        client = self.connect()
        self.log.debug("ssh $ %s", full)
        stdin, stdout, stderr = client.exec_command(full, timeout=self.cfg.command_timeout, environment=env)
        status = stdout.channel.recv_exit_status()
        out = stdout.read().decode()
        err = stderr.read().decode()
        if status != 0:
            raise RemoteCommandError(cmd, status, err)
        return {"status": status, "stdout": out, "stderr": err}

    def stream_command(self, cmd: str, cwd: str = "", env: Dict[str, str] | None = None) -> None:
        full_cmd = f"cd {cwd} && {cmd}" if cwd else cmd
        client = self.connect()
        transport = client.get_transport()
        channel = transport.open_session()

        if env:
            for key, val in env.items():
                channel.update_environment({key: val})

        channel.get_pty(term="xterm", width=200, height=24)
        channel.exec_command(full_cmd)

        self.log.info("Streaming logs from command: %s", full_cmd)

        try:
            while True:
                if channel.recv_ready():
                    output = channel.recv(4096).decode("utf-8", errors="replace")
                    if output:
                        for line in output.splitlines():
                            self.log.info("[STDOUT] %s", line)

                if channel.recv_stderr_ready():
                    error = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                    if error:
                        for line in error.splitlines():
                            self.log.error("[STDERR] %s", line)

                if channel.exit_status_ready():
                    exit_code = channel.recv_exit_status()
                    self.log.info("Stream finished with exit code: %s", exit_code)
                    break

                time.sleep(0.2)
        finally:
            channel.close()

    def get_sftp(self) -> paramiko.SFTPClient:
        if not self._sftp or not (self._client and self._client.get_transport().is_active()):
            self._sftp = self.connect().open_sftp()
            self._last_used = time.time()
        return self._sftp

    def _mkdir_p(self, sftp: paramiko.SFTPClient, remote_dir: str) -> None:
        """Recursively create remote directories like mkdir -p"""
        dirs = []
        path = remote_dir
        while len(path) > 1:
            try:
                sftp.stat(path)
                break
            except IOError:
                dirs.append(path)
                path = os.path.dirname(path)
        for d in reversed(dirs):
            self.log.debug("Creating remote dir: %s", d)
            sftp.mkdir(d)

    def transfer_directory(self, local: Path, remote: str) -> None:
        if not local.is_dir():
            raise VMOperationError(f"Local directory not found: {local}")
        sftp = self.get_sftp()
        self.exec_command(f"mkdir -p {remote}")  # base remote dir
        for file in local.rglob("*"):
            if file.is_file():
                relative = file.relative_to(local)
                target = Path(remote) / relative
                self._mkdir_p(sftp, str(target.parent))  # create parent dirs
                self.log.debug("SFTP: %s → %s", file, target)
                sftp.put(str(file), str(target))
        self.log.info("Transferred %s → %s", local, remote)

    def open_shell(self) -> paramiko.Channel:
        client = self.connect()
        chan = client.invoke_shell()
        paramiko.util.interactive_shell(chan)
        return chan

from __future__ import annotations

import concurrent.futures as futures
import os
import posixpath
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import paramiko
from smolagents import AgentLogger, LogLevel

from .errors import RemoteCommandError, SSHError, VMOperationError


# ---------------------------------------------------------------------------
# configuration dataclass ----------------------------------------------------
# ---------------------------------------------------------------------------
@dataclass
class SSHConfig:
    hostname: str = "localhost"
    port: int = 2222
    username: str = "user"
    password: str = "password"
    key_filename: Optional[str] = None
    connect_timeout: int = 30
    command_timeout: int = 60
    initial_delay: int = 15  # seconds to wait before first connect
    banner_timeout: int = 10  # seconds to wait for SSH banner
    keepalive: int = 10  # TCP keep‑alive interval


# shorthand for file‑system path hints
PathLike = str | os.PathLike[str]


# ---------------------------------------------------------------------------
# helper: remote mkdir ‑p ----------------------------------------------------
# ---------------------------------------------------------------------------


def _mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str, logger: AgentLogger | None = None) -> None:
    """POSIX ``mkdir -p`` implementation using Paramiko SFTP."""
    if remote_dir in ("", "/"):
        return
    parent = posixpath.dirname(remote_dir.rstrip("/"))
    try:
        sftp.stat(remote_dir)
    except IOError:  # path does not exist
        _mkdir_p(sftp, parent, logger)
        if logger:
            logger.log(f"Creating remote dir: {remote_dir}", level=LogLevel.DEBUG)
        sftp.mkdir(remote_dir)


# ---------------------------------------------------------------------------
# main client ---------------------------------------------------------------
# ---------------------------------------------------------------------------
class SSHClient:
    """Core client for basic SSH command execution & directory upload."""

    def __init__(self, cfg: SSHConfig, logger: AgentLogger | None = None):
        self.cfg = cfg
        self.logger: AgentLogger = logger or AgentLogger(level=LogLevel.INFO)
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    # -- context‑manager -----------------------------------------------------
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # -- connection plumbing -------------------------------------------------
    def _establish(self) -> paramiko.SSHClient:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # fine for local VM
        cli.connect(
            hostname=self.cfg.hostname,
            port=self.cfg.port,
            username=self.cfg.username,
            password=self.cfg.password,
            key_filename=self.cfg.key_filename,
            timeout=self.cfg.connect_timeout,
            banner_timeout=self.cfg.banner_timeout,
        )
        cli.get_transport().set_keepalive(self.cfg.keepalive)
        self.logger.log("SSH connection established", level=LogLevel.DEBUG)
        return cli

    def connect(self) -> paramiko.SSHClient:
        if self._client and self._client.get_transport() and self._client.get_transport().is_active():
            return self._client
        if self.cfg.initial_delay:
            self.logger.log(
                f"Initial delay {self.cfg.initial_delay}s before first connection",
                level=LogLevel.DEBUG,
            )
            time.sleep(self.cfg.initial_delay)
        try:
            self._client = self._establish()
        except Exception as exc:  # pragma: no cover – propagate with context
            raise SSHError(f"SSH connection failed: {exc!r}") from exc
        return self._client

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._client:
            self._client.close()
            self._client = None
        self.logger.log("SSH connection closed", level=LogLevel.DEBUG)

    # -- command execution ----------------------------------------------------
    def exec_command(
        self,
        cmd: str,
        cwd: str | None = None,
        env: Dict[str, str] | None = None,
        *,
        as_root: bool = False,
        block: bool = True,
    ) -> Dict[str, Any] | None:
        """Run *cmd* on the VM and return ``{"status", "stdout", "stderr"}``."""
        if cwd:
            cmd = f"cd {cwd} && {cmd}"
        if as_root and not cmd.startswith("sudo"):
            cmd = f"sudo {cmd}"

        self.logger.log(f"ssh $ {cmd}", level=LogLevel.DEBUG)
        ssh = self.connect()
        _, stdout, stderr = ssh.exec_command(
            cmd,
            get_pty=True,  # login‑shell behaviour
            timeout=self.cfg.command_timeout,
            environment=env,
        )

        if not block:
            return None  # fire‑and‑forget mode

        status = stdout.channel.recv_exit_status()
        out, err = stdout.read().decode(), stderr.read().decode()
        self.logger.log(
            f"→ exit {status} | stdout {len(out)} B | stderr {len(err)} B",
            level=LogLevel.DEBUG,
        )
        if status:
            raise RemoteCommandError(cmd, status, err)
        return {"status": status, "stdout": out, "stderr": err}

    # -- SFTP / file transfer -------------------------------------------------
    def _get_sftp(self) -> paramiko.SFTPClient:
        if not self._sftp or not (self._client and self._client.get_transport().is_active()):
            self._sftp = self.connect().open_sftp()
        return self._sftp

    def transfer_directory(
        self,
        local: PathLike,
        remote: PathLike,
        *,
        exclude: Optional[List[PathLike]] = None,
        workers: int = 1,
    ) -> None:
        """Recursively copy *local* directory to *remote* path over SFTP."""
        local_path = Path(local).expanduser().resolve()
        if not local_path.is_dir():
            raise VMOperationError(f"Local directory not found: {local_path}")

        exclude_set = {local_path / Path(p) for p in (exclude or [])}
        sftp = self._get_sftp()
        self.exec_command(f"mkdir -p {remote}")  # ensure top‑level exists

        def _upload(file: Path):
            if any(file.is_relative_to(e) for e in exclude_set):
                self.logger.log(f"Skipping excluded file: {file}", level=LogLevel.DEBUG)
                return
            if file.is_dir():
                return
            rel = file.relative_to(local_path)
            target = posixpath.join(str(remote), rel.as_posix())
            _mkdir_p(sftp, posixpath.dirname(target), self.logger)
            self.logger.log(f"SFTP: {file} → {target}", level=LogLevel.DEBUG)
            sftp.put(str(file), target)

        if workers > 1:
            self.logger.log(f"Uploading with {workers} threads...", level=LogLevel.DEBUG)
            with futures.ThreadPoolExecutor(workers) as pool:
                list(pool.map(_upload, local_path.rglob("*")))
        else:
            for f in local_path.rglob("*"):
                _upload(f)

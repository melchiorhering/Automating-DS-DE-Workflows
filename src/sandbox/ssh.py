from __future__ import annotations

import concurrent.futures as futures
import os
import posixpath
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import paramiko
from smolagents import AgentLogger, LogLevel

from .errors import RemoteCommandError, SSHError, VMOperationError


# ────────────────────────────────────────────────────────────────────
# SSH configuration
# ────────────────────────────────────────────────────────────────────
@dataclass
class SSHConfig:
    hostname: str = "localhost"
    port: int = 2222
    username: str = "user"
    password: str = "password"
    key_filename: Optional[str] = None
    connect_timeout: int = 30
    command_timeout: int = 60
    initial_delay: int = 15
    banner_timeout: int = 10
    keepalive: int = 10


PathLike = str | os.PathLike[str]


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────
def _mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str, logger: AgentLogger | None = None) -> None:
    if remote_dir in ("", "/"):
        return
    parent = posixpath.dirname(remote_dir.rstrip("/"))
    try:
        sftp.stat(remote_dir)
    except IOError:
        _mkdir_p(sftp, parent, logger)
        if logger:
            logger.log(f"Creating remote dir: {remote_dir}", level=LogLevel.DEBUG)
        sftp.mkdir(remote_dir)


# ────────────────────────────────────────────────────────────────────
# SSH client
# ────────────────────────────────────────────────────────────────────
class SSHClient:
    """Run commands and copy files / directories over SSH."""

    # ------------------------------------------------------------------
    def __init__(self, cfg: SSHConfig, logger: AgentLogger | None = None):
        self.cfg = cfg
        self.logger = logger or AgentLogger(level=LogLevel.INFO)
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    # ------------------------------------------------------------------
    # Context-manager sugar
    # ------------------------------------------------------------------
    def __enter__(self):  # noqa: DunderIter
        self.connect()
        return self

    def __exit__(self, *_exc):
        self.close()

    # ------------------------------------------------------------------
    # Connection plumbing
    # ------------------------------------------------------------------
    def _establish(self) -> paramiko.SSHClient:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
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
            self.logger.log(f"Initial delay {self.cfg.initial_delay}s before connect", level=LogLevel.DEBUG)
            time.sleep(self.cfg.initial_delay)
        try:
            self._client = self._establish()
        except Exception as exc:
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

    def _get_sftp(self) -> paramiko.SFTPClient:
        if not self._sftp or not (self._client and self._client.get_transport().is_active()):
            self._sftp = self.connect().open_sftp()
        return self._sftp

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------
    def exec_command(
        self,
        cmd: str,
        cwd: str | None = None,
        env: Dict[str, str] | None = None,
        *,
        as_root: bool = False,
        block: bool = True,
    ) -> Dict[str, Any] | None:
        if cwd:
            cmd = f"cd {cwd} && {cmd}"
        if as_root and not cmd.startswith("sudo"):
            cmd = f"sudo {cmd}"

        self.logger.log(f"ssh $ {cmd}", level=LogLevel.DEBUG)
        ssh = self.connect()
        _, stdout, stderr = ssh.exec_command(
            cmd,
            get_pty=True,
            timeout=self.cfg.command_timeout,
            environment=env,
        )

        if not block:
            return None

        status = stdout.channel.recv_exit_status()
        out, err = stdout.read().decode(), stderr.read().decode()
        self.logger.log(f"→ exit {status} | stdout {len(out)}B | stderr {len(err)}B", level=LogLevel.DEBUG)
        if status:
            raise RemoteCommandError(cmd, status, err)
        return {"status": status, "stdout": out, "stderr": err}

    # ------------------------------------------------------------------
    # Host → Guest  (put)
    # ------------------------------------------------------------------
    def put_file(
        self,
        local: PathLike,
        remote: PathLike,
        *,
        mkdir_parents: bool = True,
        overwrite: bool = True,
    ) -> None:
        local_path = Path(local).expanduser().resolve()
        if not local_path.is_file():
            raise VMOperationError(f"Local file not found: {local_path}")
        remote_path = posixpath.normpath(str(remote))
        sftp = self._get_sftp()

        if not overwrite:
            try:
                sftp.stat(remote_path)
                raise VMOperationError(f"Remote file exists: {remote_path}")
            except IOError:
                pass

        if mkdir_parents:
            _mkdir_p(sftp, posixpath.dirname(remote_path), self.logger)

        self.logger.log(f"SFTP put: {local_path} → {remote_path}", level=LogLevel.DEBUG)
        try:
            sftp.put(str(local_path), remote_path)
        except IOError as exc:
            raise VMOperationError(f"Failed to upload {local_path}: {exc}") from exc

    def put_directory(
        self,
        local_dir: PathLike,
        remote_dir: PathLike,
        *,
        exclude: Optional[List[PathLike]] = None,
        workers: int = 1,
    ) -> None:
        local_dir = Path(local_dir).expanduser().resolve()
        if not local_dir.is_dir():
            raise VMOperationError(f"Local directory not found: {local_dir}")
        exclude_set = {local_dir / Path(p) for p in (exclude or [])}
        sftp = self._get_sftp()
        self.exec_command(f"mkdir -p {remote_dir}")

        def _upload(file: Path):
            if any(file.is_relative_to(e) for e in exclude_set) or file.is_dir():
                return
            rel = file.relative_to(local_dir)
            dest = posixpath.join(str(remote_dir), rel.as_posix())
            self.put_file(file, dest, mkdir_parents=True)

        files = list(local_dir.rglob("*"))
        if workers > 1:
            with futures.ThreadPoolExecutor(workers) as pool:
                list(pool.map(_upload, files))
        else:
            for f in files:
                _upload(f)

    # ------------------------------------------------------------------
    # Guest → Host  (get)
    # ------------------------------------------------------------------
    def get_file(
        self,
        remote: PathLike,
        local: PathLike,
        *,
        overwrite: bool = True,
    ) -> None:
        sftp = self._get_sftp()
        remote_path = posixpath.normpath(str(remote))
        local_path = Path(local).expanduser().resolve()
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if local_path.exists() and not overwrite:
            raise VMOperationError(f"Local file exists: {local_path}")
        self.logger.log(f"SFTP get: {remote_path} → {local_path}", level=LogLevel.DEBUG)
        try:
            sftp.get(remote_path, str(local_path))
        except IOError as exc:
            raise VMOperationError(f"Failed to fetch {remote_path}: {exc}") from exc

    def get_directory(
        self,
        remote_dir: PathLike,
        local_dir: PathLike,
        *,
        workers: int = 1,
    ) -> None:
        remote_dir = posixpath.normpath(str(remote_dir)).rstrip("/")
        local_dir = Path(local_dir).expanduser().resolve()
        local_dir.mkdir(parents=True, exist_ok=True)
        sftp = self._get_sftp()

        def _walk(path: str):
            for entry in sftp.listdir_attr(path):
                r_path = posixpath.join(path, entry.filename)
                if stat.S_ISDIR(entry.st_mode):
                    yield from _walk(r_path)
                else:
                    yield r_path

        def _download(r_file: str):
            rel = Path(r_file[len(remote_dir) + 1 :])
            l_file = local_dir / rel
            l_file.parent.mkdir(parents=True, exist_ok=True)
            self.logger.log(f"SFTP get: {r_file} → {l_file}", level=LogLevel.DEBUG)
            sftp.get(r_file, str(l_file))

        files = list(_walk(remote_dir))
        if workers > 1 and len(files) > 1:
            with futures.ThreadPoolExecutor(workers) as pool:
                list(pool.map(_download, files))
        else:
            for f in files:
                _download(f)

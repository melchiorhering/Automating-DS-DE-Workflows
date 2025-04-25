from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import paramiko
from tenacity import retry, stop_after_attempt, wait_exponential

from sandbox.errors import RemoteCommandError, SSHError, VMOperationError


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


class SFTPHelper:
    def __init__(self, sftp: paramiko.SFTPClient, logger: logging.Logger):
        self.sftp = sftp
        self.log = logger

    def mkdir_p(self, remote_dir: str) -> None:
        dirs = []
        path = remote_dir
        while len(path) > 1:
            try:
                self.sftp.stat(path)
                break
            except IOError:
                dirs.append(path)
                path = os.path.dirname(path)
        for d in reversed(dirs):
            self.log.debug("Creating remote dir: %s", d)
            self.sftp.mkdir(d)

    def transfer_directory(
        self, local: Union[Path, str], remote: Union[Path, str], exclude: Optional[List[Union[str, Path]]] = None
    ) -> None:
        local = Path(local).resolve()
        remote = Path(remote)

        if not local.is_dir():
            raise VMOperationError(f"Local directory not found: {local}")

        exclude_paths = {(local / Path(p)).resolve() for p in (exclude or [])}
        self.sftp.chdir("/")

        for file in local.rglob("*"):
            file = file.resolve()
            if any(file.is_relative_to(ex) for ex in exclude_paths):
                self.log.debug("Skipping excluded file: %s", file)
                continue

            if file.is_file():
                relative = file.relative_to(local)
                target = remote / relative
                self.mkdir_p(str(target.parent))
                self.log.debug("SFTP: %s → %s", file, target)
                self.sftp.put(str(file), str(target))


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

    def exec_command(
        self,
        cmd: str,
        cwd: str = "",
        env: Dict[str, str] | None = None,
        block: bool = True,
        as_root: bool = False,
    ) -> Dict[str, Any] | None:
        # Build full command
        full_cmd = f"cd {cwd} && {cmd}" if cwd else cmd
        if as_root and not full_cmd.startswith("sudo"):
            full_cmd = f"sudo {full_cmd}"

        # Wrap command to use login shell (bash -l -c '...')
        # wrapped_cmd = f"bash -l -c '{full_cmd}'"

        client = self.connect()
        self.log.debug("ssh $ %s", full_cmd)

        # Open SSH command with get_pty=True for login shell
        stdin, stdout, stderr = client.exec_command(
            full_cmd,
            timeout=self.cfg.command_timeout,
            environment=env,
            get_pty=True,  # ← this is CRITICAL
        )

        if not block:
            self.log.debug("Non-blocking exec_command issued.")
            return None

        status = stdout.channel.recv_exit_status()
        out = stdout.read().decode()
        err = stderr.read().decode()

        if status != 0:
            raise RemoteCommandError(cmd, status, err)

        return {"status": status, "stdout": out, "stderr": err}

    def get_sftp(self) -> paramiko.SFTPClient:
        if not self._sftp or not (self._client and self._client.get_transport().is_active()):
            self._sftp = self.connect().open_sftp()
            self._last_used = time.time()
        return self._sftp

    def transfer_directory(
        self, local: Union[Path, str], remote: Union[Path, str], exclude: Optional[List[Union[str, Path]]] = None
    ) -> None:
        local = Path(local).resolve()
        remote = Path(remote)

        sftp = self.get_sftp()
        self.exec_command(f"mkdir -p {remote}")
        helper = SFTPHelper(sftp, self.log)
        helper.transfer_directory(local, remote, exclude=exclude)

    def send_command(
        self,
        chan: paramiko.Channel,
        cmd: str,
        prompt: str = "$",
        timeout: float = 5.0,
        wait_interval: float = 0.1,
    ) -> str:
        """
        Send a command over an interactive shell and wait for the prompt to return.

        Args:
            chan: The paramiko.Channel with an open shell.
            cmd: The command to send.
            prompt: The shell prompt to wait for (default '$').
            timeout: Maximum time to wait for command to finish.
            wait_interval: How often to check for new data.

        Returns:
            Full command output (excluding the command itself).
        """
        chan.send(cmd.strip() + "\n")

        output = ""
        start_time = time.time()

        while True:
            if chan.recv_ready():
                output += chan.recv(4096).decode()

                # Look for prompt (means command finished)
                if output.rstrip().endswith(prompt):
                    break

            if time.time() - start_time > timeout:
                raise TimeoutError(f"Timeout waiting for command '{cmd}' to complete")

            time.sleep(wait_interval)

        # Clean output: remove the echoed command and prompt
        lines = output.splitlines()

        # Remove first line if it contains the command we just sent
        if lines and cmd.strip() in lines[0]:
            lines = lines[1:]

        # Remove last line if it is the shell prompt
        if lines and lines[-1].strip() == prompt.strip():
            lines = lines[:-1]

        return "\n".join(lines).strip()

    def open_shell(self) -> paramiko.Channel:
        client = self.connect()

        # Open an interactive shell (request xterm)
        chan = client.invoke_shell(term="xterm")
        self.log.info("SSH interactive shell opened (xterm)")

        time.sleep(0.5)  # Give it a moment to initialize

        # Try to detect if we are already in a login shell
        chan.send("echo $0\n")
        time.sleep(0.5)

        output = ""
        while chan.recv_ready():
            output += chan.recv(4096).decode()

        self.log.debug("Shell detection output: %s", output.strip())

        if not any(shell_name in output for shell_name in ["-bash", "bash -l", "login"]):
            # If not in a login shell already, switch to bash login shell
            self.log.info("🔄 Switching to bash login shell...")
            chan.send("bash -l\n")
            time.sleep(0.5)

        return chan

"""High‑level wrapper that spins up a QEMU‑in‑Docker VM, keeps exactly **one**
SSH connection to it and exposes notebook‑friendly helpers like

    vm = QemuVMManager(...)
    vm.get_or_create_container()   # boots or re‑uses the VM
    vm.exec("uname -a")            # run single command
    vm.shell()                     # raw interactive shell
    vm.repl()                      # colour REPL with TAB completion

Requires: docker‑python, paramiko, rich, prompt‑toolkit.
"""

from __future__ import annotations

# ────────────────────────────── stdlib ──────────────────────────────
import logging
import os
import select
import shlex
import shutil
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, fields
from enum import Enum
from functools import cached_property
from pathlib import Path
from shlex import quote as shq
from typing import Any, Callable, Dict, List, Optional, Union

# ───────────────────────── third‑party ──────────────────────────────
import paramiko
from IPython import get_ipython
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from rich.console import Console

import docker
from docker.models.containers import Container
from docker.types import Mount

logging.getLogger("paramiko.transport").setLevel(logging.WARNING)  # silence spam


# ───────────────────────── helpers ──────────────────────────────────


def _setup_logger(name: str) -> logging.Logger:
    lg = logging.getLogger(name)
    if not lg.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
    return lg


def _ensure_dir(p: Path, mode: int = 0o777):
    p.mkdir(exist_ok=True, parents=True)
    os.chmod(p, mode)


# ────────────────────────── errors / enums ──────────────────────────
class VMManagerError(Exception): ...


class VMCreationError(VMManagerError): ...


class VMOperationError(VMManagerError): ...


class SSHError(VMManagerError): ...


class VMState(Enum):
    INITIALIZING = "initializing"
    CREATING = "creating"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# ─────────────────────────── dataclasses ────────────────────────────
@dataclass
class VMConfig:
    # container & image
    container_image: str = "qemux/qemu"
    container_name: str = "qemu"
    unique_container_name: bool = False
    # VM sizing
    vm_boot_image: str = "ubuntu"
    vm_ram: str = "4G"
    vm_cpu_cores: int = 4
    vm_disk_size: str = "16G"
    enable_debug: bool = True
    extra_env: Dict[str, str] = field(default_factory=dict)
    # ports
    vnc_port: int = 8006
    ssh_port: int = 2222
    extra_ports: Dict[str, int] = field(default_factory=dict)
    # devices / caps
    devices: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    restart_policy: str = "always"
    # paths
    vm_base_dir: Optional[Path] = None
    instances_dir: Path = Path("docker/environments")
    host_shared_dir: Optional[Path] = None
    guest_shared_dir: Path = Path("/shared")
    # runtime env passed to start.sh
    runtime_env: Dict[str, str] = field(default_factory=dict)
    # server deployment
    server_host_dir: Optional[Path] = None
    server_guest_dir: Path = Path("/home/user/server")

    # --- helpers ---------------------------------------------------------
    def __post_init__(self):
        self.instances_dir = self.instances_dir.resolve()
        if self.unique_container_name:
            self.container_name = f"{self.container_name}_{int(time.time())}"
        for attr in ("vm_base_dir", "host_shared_dir", "server_host_dir"):
            val = getattr(self, attr)
            if val:
                setattr(self, attr, Path(val).resolve())

    @staticmethod
    def _unique_name(base: str) -> str:
        return f"{base}_{int(time.time())}"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]):  # convenience loader
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in fields(cls)}})

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


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

    @classmethod
    def from_dict(cls, d: Dict[str, Any]):
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in fields(cls)}})


# ---------------------------------------------------------------------------
# SSH client – unchanged API, new helper .out()
# ---------------------------------------------------------------------------


class SSHClient:
    def __init__(self, *, logger: logging.Logger, config: SSHConfig):
        self.log, self.cfg = logger, config
        self._cli: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._last_used = 0.0
        self._idle_timeout = 300

    # context‑manager -------------------------------------------------
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # connection handling --------------------------------------------
    def _active(self):
        if not self._cli:
            return False
        tr = self._cli.get_transport()
        if not (tr and tr.is_active()):
            return False
        if time.time() - self._last_used > self._idle_timeout:
            return False
        try:
            tr.send_ignore()
        except Exception:
            return False
        return True

    # ---------- public API ----------------------------------------------
    def connect(self):
        if self._active():
            self._last_used = time.time()
            return self._cli
        self.close()
        time.sleep(self.cfg.initial_delay)
        self._cli = paramiko.SSHClient()
        self._cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                self.log.info("SSH connect attempt %d/%d", attempt, self.cfg.max_retries)
                self._cli.connect(
                    hostname=self.cfg.hostname,
                    port=self.cfg.port,
                    username=self.cfg.username,
                    password=self.cfg.password,
                    key_filename=self.cfg.key_filename,
                    timeout=self.cfg.connect_timeout,
                )
                self._last_used = time.time()
                self.log.info("SSH connection established")
                return self._cli
            except paramiko.SSHException:
                if attempt == self.cfg.max_retries:
                    raise SSHError("SSH connect failed")
                time.sleep(min(2**attempt, 30))
        raise SSHError("SSH connect attempts exhausted")

    def close(self):
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._cli:
            self._cli.close()
            self._cli = None
            self.log.info("SSH connection closed")

    def get_sftp(self) -> paramiko.SFTPClient:
        """
        Return an open SFTP session tied to *the* SSH connection.
        A new session is created only when necessary:
          • first call
          • after the SSH connection has been re‑established
        """
        # if we already have one *and* the transport is still alive – reuse it
        if self._sftp and self._active():
            return self._sftp

        # otherwise (re)connect and open a fresh channel
        cli = self.connect()  # refresh _cli as needed
        self._sftp = cli.open_sftp()
        self._last_used = time.time()  # bump activity timer
        return self._sftp

    # ----- run commands --------------------------------------------------
    def exec_command(self, cmd: str, *, cwd: str = "", env: Dict[str, str] | None = None):
        full = f"cd {cwd} && {cmd}" if cwd else cmd
        cli = self.connect()
        self.log.debug("ssh $ %s", full)
        _in, out, err = cli.exec_command(full, timeout=self.cfg.command_timeout, environment=env)
        status = out.channel.recv_exit_status()
        res = dict(status=status, stdout=out.read().decode(), stderr=err.read().decode())
        if status:
            self.log.error("exit %d: %s", status, res["stderr"].strip())
        return res

    def exec_commands(
        self, cmds: List[str], cwd: str = "", env: Optional[Dict[str, str]] = None, stream: bool = False
    ) -> List[Union[Dict[str, Any], int]]:
        """
        Run multiple commands over SSH.
        If stream=True, yield List[int] or List[Dict].
        """
        results = []
        for cmd in cmds:
            if stream:
                code = self.exec_command_stream(cmd, cwd=cwd, env=env)
                results.append(code)
                if code != 0:
                    break
            else:
                res = self.exec_command(cmd, cwd=cwd, env=env)
                results.append(res)
                if res["status"] != 0:
                    break
        return results

    def exec_command_stream(
        self,
        cmd: str,
        cwd: str = "",
        env: Optional[Dict[str, str]] = None,
        tail_lines: Optional[int] = None,
    ) -> int:
        """Execute a long‐running command, streaming stdout/stderr live,
        but only keep the last `tail_lines`."""
        client = self.connect()
        full_cmd = f"cd {cwd} && {cmd}" if cwd else cmd

        stdin, stdout, stderr = client.exec_command(
            full_cmd,
            timeout=None,
            get_pty=True,
            environment=env,
        )

        channel = stdout.channel
        # If tail_lines is set, buffer up to that many lines:
        buffer = deque(maxlen=tail_lines) if tail_lines else None

        while not channel.closed or channel.recv_ready() or channel.recv_stderr_ready():
            r, _, _ = select.select([channel], [], [], 1.0)
            if channel.recv_ready():
                chunk = channel.recv(1024).decode()
                for line in chunk.splitlines():
                    if buffer is not None:
                        buffer.append(("INFO", line))
                    else:
                        self.log.info(line)
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(1024).decode()
                for line in chunk.splitlines():
                    if buffer is not None:
                        buffer.append(("ERROR", line))
                    else:
                        self.log.error(line)

        exit_code = channel.recv_exit_status()

        # If we were buffering, now print only the last tail_lines
        if buffer is not None:
            self.log.info(f"--- Last {len(buffer)} lines of output ---")
            for level, line in buffer:
                if level == "INFO":
                    self.log.info(line)
                else:
                    self.log.error(line)

        return exit_code

    # quick helper → stdout only
    def out(self, cmd: str, **kw):
        return self.exec_command(cmd, **kw)["stdout"]

    # --------------------------------------------------------------------
    # (other methods: exec_command_stream, transfer_directory, open_shell,
    #  interactive_repl)  — unchanged from previous snippet, omitted here
    # --------------------------------------------------------------------

    def transfer_directory(self, local_path: str, remote_path: str) -> None:
        local_path = str(Path(local_path).resolve())
        if not os.path.isdir(local_path):
            raise VMOperationError(f"Local directory not found: {local_path}")

        sftp = self.get_sftp()
        self.exec_command(f"mkdir -p {remote_path}")
        for root, dirs, files in os.walk(local_path):
            rel = os.path.relpath(root, local_path)
            rel = "" if rel == "." else rel
            target_dir = Path(remote_path, rel).as_posix()
            try:
                sftp.mkdir(target_dir)
            except IOError:
                pass
            for fn in files:
                local_file = os.path.join(root, fn)
                remote_file = f"{target_dir}/{fn}"
                self.log.debug("SFTP: %s → %s", local_file, remote_file)
                sftp.put(local_file, remote_file)
        self.log.info("Transferred directory %s → %s", local_path, remote_path)

    def open_shell(self, term: str = "xterm") -> paramiko.Channel:
        chan = self.connect().invoke_shell(term=term)
        chan.settimeout(0.0)

        ipy = get_ipython()

        # fall back to sys.stdin in plain Python
        def _read_stdin():
            return (ipy.stdin if ipy else sys.stdin).read(1)

        def _writer():
            while not chan.closed:
                ch = _read_stdin()
                if not ch:
                    chan.shutdown_write()
                    break
                chan.send(ch)

        threading.Thread(target=_writer, daemon=True).start()

        try:
            while True:
                if chan.recv_ready():
                    sys.stdout.write(chan.recv(1024).decode())
                    sys.stdout.flush()
                time.sleep(0.05)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            chan.close()
        return chan

    def interactive_repl(self) -> None:
        """
        Jupyter‑friendly one‑line‑at‑a‑time REPL.

            >>> vm = QemuVMManager(...)
            >>> vm.get_or_create_container()
            >>> vm.repl()  # ← runs this method

        • TAB‑completion for remote files *and* executables
        • `cd …` keeps its effect for subsequent commands
        • leave with Ctrl‑D or the commands  `exit` / `quit`
        """
        # make sure the SSH connection is alive
        self.connect()
        console = Console()

        # ───────────────────────── completion helpers ─────────────────────────
        def _remote_listdir(path: str = ".") -> List[str]:
            res = self.exec_command(f"ls -1a {shq(path)}")
            return res["stdout"].splitlines() if res["status"] == 0 else []

        def _remote_commands() -> set[str]:
            if not hasattr(self, "_cmd_cache"):
                cmd = self.exec_command("IFS=:; for p in $PATH; do ls -1 $p 2>/dev/null; done")
                self._cmd_cache = set(cmd["stdout"].split())
            return self._cmd_cache

        class SSHCompleter(Completer):
            def get_completions(self, doc: Document, _):
                text = doc.text_before_cursor
                if not text.strip():
                    return
                token = shlex.split(text)[-1] if not text.endswith(" ") else ""
                # path completion if token contains /
                if "/" in token:
                    dirname = os.path.dirname(token) or "."
                    prefix = os.path.basename(token)
                    for f in _remote_listdir(dirname):
                        if f.startswith(prefix):
                            yield Completion(os.path.join(dirname, f), start_position=-len(token))
                else:  # otherwise suggest commands
                    for cmd in _remote_commands():
                        if cmd.startswith(token):
                            yield Completion(cmd, start_position=-len(token))

        session = PromptSession(
            message="[ssh-sandbox]$ ",
            completer=SSHCompleter(),
            complete_while_typing=True,
            multiline=False,
        )

        console.print("[bold green]Connected – press Ctrl‑D to exit[/]\n")

        current_dir = ""  # empty string → remote login shell’s default ($HOME)

        try:
            while True:
                # run prompt *inside* the Jupyter event loop
                try:
                    line = session.prompt(in_thread=True)
                except KeyboardInterrupt:
                    continue  # clear input line on Ctrl‑C
                except EOFError:  # Ctrl‑D
                    break

                line = line.strip()
                if not line:
                    continue
                if line in ("exit", "quit"):
                    break

                # ── local handling of `cd …` so it persists across commands ──
                tokens = shlex.split(line)
                if tokens and tokens[0] == "cd":
                    target = tokens[1] if len(tokens) > 1 else ""
                    if target in ("", "~"):
                        current_dir = ""  # let remote shell expand $HOME
                    elif target == "-":
                        # Optional: implement "cd -" (toggle) if you like
                        pass
                    elif target.startswith("/"):
                        current_dir = target  # absolute path
                    else:
                        current_dir = os.path.normpath(f"{current_dir}/{target}")
                    # (optional) verify directory exists – ignore errors
                    self.exec_command("true", cwd=current_dir)
                    console.print(current_dir or "~")
                    continue

                # ── normal command ──
                res = self.exec_command(line, cwd=current_dir)
                if res["stdout"]:
                    console.print(res["stdout"].rstrip(), highlight=False)
                if res["stderr"]:
                    console.print(res["stderr"].rstrip(), style="bold red")
                if res["status"] != 0:
                    console.print(f"[red]exit status {res['status']}[/]")

        finally:
            console.print("\n[bold yellow]Leaving SSH REPL[/]")


# ===========================================================================
# QemuVMManager – only NEW / CHANGED bits are commented
# ===========================================================================


class QemuVMManager:
    """High‑level wrapper that spins up a QEMU‑in‑Docker VM, keeps exactly **one**
    SSH connection to it and exposes notebook‑friendly helpers like

        vm = QemuVMManager(...)
        vm.get_or_create_container()   # boots or re‑uses the VM
        vm.exec("uname -a")            # run single command
        vm.shell()                     # raw interactive shell
        vm.repl()                      # colour REPL with TAB completion

    Requires: docker‑python, paramiko, rich, prompt‑toolkit.
    """

    def __init__(self, config: VMConfig, *, docker_client=None, logger=None, ssh_cfg: SSHConfig | None = None):
        self.cfg = config
        self.docker = docker_client or docker.from_env()
        self.log = logger or _setup_logger("QemuVMManager")
        self.ssh_cfg = ssh_cfg or SSHConfig(port=self.cfg.ssh_port)
        self.state = VMState.INITIALIZING
        self.container: Container | None = None
        self.instance_dir: Path | None = None
        self.on_state_change: Callable[[VMState], None] | None = None
        self._validate_cfg()

    # ------------ ONE PER‑INSTANCE SSH CLIENT ---------------------------
    @cached_property
    def ssh_client(self) -> SSHClient:
        return SSHClient(logger=self.log, config=self.ssh_cfg)

    # Convenience aliases -------------------------------------------------
    exec = property(lambda self: self.ssh_client.exec_command)
    run = property(lambda self: self.ssh_client.exec_commands)
    shell = property(lambda self: self.ssh_client.open_shell)
    repl = property(lambda self: self.ssh_client.interactive_repl)

    # --------------------------------------------------------------------
    # ... all helper methods (prepare_container_config, etc.) stay mostly
    # unchanged, **BUT** every previous call to `self.ssh_client()` is
    # now simply `self.ssh_client`.
    # --------------------------------------------------------------------
    def _validate_cfg(self) -> None:
        cfg = self.cfg

        # must have a base image directory
        if not cfg.vm_base_dir or not cfg.vm_base_dir.is_dir():
            raise VMCreationError(f"vm_base_dir must exist and be a directory (got {cfg.vm_base_dir!r})")

        # ensure instances_dir is writable
        if not os.access(cfg.instances_dir, os.W_OK):
            raise VMCreationError(f"instances_dir is not writable: {cfg.instances_dir!r}")

        # if you plan to share via 9p, host_shared_dir must exist
        if cfg.host_shared_dir and not cfg.host_shared_dir.is_dir():
            raise VMCreationError(f"host_shared_dir must exist if set (got {cfg.host_shared_dir!r})")

        # if you plan to deploy a server, check for start.sh
        if cfg.server_host_dir:
            start_sh = cfg.server_host_dir / "start.sh"
            if not start_sh.is_file():
                raise VMCreationError(f"server_host_dir must contain start.sh (checked {start_sh!r})")

        # port sanity
        ports = [cfg.vnc_port, cfg.ssh_port] + list(cfg.extra_ports.values())
        for p in ports:
            if not (1 <= p <= 65535):
                raise VMCreationError(f"Invalid port number: {p}")

        # caps/devices are strings
        if not all(isinstance(d, str) for d in cfg.devices):
            raise VMCreationError("All devices must be strings")
        if not all(isinstance(c, str) for c in cfg.capabilities):
            raise VMCreationError("All capabilities must be strings")

    def _set_state(self, new_state: VMState) -> None:
        old = self.state
        self.state = new_state
        self.log.info("VM state: %s -> %s", old.value, new_state.value)
        if self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception as e:
                self.log.error("State callback error: %s", e)

    def check_ssh_connection(self) -> bool:
        """Check if SSH connection is active and ready.

        Returns:
            bool: True if connection is active, False otherwise
        """
        if not self.ssh_client:
            return False

        return self.ssh_client._active()

    def wait_for_ssh_connection(self, timeout: int = 180, retry_interval: int = 5) -> bool:
        """Wait for SSH connection to be available.

        Args:
            timeout: Maximum time to wait in seconds
            retry_interval: Time between connection attempts in seconds

        Returns:
            bool: True if connection was established, False if timed out
        """
        self.log.info(f"Waiting for SSH connection to be available (timeout: {timeout}s)")

        start_time = time.time()
        attempt = 0
        while time.time() - start_time < timeout:
            attempt += 1
            try:
                # Try to get a connection - close any existing connection first
                self.ssh_client.close()

                # Try a simple command to verify connection
                result = self.ssh_client.exec_command("echo 'SSH connection test'")
                if result["status"] == 0:
                    elapsed = time.time() - start_time
                    self.log.info(f"SSH connection established after {elapsed:.1f}s ({attempt} attempts)")
                    return True

            except Exception as e:
                self.log.debug(f"SSH connection attempt {attempt} failed: {e}")

            # Only log every few attempts to avoid cluttering logs
            if attempt % 3 == 0:
                elapsed = time.time() - start_time
                self.log.info(f"Still waiting for SSH... ({elapsed:.1f}s elapsed, {attempt} attempts)")

            time.sleep(retry_interval)

        self.log.error(f"SSH connection timed out after {timeout}s")
        return False

    def _get_instance_dir(self) -> str:
        inst_dir = (self.cfg.instances_dir / self.cfg.container_name).resolve()
        _ensure_dir(inst_dir)
        try:
            shutil.copytree(self.cfg.vm_base_dir, inst_dir, dirs_exist_ok=True)
        except Exception as e:
            raise VMCreationError(f"Failed to copy base VM: {e}") from e
        return str(inst_dir)

    # Example of such a change (mount‑helper only):
    def _mount_shared_in_guest(self, host_shared: Path):
        if not self.cfg.host_shared_dir:
            return
        mount_point = f"/mnt/{host_shared.name}"
        self.cfg.runtime_env["SCREENSHOTS_PATH"] = mount_point

        cmds = [
            f"echo '{self.ssh_cfg.password}' | sudo -S mkdir -p {mount_point}",
            f"echo '{self.ssh_cfg.password}' | sudo -S mount -t 9p -o trans=virtio {self.cfg.guest_shared_dir.name} {mount_point}",
        ]
        self.run(cmds)

    def _deploy_server(self) -> None:
        cfg = self.cfg
        if not cfg.server_host_dir:
            return

        self.ssh_client.transfer_directory(cfg.server_host_dir.as_posix(), cfg.server_guest_dir.as_posix())

        # Log the runtime environment for debugging
        self.log.info(f"Deploying server with runtime environment: {self.cfg.runtime_env}")

        # Verify that SCREENSHOTS_PATH is set (critical for your script)
        if "SCREENSHOTS_PATH" not in self.cfg.runtime_env:
            self.log.warning("SCREENSHOTS_PATH not set in runtime environment!")
            if self.cfg.host_shared_dir:
                # Try to derive it from the mount point if not explicitly set
                name = Path(self.instance_dir).name if self.instance_dir else "default"
                mount_point = f"/mnt/{name}"
                self.log.info(f"Using derived SCREENSHOTS_PATH={mount_point}")
                self.cfg.runtime_env["SCREENSHOTS_PATH"] = mount_point

        # # Make start.sh executable and run it with the runtime environment
        # cmds = [f"chmod +x {cfg.server_guest_dir}/start.sh", f"cd {cfg.server_guest_dir} && ./start.sh"]

        # # stream logs live
        # exit_codes = self.ssh_client.exec_commands(cmds, stream=True, env=self.cfg.runtime_env)
        # if any(code != 0 for code in exit_codes):
        #     raise VMOperationError(f"Server startup failed with codes {exit_codes}")

        # self.log.info("Server started in guest at %s", cfg.server_guest_dir)

    # --------------------------------------------------------------------
    # start_container – prime SSH connection once the VM is RUNNING
    # --------------------------------------------------------------------
    def start_container(self):
        if not self.container:
            raise VMOperationError("container missing")
        self.state = VMState.STARTING
        self.container.start()
        self.log.info("container started – waiting for SSH")
        if not self.wait_for_ssh_connection():
            self.state = VMState.ERROR
            raise VMOperationError("SSH never became ready")
        self.state = VMState.RUNNING
        # first (lazy) connection so user can use ssh_client immediately
        try:
            self.ssh_client.connect()
        except SSHError as e:
            self.log.warning("VM up but SSH handshake failed: %s", e)

    # --------------------------------------------------------------------
    # (all remaining manager methods are identical to previous version
    #  except that they reference self.ssh_client instead of the removed
    #  get_ssh_client())
    # --------------------------------------------------------------------
    def prepare_container_config(self) -> Dict[str, Any]:
        """Prepare Docker container configuration without creating it."""
        cfg = self.cfg
        self.instance_dir = self._get_instance_dir()
        name = Path(self.instance_dir).name
        self.log.info("Preparing container configuration for %s", name)

        mounts = [Mount("/storage", self.instance_dir, type="bind")]

        if cfg.host_shared_dir:
            host_shared = (cfg.host_shared_dir / name).resolve()
            _ensure_dir(host_shared)
            mounts.append(Mount(cfg.guest_shared_dir.as_posix(), host_shared.as_posix(), type="bind"))

        if not self.ensure_image_exists(cfg.container_image):
            raise VMCreationError(f"Image {cfg.container_image} missing")

        vm_env = {
            "BOOT": cfg.vm_boot_image,
            "DEBUG": "Y" if cfg.enable_debug else "N",
            "RAM_SIZE": cfg.vm_ram,
            "CPU_CORES": str(cfg.vm_cpu_cores),
            "DISK_SIZE": cfg.vm_disk_size,
            **cfg.extra_env,
        }

        ports = {
            "8006/tcp": cfg.vnc_port,
            "22/tcp": cfg.ssh_port,
            **cfg.extra_ports,
        }

        return {
            "image": cfg.container_image,
            "name": name,
            "environment": vm_env,
            "devices": cfg.devices,
            "cap_add": cfg.capabilities,
            "ports": ports,
            "mounts": mounts,
            "restart_policy": {"Name": cfg.restart_policy},
            "detach": True,
        }

    def get_container(self, name: Optional[str] = None) -> Optional[Container]:
        """Get a container by name if it exists.

        Args:
            name: Name of the container to retrieve (uses instance name if None)

        Returns:
            Container object if found, None otherwise
        """
        container_name = name or (Path(self.instance_dir).name if self.instance_dir else self.cfg.container_name)

        try:
            return self.docker.containers.get(container_name)
        except docker.errors.NotFound:
            return None
        except Exception as e:
            self.log.error(f"Error getting container {container_name}: {e}")
            return None

    def create_container(
        self, start_immediately: bool = False, reuse_existing: bool = True, recreate_if_exists: bool = False
    ) -> Container:
        """Create Docker container with prepared configuration or reuse existing.

        Args:
            start_immediately: Whether to start the container after creation
            reuse_existing: Whether to reuse existing container if it exists
            recreate_if_exists: Whether to recreate container if it exists (overrides reuse_existing)

        Returns:
            Container object
        """
        self._set_state(VMState.CREATING)

        try:
            container_config = self.prepare_container_config()
            name = container_config["name"]

            # Check if container already exists
            existing_container = self.get_container(name)

            if existing_container:
                self.log.info(f"Container {name} already exists")

                if recreate_if_exists:
                    self.log.info(f"Recreating container {name}")
                    existing_container.remove(force=True)
                    container = self.docker.containers.create(**container_config)
                elif reuse_existing:
                    self.log.info(f"Reusing existing container {name}")
                    container = existing_container
                else:
                    raise VMCreationError(f"Container {name} already exists and reuse_existing=False")
            else:
                # Container doesn't exist, create a new one
                container = self.docker.containers.create(**container_config)
                self.log.info(f"Created new container {name}")

            self.container = container

            if start_immediately:
                self.start_container()

            return container

        except Exception as e:
            self._set_state(VMState.ERROR)
            self.log.error(f"Error creating container: {e}")
            raise VMCreationError(f"Create failed: {e}") from e

    def get_or_create_container(
        self, start_if_stopped: bool = True, recreate: bool = False, setup_if_needed: bool = True
    ) -> Container:
        """Get existing container or create a new one, ensuring the guest VM is fully set up.

        Args:
            start_if_stopped: Whether to start the container if it exists but is stopped
            recreate: Whether to recreate the container if it exists
            setup_if_needed: Whether to set up the guest VM if the container is started

        Returns:
            Container object
        """
        # Prepare the config to ensure instance_dir is set
        if not self.instance_dir:
            _ = self.prepare_container_config()

        name = Path(self.instance_dir).name

        # Try to get existing container
        container = self.get_container(name)
        vm_was_running = False

        if container:
            self.container = container

            if recreate:
                self.log.info(f"Recreating container {name}")
                container.remove(force=True)
                container = self.create_container()
                # New container always needs to be started and set up
                self.start_container()
                if setup_if_needed:
                    self.setup_guest_vm()
                return container

            self.log.info(f"Reusing container {name}")
            vm_was_running = container.status == "running"

            if start_if_stopped and not vm_was_running:
                self.log.info(f"Starting stopped container {name}")
                self.start_container()
                # Container was stopped, so we need to set up the guest VM
                if setup_if_needed:
                    self.setup_guest_vm()
            elif vm_was_running:
                # Container was already running, update our state
                self._set_state(VMState.RUNNING)

                # Check if VM is properly set up by testing SSH connection
                if setup_if_needed and self.check_server_running():
                    self.log.info("VM is already running with server")
                elif setup_if_needed:
                    self.log.info("VM is running but server needs setup")
                    self.setup_guest_vm()

            return container

        # Container doesn't exist, create a new one
        container = self.create_container()

        if start_if_stopped:
            self.start_container()
            if setup_if_needed:
                self.setup_guest_vm()

        return container

    def check_server_running(self) -> bool:
        """Check if the server is running in the guest VM.

        Returns:
            bool: True if server is running, False otherwise
        """
        if not self.check_ssh_connection():
            return False

        try:
            # Simple check to see if server process is running
            result = self.ssh_client.exec_command("pgrep -f 'uv run main.py'", cwd="/home/user/server")
            return result["status"] == 0
        except Exception as e:
            self.log.debug(f"Failed to check if server is running: {e}")
            return False

    def setup_guest_vm(self) -> None:
        """Configure guest VM after it's running and SSH is available."""
        if self.state != VMState.RUNNING:
            raise VMOperationError("VM must be in RUNNING state to setup guest")

        try:
            # Set up the environment for the guest VM
            if self.cfg.host_shared_dir:
                name = Path(self.instance_dir).name
                host_shared = (self.cfg.host_shared_dir / name).resolve()
                self._mount_shared_in_guest(host_shared)

            # Deploy the server if configured
            self._deploy_server()

            self.log.info("Guest VM setup completed successfully")

        except Exception as e:
            self.log.error("Failed to setup guest VM: %s", e)
            raise VMOperationError(f"Guest setup failed: {e}") from e

    def create_and_setup_vm(self) -> Container:
        """Convenience method to create, start and setup VM in one go."""
        container = self.create_container(start_immediately=False)
        self.start_container()
        self.setup_guest_vm()
        return container

    def reset_container(
        self,
        *,
        preserve_storage: bool = False,
        recreate_image: bool = False,
    ) -> Container:
        """
        Fully reset the VM container and start fresh.

        Parameters
        ----------
        preserve_storage : bool, default False
            If *True*, keep the per‑instance storage directory on the host
            (faster when you only need a clean guest OS boot).
        recreate_image : bool, default False
            If *True*, force a fresh `docker pull` of ``cfg.container_image`` before
            recreating the container.

        Returns
        -------
        docker.models.containers.Container
            The NEW, running container.

        Notes
        -----
        • Works even if the previous container is already stopped or half‑broken.
        • On failure, leaves the manager in *ERROR* state and re‑raises.
        """
        self.log.info("Resetting VM container (preserve_storage=%s)…", preserve_storage)

        # 1) shut down everything we know about
        try:
            # close SSH first so we don't block on a dead channel
            if "ssh_client" in self.__dict__:
                self.ssh_client.close()
                del self.__dict__["ssh_client"]
        except Exception as e:
            self.log.warning("Error closing SSH client: %s", e)

        # stop & remove the container and, optionally, the disk image
        try:
            self.cleanup_vm(delete_storage=not preserve_storage)
        except Exception as e:
            self.log.warning("Error during cleanup: %s (continuing anyway)", e)

        # 2) (optional) refresh the Docker image
        if recreate_image:
            try:
                self.log.info("Pulling fresh image %s", self.cfg.container_image)
                self.docker.api.pull(self.cfg.container_image, stream=True)
            except Exception as e:
                self.log.warning("Failed to pull image: %s (continuing with local copy)", e)

        # 3) create, boot and configure a brand‑new VM
        try:
            container = self.create_container(start_immediately=False, reuse_existing=False)
            self.start_container()
            self.setup_guest_vm()
            return container
        except Exception as e:
            self._set_state(VMState.ERROR)
            self.log.error("Failed to reset VM: %s", e)
            raise

    def ensure_image_exists(self, image_name: Optional[str] = None) -> bool:
        image_name = image_name or self.cfg.container_image
        try:
            self.docker.images.get(image_name)
            return True
        except docker.errors.ImageNotFound:
            self.log.info("Pulling image %s", image_name)
            self.docker.api.pull(image_name, stream=True)
            return True
        except Exception:
            return False

    def cleanup_vm(self, delete_storage: bool = True) -> None:
        if self.container:
            self.log.info("Stopping/removing container %s", self.cfg.container_name)
            try:
                self.container.stop(timeout=30)
                self.container.remove()
            except Exception as e:
                self.log.error("Cleanup error: %s", e)
            self.container = None
        if delete_storage and self.instance_dir:
            try:
                shutil.rmtree(self.instance_dir)
                self.log.info("Deleted storage %s", self.instance_dir)
            except Exception as e:
                self.log.error("Failed to delete storage %s: %s", self.instance_dir, e)
            self.instance_dir = None

    def stop_and_cleanup(self) -> None:
        """Stop the VM and clean up resources including SSH connections."""

        if "ssh_client" in self.__dict__:
            self.ssh_client.close()
            del self.__dict__["ssh_client"]

        self.cleanup_vm(delete_storage=True)
        self._set_state(VMState.STOPPED)

    def ssh_shell(self, term: str = "xterm"):
        """
        Start an interactive shell to the guest VM.
        Intended to be called from a Jupyter‑style environment.

        Example
        -------
        >>> vm = QemuVMManager(...)
        >>> vm.get_or_create_container(setup_if_needed=False)
        >>> vm.ssh_shell()  # drops you into a live shell
        """
        if self.state != VMState.RUNNING:
            raise VMOperationError("VM is not running – start it first")

        return self.ssh_client.open_shell(term=term)

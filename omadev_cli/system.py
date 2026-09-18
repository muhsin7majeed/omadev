"""Thin, timeout-guarded access to the outside world.

Every subprocess the CLI runs goes through a Runner, so tests can substitute
a fake and assert exactly which commands would run. Nothing here uses a
shell: commands are argv lists.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

DEFAULT_TIMEOUT = 10.0
PROBE_TIMEOUT = 0.5


class ToolMissing(Exception):
    """A required executable is not installed."""

    def __init__(self, tool: str) -> None:
        super().__init__(f"'{tool}' is not installed or not on PATH")
        self.tool = tool


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def message(self) -> str:
        """The most useful one-line explanation of a failure."""
        text = (self.stderr or self.stdout).strip()
        return text.splitlines()[-1] if text else f"exit code {self.returncode}"


class Runner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT, cwd: Path | None = None) -> CommandResult: ...

    def detach(self, argv: Sequence[str], *, cwd: Path | None = None) -> None: ...

    def has(self, tool: str) -> bool: ...


class SystemRunner:
    """Runs real processes. Never a shell, always a timeout."""

    def run(self, argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT, cwd: Path | None = None) -> CommandResult:
        args = tuple(argv)
        try:
            completed = subprocess.run(
                list(args), capture_output=True, text=True, timeout=timeout,
                cwd=cwd, check=False, stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ToolMissing(args[0]) from exc
        except subprocess.TimeoutExpired as exc:
            return CommandResult(args, 124, _text(exc.stdout), f"timed out after {timeout:g}s")
        return CommandResult(args, completed.returncode, completed.stdout, completed.stderr)

    def detach(self, argv: Sequence[str], *, cwd: Path | None = None) -> None:
        """Start a process that outlives this CLI, in its own session."""
        args = list(argv)
        try:
            subprocess.Popen(
                args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise ToolMissing(args[0]) from exc

    def has(self, tool: str) -> bool:
        return shutil.which(tool) is not None


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def gui_argv(runner: Runner, argv: Sequence[str]) -> list[str]:
    """Wrap a graphical launch the way Omarchy does, so the app lands in the
    session scope instead of dying with this CLI. Omarchy's own launchers
    already do this themselves."""
    args = list(argv)
    if args and args[0].startswith("omarchy-"):
        return args
    if runner.has("uwsm-app"):
        return ["uwsm-app", "--", *args]
    return args


def port_open(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """True if something accepts TCP connections at host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(
    host: str,
    port: int,
    timeout: float,
    *,
    interval: float = 0.5,
    probe: Callable[[str, int], bool] = port_open,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll until host:port accepts connections or `timeout` seconds pass."""
    deadline = clock() + timeout
    while True:
        if probe(host, port):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def list_processes(proc_root: Path = Path("/proc")) -> list[tuple[int, list[str]]]:
    """(pid, argv) for every process visible under /proc."""
    found: list[tuple[int, list[str]]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        if argv:
            found.append((int(entry.name), argv))
    return found


def parent_pid(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    try:
        status = (proc_root / str(pid) / "status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("PPid:"):
            value = line.split(":", 1)[1].strip()
            return int(value) if value.isdigit() else None
    return None

"""Thin, timeout-guarded access to the outside world.

Every subprocess the CLI runs goes through a Runner, so tests can substitute
a fake and assert exactly which commands would run. Nothing here uses a
shell: commands are argv lists.
"""

from __future__ import annotations

import shlex
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

DEFAULT_TIMEOUT = 10.0
PROBE_TIMEOUT = 0.5
HTTP_TIMEOUT = 2.0


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Treat a redirect as an answer instead of following it.

    A dev server may redirect to a login page or, misconfigured, to an
    external site; the readiness check must never fetch beyond the URL it
    was given.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 (urllib signature)
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def http_ok(url: str, timeout: float = HTTP_TIMEOUT) -> bool:
    """True if the URL answers HTTP at all, whatever the status code.

    A TCP connect is not enough for docker projects: the proxy binds the
    port the moment the container starts, long before the app inside can
    answer. Any HTTP response, error statuses and redirects included, means
    the app is up. Redirects are not followed.
    """
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "omadev"})
    try:
        with _opener.open(request, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_until(
    condition: Callable[[], bool],
    timeout: float,
    *,
    interval: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll `condition` until it holds or `timeout` seconds pass."""
    deadline = clock() + timeout
    while True:
        if condition():
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


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
    return wait_until(lambda: probe(host, port), timeout, interval=interval, clock=clock, sleep=sleep)


def wait_for_http(url: str, timeout: float, *, interval: float = 0.5) -> bool:
    """Poll until the URL answers HTTP or `timeout` seconds pass."""
    return wait_until(lambda: http_ok(url), timeout, interval=interval)


def split_command(text: str) -> list[str]:
    """Turn a command string from the config into argv, without a shell.

    Quoting works as in a POSIX shell; pipes, redirects and variables do not.
    """
    argv = shlex.split(text)
    if not argv:
        raise ValueError("empty command")
    return argv


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

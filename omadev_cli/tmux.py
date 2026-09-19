"""Drive tmux for projects that use it instead of herdr.

A project maps to one tmux session. Each long-running command gets its own
window in that session, named `omadev-<command>`, mirroring the herdr layout.
"""

from __future__ import annotations

import re
from pathlib import Path

from .system import CommandResult, Runner

WINDOW_PREFIX = "omadev-"
TMUX_TIMEOUT = 5.0
SHELLS = frozenset({"bash", "zsh", "fish", "sh", "dash", "nu", "elvish"})


class TmuxError(Exception):
    """tmux is unavailable or a command failed."""


def session_name(project_name: str) -> str:
    """tmux forbids '.' and ':' in session names; swap them for '_'."""
    return re.sub(r"[.:]", "_", project_name)


def window_name(command_name: str) -> str:
    return WINDOW_PREFIX + command_name


def attach_argv(session: str) -> list[str]:
    """Open a terminal attached to the session, creating it if needed."""
    return ["omarchy-launch-terminal", "tmux", "new-session", "-A", "-s", session]


class Tmux:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def available(self) -> bool:
        return self.runner.has("tmux")

    def _run(self, *args: str, check: bool = True) -> CommandResult:
        result = self.runner.run(["tmux", *args], timeout=TMUX_TIMEOUT)
        if check and not result.ok:
            raise TmuxError(f"tmux {args[0]} failed: {result.message}")
        return result

    # --------------------------------------------------------------- sessions

    def has_session(self, session: str) -> bool:
        # The '=' prefix asks for an exact name instead of a prefix match.
        return self._run("has-session", "-t", f"={session}", check=False).ok

    def new_session(self, session: str, cwd: Path) -> None:
        self._run("new-session", "-d", "-s", session, "-c", str(cwd))

    def kill_session(self, session: str) -> None:
        self._run("kill-session", "-t", f"={session}")

    def client_pids(self, session: str) -> list[int]:
        result = self._run("list-clients", "-t", f"={session}", "-F", "#{client_pid}", check=False)
        if not result.ok:
            return []
        return [int(line) for line in result.stdout.split() if line.isdigit()]

    # ---------------------------------------------------------------- windows

    def windows(self, session: str) -> list[tuple[str, str]]:
        """(target, name) for every window in the session."""
        result = self._run("list-windows", "-t", f"={session}", "-F", "#{session_name}:#{window_index}\t#{window_name}")
        pairs = []
        for line in result.stdout.splitlines():
            target, _, name = line.partition("\t")
            if target:
                pairs.append((target, name))
        return pairs

    def find_window(self, session: str, name: str) -> str | None:
        for target, window in self.windows(session):
            if window == name:
                return target
        return None

    def new_window(self, session: str, name: str, cwd: Path, env: tuple[str, ...] = ()) -> str:
        """Create a detached window and return its target. `env` entries are
        KEY=VALUE for the new window's shell (tmux 3.2+ `-e`)."""
        args = ["new-window", "-d", "-t", f"={session}:", "-n", name, "-c", str(cwd)]
        for entry in env:
            args += ["-e", entry]
        result = self._run(*args, "-P", "-F", "#{session_name}:#{window_index}")
        target = result.stdout.strip()
        if not target:
            raise TmuxError("tmux new-window did not report the new window")
        return target

    def kill_window(self, target: str) -> None:
        self._run("kill-window", "-t", target)

    def select_window(self, target: str) -> None:
        self._run("select-window", "-t", target)

    # ------------------------------------------------------------------ panes

    def current_command(self, target: str) -> str:
        return self._run("display-message", "-p", "-t", target, "#{pane_current_command}").stdout.strip()

    def idle(self, target: str) -> bool:
        return self.current_command(target) in SHELLS

    def run(self, target: str, command: str) -> None:
        """Type `command` literally into the window and press Enter."""
        self._run("send-keys", "-t", target, "-l", command)
        self._run("send-keys", "-t", target, "Enter")

    def send_keys(self, target: str, *keys: str) -> None:
        self._run("send-keys", "-t", target, *keys)

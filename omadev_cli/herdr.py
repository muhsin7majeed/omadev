"""Drive herdr through its CLI, which answers in JSON over the socket API.

A project maps to one herdr workspace, found by label. Each long-running
command gets its own tab inside that workspace, labeled `omadev-<command>`,
so it can be found again and so nothing is ever typed into a pane the user
is working in.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .system import Runner

TAB_PREFIX = "omadev-"
HERDR_TIMEOUT = 10.0
SHELLS = frozenset({"bash", "zsh", "fish", "sh", "dash", "nu", "elvish"})


class HerdrError(Exception):
    """herdr is unavailable, failed, or answered in an unexpected shape."""


@dataclass(frozen=True)
class Workspace:
    id: str
    label: str


@dataclass(frozen=True)
class Tab:
    id: str
    workspace_id: str
    label: str


@dataclass(frozen=True)
class Pane:
    id: str
    tab_id: str
    workspace_id: str
    cwd: str


@dataclass(frozen=True)
class Foreground:
    """What a pane is running right now."""

    shell_pid: int | None
    processes: tuple[str, ...]

    @property
    def idle(self) -> bool:
        """True when only a shell prompt is waiting in the pane."""
        return all(name in SHELLS for name in self.processes)

    @property
    def summary(self) -> str:
        return ", ".join(self.processes) if self.processes else "nothing"


def tab_label(command_name: str) -> str:
    return TAB_PREFIX + command_name


def client_pids(processes: Iterable[tuple[int, list[str]]]) -> list[int]:
    """PIDs of interactive herdr clients, excluding the server."""
    pids = []
    for pid, argv in processes:
        if os.path.basename(argv[0]) != "herdr":
            continue
        if len(argv) > 1 and argv[1] == "server":
            continue
        pids.append(pid)
    return pids


def attach_argv() -> list[str]:
    """Open a terminal attached to the persistent herdr session."""
    return ["omarchy-launch-terminal", "herdr"]


class Herdr:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def available(self) -> bool:
        return self.runner.has("herdr")

    def _call(self, *args: str) -> dict:
        result = self.runner.run(["herdr", *args], timeout=HERDR_TIMEOUT)
        if not result.ok:
            raise HerdrError(f"herdr {' '.join(args[:2])} failed: {result.message}")
        if not result.stdout.strip():
            return {}
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HerdrError(f"herdr {' '.join(args[:2])} returned something that is not JSON") from exc
        payload = data.get("result") if isinstance(data, dict) else None
        return payload if isinstance(payload, dict) else {}

    # ----------------------------------------------------------------- server

    def server_running(self) -> bool:
        try:
            self._call("workspace", "list")
        except HerdrError:
            return False
        return True

    # ------------------------------------------------------------- workspaces

    def workspaces(self) -> list[Workspace]:
        items = self._call("workspace", "list").get("workspaces", [])
        return [Workspace(id=str(w["workspace_id"]), label=str(w.get("label", ""))) for w in items if "workspace_id" in w]

    def find_workspace(self, label: str) -> Workspace | None:
        for workspace in self.workspaces():
            if workspace.label == label:
                return workspace
        return None

    def create_workspace(self, cwd: Path, label: str) -> Workspace:
        payload = self._call("workspace", "create", "--cwd", str(cwd), "--label", label, "--no-focus")
        workspace = payload.get("workspace")
        if not isinstance(workspace, dict) or "workspace_id" not in workspace:
            raise HerdrError("herdr workspace create did not return a workspace id")
        return Workspace(id=str(workspace["workspace_id"]), label=label)

    def focus_workspace(self, workspace_id: str) -> None:
        self._call("workspace", "focus", workspace_id)

    def close_workspace(self, workspace_id: str) -> None:
        self._call("workspace", "close", workspace_id)

    # ------------------------------------------------------------------- tabs

    def tabs(self, workspace_id: str) -> list[Tab]:
        items = self._call("tab", "list", "--workspace", workspace_id).get("tabs", [])
        return [
            Tab(id=str(t["tab_id"]), workspace_id=str(t.get("workspace_id", workspace_id)), label=str(t.get("label", "")))
            for t in items if "tab_id" in t
        ]

    def find_tab(self, workspace_id: str, label: str) -> Tab | None:
        for tab in self.tabs(workspace_id):
            if tab.label == label:
                return tab
        return None

    def create_tab(self, workspace_id: str, cwd: Path, label: str) -> tuple[Tab, str]:
        """Create a tab and return it with the id of its root pane."""
        payload = self._call("tab", "create", "--workspace", workspace_id, "--cwd", str(cwd), "--label", label, "--no-focus")
        tab = payload.get("tab")
        pane = payload.get("root_pane")
        if not isinstance(tab, dict) or "tab_id" not in tab or not isinstance(pane, dict) or "pane_id" not in pane:
            raise HerdrError("herdr tab create did not return a tab and root pane")
        return Tab(id=str(tab["tab_id"]), workspace_id=workspace_id, label=label), str(pane["pane_id"])

    def close_tab(self, tab_id: str) -> None:
        self._call("tab", "close", tab_id)

    # ------------------------------------------------------------------ panes

    def panes(self, workspace_id: str) -> list[Pane]:
        items = self._call("pane", "list", "--workspace", workspace_id).get("panes", [])
        return [
            Pane(id=str(p["pane_id"]), tab_id=str(p.get("tab_id", "")), workspace_id=str(p.get("workspace_id", workspace_id)), cwd=str(p.get("cwd", "")))
            for p in items if "pane_id" in p
        ]

    def first_pane(self, workspace_id: str, tab_id: str) -> Pane | None:
        for pane in self.panes(workspace_id):
            if pane.tab_id == tab_id:
                return pane
        return None

    def foreground(self, pane_id: str) -> Foreground:
        info = self._call("pane", "process-info", "--pane", pane_id).get("process_info", {})
        shell_pid = info.get("shell_pid")
        processes = tuple(
            str(p.get("name", "")) for p in info.get("foreground_processes", []) if isinstance(p, dict)
        )
        return Foreground(shell_pid=shell_pid if isinstance(shell_pid, int) else None, processes=processes)

    def run(self, pane_id: str, command: str) -> None:
        """Type `command` into the pane and press Enter, atomically."""
        self._call("pane", "run", pane_id, command)

    def send_keys(self, pane_id: str, *keys: str) -> None:
        self._call("pane", "send-keys", pane_id, *keys)

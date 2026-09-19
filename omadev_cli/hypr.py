"""Read and act on Hyprland windows through hyprctl.

Window lookup is how Start decides that an editor, browser or helper app is
already open and should be focused rather than launched again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .system import Runner, parent_pid

HYPRCTL_TIMEOUT = 5.0


class HyprError(Exception):
    """hyprctl failed or returned something unexpected."""


@dataclass(frozen=True)
class Window:
    address: str
    cls: str
    initial_class: str
    title: str
    initial_title: str
    pid: int
    workspace: str

    @property
    def label(self) -> str:
        """Short description for step results and logs."""
        return f"{self.cls} '{self.title}'" if self.title else self.cls


def clients(runner: Runner) -> list[Window]:
    """Every mapped window, from `hyprctl clients -j`."""
    result = runner.run(["hyprctl", "clients", "-j"], timeout=HYPRCTL_TIMEOUT)
    if not result.ok:
        raise HyprError(f"hyprctl clients failed: {result.message}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HyprError("hyprctl clients returned something that is not JSON") from exc
    if not isinstance(data, list):
        raise HyprError("hyprctl clients returned something that is not a list")
    return [_window(item) for item in data if isinstance(item, dict)]


def _window(item: dict) -> Window:
    workspace = item.get("workspace")
    pid = item.get("pid")
    return Window(
        address=str(item.get("address", "")),
        cls=str(item.get("class", "")),
        initial_class=str(item.get("initialClass", "")),
        title=str(item.get("title", "")),
        initial_title=str(item.get("initialTitle", "")),
        pid=int(pid) if isinstance(pid, int) else 0,
        workspace=str(workspace.get("name", "")) if isinstance(workspace, dict) else "",
    )


def class_matches(window: Window, pattern: str) -> bool:
    """Case-insensitive regex search over the window class only."""
    regex = re.compile(pattern, re.IGNORECASE)
    return bool(regex.search(window.cls) or regex.search(window.initial_class))


def matches(window: Window, pattern: str) -> bool:
    """Case-insensitive regex search over class and title, current and initial."""
    regex = re.compile(pattern, re.IGNORECASE)
    return any(regex.search(field) for field in (window.cls, window.initial_class, window.title, window.initial_title))


def title_has_word(window: Window, word: str) -> bool:
    """True if `word` appears in the title as a whole word.

    Editors put the project folder name in the title in different places:
    Zed as "kadha — file", VS Code as "file - kadha - Visual Studio Code".
    A whole-word search covers both without matching "kadha-old".
    """
    regex = re.compile(r"(?<![\w.-])" + re.escape(word) + r"(?![\w.-])")
    return bool(regex.search(window.title) or regex.search(window.initial_title))


def first(windows: Iterable[Window], predicate) -> Window | None:
    for window in windows:
        if predicate(window):
            return window
    return None


def _dispatch(runner: Runner, lua: str, classic: list[str], what: str) -> None:
    """Run a dispatcher the way Omarchy's scripts do: the Lua form first,
    because Hyprland with a Lua config rejects the classic syntax, then the
    classic form for Hyprland builds without Lua."""
    result = runner.run(["hyprctl", "dispatch", lua], timeout=HYPRCTL_TIMEOUT)
    if result.ok and result.stdout.strip() == "ok":
        return
    fallback = runner.run(["hyprctl", "dispatch", *classic], timeout=HYPRCTL_TIMEOUT)
    if fallback.ok and fallback.stdout.strip() == "ok":
        return
    raise HyprError(f"could not {what}: {fallback.message}")


def focus(runner: Runner, window: Window) -> None:
    address = f"address:{window.address}"
    _dispatch(runner, f'hl.dsp.focus({{ window = "{address}" }})', ["focuswindow", address], f"focus {window.label}")


def close(runner: Runner, window: Window) -> None:
    address = f"address:{window.address}"
    # Window dispatchers live under hl.dsp.window in Hyprland's Lua API;
    # focus is the exception and sits at the top level.
    _dispatch(runner, f'hl.dsp.window.close({{ window = "{address}" }})', ["closewindow", address], f"close {window.label}")


def move_to_workspace(runner: Runner, window: Window, workspace: int) -> None:
    """Move a window to a workspace without switching to it."""
    address = f"address:{window.address}"
    _dispatch(
        runner,
        f'hl.dsp.window.move({{ window = "{address}", workspace = {int(workspace)}, silent = true }})',
        ["movetoworkspacesilent", f"{int(workspace)},{address}"],
        f"move {window.label} to workspace {workspace}",
    )


def app_id(*parts: str) -> str:
    """A window class for a terminal window omadev opens itself, so it can be
    found again: `omadev.<project>.<name>`, lower-case, safe characters only."""
    safe = [re.sub(r"[^a-z0-9_-]+", "-", part.lower()).strip("-") or "x" for part in parts]
    return "omadev." + ".".join(safe)


def window_for_pid(windows: Iterable[Window], pid: int, *, proc_root: Path = Path("/proc"), max_depth: int = 16) -> Window | None:
    """The window owned by `pid` or by one of its ancestors.

    A herdr or tmux client runs inside a shell inside a terminal; the
    terminal is the process Hyprland knows about, a few parents up.
    """
    by_pid = {window.pid: window for window in windows if window.pid > 0}
    current = pid
    for _ in range(max_depth):
        if current in by_pid:
            return by_pid[current]
        parent = parent_pid(current, proc_root)
        if parent is None or parent <= 1:
            return None
        current = parent
    return None

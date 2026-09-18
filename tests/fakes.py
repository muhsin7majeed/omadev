"""Test doubles for everything that touches the machine."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from omadev_cli import hypr, steps
from omadev_cli.herdr import Herdr
from omadev_cli.system import CommandResult, ToolMissing
from omadev_cli.tmux import Tmux

Responder = Callable[[tuple[str, ...]], CommandResult | None]


class FakeRunner:
    """Answers commands from registered prefixes and records every call.

    Unregistered commands raise, so a test fails loudly the moment the code
    under test runs something the test did not anticipate.
    """

    def __init__(self, tools: Sequence[str] = ("herdr", "tmux", "hyprctl", "uwsm-app")) -> None:
        self.tools = set(tools)
        self.calls: list[tuple[str, ...]] = []
        self.detached: list[tuple[str, ...]] = []
        self._responders: list[Responder] = []

    def on(self, *prefix: str, stdout: str = "", returncode: int = 0, stderr: str = "") -> "FakeRunner":
        def respond(argv: tuple[str, ...]) -> CommandResult | None:
            if argv[: len(prefix)] == prefix:
                return CommandResult(argv, returncode, stdout, stderr)
            return None

        self._responders.append(respond)
        return self

    def on_json(self, *prefix: str, result: Any) -> "FakeRunner":
        """Answer like herdr does: {"id": ..., "result": {...}}."""
        return self.on(*prefix, stdout=json.dumps({"id": "test", "result": result}))

    def respond_with(self, responder: Responder) -> "FakeRunner":
        """Register a custom responder for stateful answers."""
        self._responders.append(responder)
        return self

    def run(self, argv: Sequence[str], *, timeout: float = 10.0, cwd: Path | None = None) -> CommandResult:
        args = tuple(argv)
        self.calls.append(args)
        if args[0] not in self.tools:
            raise ToolMissing(args[0])
        for respond in reversed(self._responders):
            result = respond(args)
            if result is not None:
                return result
        raise AssertionError(f"unexpected command: {' '.join(args)}")

    def detach(self, argv: Sequence[str], *, cwd: Path | None = None) -> None:
        args = tuple(argv)
        if args[0] not in self.tools and not args[0].startswith("omarchy-") and args[0] != "xdg-open":
            raise ToolMissing(args[0])
        self.detached.append(args)

    def has(self, tool: str) -> bool:
        return tool in self.tools

    def mutating_calls(self) -> list[tuple[str, ...]]:
        """Calls that would change something: anything but list/get/probe commands."""
        read_only = {("hyprctl", "clients"), ("herdr", "workspace", "list"), ("herdr", "tab", "list"),
                     ("herdr", "pane", "list"), ("herdr", "pane", "process-info"), ("tmux", "has-session"),
                     ("tmux", "list-windows"), ("tmux", "list-clients"), ("tmux", "display-message")}
        return [c for c in self.calls if not any(c[: len(prefix)] == prefix for prefix in read_only)]


def window(**overrides: Any) -> hypr.Window:
    data: dict[str, Any] = {"address": "0x1", "cls": "", "initial_class": "", "title": "", "initial_title": "", "pid": 0, "workspace": "1"}
    data.update(overrides)
    return hypr.Window(**data)


@dataclass
class FakeServices:
    """Builds a steps.Services with fakes and knobs for ports and windows."""

    runner: FakeRunner = field(default_factory=FakeRunner)
    open_ports: set[int] = field(default_factory=set)
    ports_after_wait: set[int] = field(default_factory=set)
    windows: list[hypr.Window] = field(default_factory=list)
    processes: list[tuple[int, list[str]]] = field(default_factory=list)
    proc_root: Path = Path("/nonexistent-proc")
    waited: list[tuple[str, int, float]] = field(default_factory=list)
    now: float = 0.0

    def probe(self, host: str, port: int) -> bool:
        return port in self.open_ports

    def wait(self, host: str, port: int, timeout: float) -> bool:
        self.waited.append((host, port, timeout))
        self.open_ports |= self.ports_after_wait
        return port in self.open_ports

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def clock(self) -> float:
        return self.now

    def build(self) -> steps.Services:
        return steps.Services(
            runner=self.runner,
            herdr=Herdr(self.runner),
            tmux=Tmux(self.runner),
            probe=self.probe,
            wait=self.wait,
            processes=lambda: list(self.processes),
            windows=lambda: list(self.windows),
            proc_root=self.proc_root,
            sleep=self.sleep,
            clock=self.clock,
        )

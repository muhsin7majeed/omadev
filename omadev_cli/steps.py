"""Start and stop a project as a series of check-then-act steps.

Every step first looks at what already exists and only then acts, so
pressing Start twice is harmless: the second run finds everything in place
and reports `skipped` or `focused` for each step. With `dry_run` nothing is
changed and every action is reported as `planned`.

Order for a sequential project:

  workspace  -> ensure the herdr workspace or tmux session exists
               ("none" runs processes in their own terminal windows)
  setup:*    -> one-shot commands, run to completion, skipped when their
               `unless_exists` path is present
  command:*  -> send each process to its own tab or window, unless its
               port is already served or the pane is busy
  terminal   -> focus the terminal attached to the multiplexer, or open one
  wait       -> wait until the project URL answers HTTP
  browser    -> open or focus the URL
  editor     -> open or focus the editor on the project
  app:*      -> open or focus each helper app

A parallel project opens the editor and apps before waiting for the URL.

Windows that Start opens itself can be placed on a Hyprland workspace
(`workspaces`, `apps[].workspace`): after launching, Start waits for the new
window to appear and moves it there silently. A window that already exists
is focused where it is and never moved.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import hypr, ports
from .config import Command, Config, Project, SetupCommand, url_port
from .herdr import Herdr, HerdrError, attach_argv as herdr_attach_argv, client_pids as herdr_client_pids, tab_label
from .system import Runner, SystemRunner, ToolMissing, gui_argv, http_ok, list_processes, port_open, split_command, wait_for_http
from .tmux import Tmux, TmuxError, attach_argv as tmux_attach_argv, session_name, window_name

SKIPPED = "skipped"
STARTED = "started"
FOCUSED = "focused"
STOPPED = "stopped"
FAILED = "failed"
PLANNED = "planned"

LOCAL_HOST = "localhost"
SERVER_START_TIMEOUT = 15.0
PLACE_TIMEOUT = 15.0
SETTLE_SECONDS = 1.5
SETUP_TAB = "setup"
HERDR_INTERRUPT = "ctrl+c"
TMUX_INTERRUPT = "C-c"
BROWSER_CLASSES = r"^(brave|chrom|google-chrome|microsoft-edge|vivaldi|opera|helium|firefox|zen|librewolf)"


class StepFailure(Exception):
    """A step could not do what it set out to do."""


StepError = (HerdrError, TmuxError, hypr.HyprError, ToolMissing, OSError, StepFailure)


@dataclass(frozen=True)
class StepResult:
    step: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"step": self.step, "status": self.status, "detail": self.detail}


@dataclass
class Services:
    """Everything that touches the machine, so tests can replace all of it."""

    runner: Runner
    herdr: Herdr
    tmux: Tmux
    probe: Callable[[str, int], bool]
    http_ok: Callable[[str], bool]
    wait_url: Callable[[str, float], bool]
    processes: Callable[[], list[tuple[int, list[str]]]]
    windows: Callable[[], list[hypr.Window]]
    listener: Callable[[int], ports.Listener | None]
    containers: Callable[[], list[ports.Container] | None]
    proc_root: Path = Path("/proc")
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def real(cls) -> "Services":
        runner = SystemRunner()
        return cls(
            runner=runner,
            herdr=Herdr(runner),
            tmux=Tmux(runner),
            probe=port_open,
            http_ok=http_ok,
            wait_url=wait_for_http,
            processes=list_processes,
            windows=lambda: hypr.clients(runner),
            listener=lambda port: ports.listener(runner, port),
            containers=lambda: ports.containers(runner),
        )


class PortInspector:
    """Classifies ports for one project, asking docker at most once."""

    def __init__(self, project: Project, services: Services) -> None:
        self.project = project
        self.s = services
        self._containers: list[ports.Container] | None = None
        self._asked_docker = False

    def _containers_once(self) -> list[ports.Container] | None:
        if not self._asked_docker:
            self._asked_docker = True
            self._containers = self.s.containers()
        return self._containers

    def state(self, port: int) -> ports.PortState:
        return ports.classify(port, self.project.path, probe=self.s.probe, find_listener=self.s.listener, find_containers=self._containers_once)


@dataclass(frozen=True)
class Session:
    """The container a project's processes live in."""

    kind: str          # "herdr", "tmux" or "none"
    id: str            # herdr workspace id, tmux session name, or "" for none
    created: bool      # True when this run created it (or would, in a dry run)


def fill(text: str, project: Project) -> str:
    """Substitute {path} and {name}. Plain replacement, so regex braces survive."""
    return text.replace("{path}", str(project.path)).replace("{name}", project.name)


def shell_line(cwd: Path, run: str) -> str:
    """One shell line that runs `run` from `cwd`, for tabs whose cwd differs."""
    quoted = "'" + str(cwd).replace("'", "'\\''") + "'"
    return f"cd {quoted} && {run}"


class Run:
    """Shared machinery for Start and Stop: recording, dry run, windows."""

    def __init__(self, project: Project, config: Config, services: Services, *, dry_run: bool) -> None:
        self.project = project
        self.config = config
        self.s = services
        self.dry_run = dry_run
        self.results: list[StepResult] = []
        self._windows: list[hypr.Window] | None = None
        self.ports = PortInspector(project, services)

    # ------------------------------------------------------------- recording

    def record(self, step: str, status: str, detail: str) -> StepResult:
        result = StepResult(step, status, detail)
        self.results.append(result)
        return result

    def act(self, step: str, detail: str, action: Callable[[], None], *, status: str = STARTED) -> bool:
        """Perform `action` unless dry-running; record the outcome.

        Details are imperative ("create tab …"); the status carries the tense.
        """
        if self.dry_run:
            self.record(step, PLANNED, detail)
            return True
        try:
            action()
        except StepError as exc:
            self.record(step, FAILED, f"{detail}: {exc}")
            return False
        self.record(step, status, detail)
        return True

    def windows(self) -> list[hypr.Window]:
        if self._windows is None:
            self._windows = self.s.windows()
        return self._windows

    def refresh_windows(self) -> list[hypr.Window]:
        self._windows = self.s.windows()
        return self._windows

    def focus(self, step: str, window: hypr.Window, what: str) -> bool:
        return self.act(step, f"focus {what} ({window.label})", lambda: hypr.focus(self.s.runner, window), status=FOCUSED)

    def close_window(self, step: str, window: hypr.Window, what: str) -> bool:
        return self.act(step, f"close {what} ({window.label})", lambda: hypr.close(self.s.runner, window), status=STOPPED)

    def wait_until(self, condition: Callable[[], bool], timeout: float) -> bool:
        deadline = self.s.clock() + timeout
        while not condition():
            if self.s.clock() >= deadline:
                return False
            self.s.sleep(0.5)
        return True

    # ------------------------------------------------------------ placement

    def launch_placed(self, step: str, detail: str, argv: list[str], *, workspace: int | None,
                      appears: Callable[[hypr.Window], bool], cwd: Path | None = None) -> None:
        """Detach `argv`; when `workspace` is set, wait for the window it opens
        and move it there. The window is recognised by `appears` and by not
        having existed before the launch."""
        if workspace is None:
            self.act(step, detail, lambda: self.s.runner.detach(argv, cwd=cwd))
            return
        if self.dry_run:
            self.record(step, PLANNED, f"{detail}, on workspace {workspace}")
            return
        before = {w.address for w in self.windows()}

        def find_new() -> hypr.Window | None:
            return hypr.first(self.refresh_windows(), lambda w: w.address not in before and appears(w))

        found: dict[str, hypr.Window | None] = {"window": None}

        def launch_and_move() -> None:
            self.s.runner.detach(argv, cwd=cwd)
            self.wait_until(lambda: (found.__setitem__("window", find_new()) or found["window"] is not None), PLACE_TIMEOUT)
            if found["window"] is not None:
                hypr.move_to_workspace(self.s.runner, found["window"], workspace)

        try:
            launch_and_move()
        except StepError as exc:
            self.record(step, FAILED, f"{detail}: {exc}")
            return
        if found["window"] is None:
            self.record(step, STARTED, f"{detail}; its window did not appear within {PLACE_TIMEOUT:g}s, so it was not moved to workspace {workspace}")
        else:
            self.record(step, STARTED, f"{detail}, moved to workspace {workspace}")

    # ------------------------------------------------------------- lookups

    def find_session(self) -> Session | None:
        """The project's existing workspace or session, without creating it."""
        if self.project.multiplexer == "none":
            return Session("none", "", created=False)
        if self.project.multiplexer == "herdr":
            herdr = self.s.herdr
            if not herdr.available() or not herdr.server_running():
                return None
            workspace = herdr.find_workspace(self.project.session_name)
            return Session("herdr", workspace.id, created=False) if workspace else None
        name = session_name(self.project.session_name)
        if self.s.tmux.available():
            existing = self.s.tmux.find_session(name)
            if existing is not None:
                return Session("tmux", existing, created=False)
        return None

    def editor_window(self) -> hypr.Window | None:
        editor = self.project.effective_editor
        if editor.match is None:
            return None
        pattern = fill(editor.match, self.project)
        folder = self.project.path.name
        return hypr.first(self.windows(), self.editor_predicate(pattern, folder))

    @staticmethod
    def editor_predicate(pattern: str, folder: str) -> Callable[[hypr.Window], bool]:
        return lambda w: hypr.class_matches(w, pattern) and hypr.title_has_word(w, folder)

    def app_window(self, app) -> hypr.Window | None:
        pattern = fill(app.match, self.project)
        return hypr.first(self.windows(), lambda w: hypr.matches(w, pattern))

    def webapp_pattern(self) -> str:
        # Chromium-family app windows carry the URL host in their window
        # class, e.g. "brave-localhost__-Default"; ordinary browser windows
        # are just "brave-browser".
        host = urlsplit(self.project.url or "").hostname or LOCAL_HOST
        return BROWSER_CLASSES + ".*" + re.escape(host)

    def webapp_window(self) -> hypr.Window | None:
        pattern = self.webapp_pattern()
        return hypr.first(self.windows(), lambda w: hypr.class_matches(w, pattern))

    def process_window_class(self, name: str) -> str:
        """Window class of the terminal window a process runs in ("none" mode)."""
        return hypr.app_id(self.project.name, name)

    def process_window(self, name: str) -> hypr.Window | None:
        cls = self.process_window_class(name)
        return hypr.first(self.windows(), lambda w: w.cls == cls or w.initial_class == cls)


class StartRun(Run):
    def __init__(self, project: Project, config: Config, services: Services, *, dry_run: bool) -> None:
        super().__init__(project, config, services, dry_run=dry_run)
        # Set when the URL's port belongs to another project: the browser
        # must not open there, not even in a dry run's plan.
        self.url_blocked = False
        # Set when a setup command failed: processes must not start on a
        # half-prepared project.
        self.setup_failed = False

    # ------------------------------------------------------------- preflight

    def check_page_port(self) -> None:
        """Before anything is typed: is the page's port another project's?

        The per-process check only knows ports that are configured. The page
        is often the only place the port is written down, so it is checked
        first; a port held elsewhere stops the run before a process is sent
        to a tab where it would fail to bind.
        """
        url = self.project.url
        if url is None:
            return
        host = urlsplit(url).hostname or LOCAL_HOST
        if not ports.is_local_host(host):
            return
        port = url_port(url)
        if not self.s.probe(host, port):
            return
        state = self.ports.state(port)
        if state.owner == ports.OTHER:
            self.url_blocked = True
            self.record("page", FAILED, f"{host}:{port} is held by {state.detail}; stop that project first")

    # ------------------------------------------------------------ workspace

    def ensure_multiplexer(self) -> Session | None:
        step = "workspace"
        try:
            if self.project.multiplexer == "none":
                self.record(step, SKIPPED, "no multiplexer; processes run in their own terminal windows")
                return Session("none", "", created=False)
            if self.project.multiplexer == "herdr":
                return self._ensure_herdr(step)
            return self._ensure_tmux(step)
        except StepError as exc:
            self.record(step, FAILED, str(exc))
            return None

    def _ensure_herdr(self, step: str) -> Session | None:
        herdr = self.s.herdr
        label = self.project.session_name
        if not herdr.available():
            self.record(step, FAILED, "herdr is not installed")
            return None
        if not herdr.server_running():
            # Opening a terminal with `herdr` starts the server and attaches.
            started = self.act("herdr", "start herdr in a new terminal", lambda: self.s.runner.detach(herdr_attach_argv()))
            if not started:
                return None
            if self.dry_run:
                self.record(step, PLANNED, f"create herdr workspace '{label}' at {self.project.path}")
                return Session("herdr", "(new)", created=True)
            if not self.wait_until(herdr.server_running, SERVER_START_TIMEOUT):
                self.record(step, FAILED, f"herdr server did not come up within {SERVER_START_TIMEOUT:g}s")
                return None
        workspace = herdr.find_workspace(label)
        if workspace is not None:
            self.record(step, SKIPPED, f"herdr workspace '{label}' exists ({workspace.id})")
            return Session("herdr", workspace.id, created=False)

        created: dict[str, str] = {}

        def create() -> None:
            created["id"] = herdr.create_workspace(self.project.path, label).id

        if not self.act(step, f"create herdr workspace '{label}' at {self.project.path}", create):
            return None
        return Session("herdr", created.get("id", "(new)"), created=True)

    def _ensure_tmux(self, step: str) -> Session | None:
        tmux = self.s.tmux
        name = session_name(self.project.session_name)
        if not tmux.available():
            self.record(step, FAILED, "tmux is not installed")
            return None
        existing = tmux.find_session(name)
        if existing is not None:
            self.record(step, SKIPPED, f"tmux session '{existing}' exists")
            return Session("tmux", existing, created=False)
        if not self.act(step, f"create tmux session '{name}' at {self.project.path}", lambda: tmux.new_session(name, self.project.path)):
            return None
        return Session("tmux", name, created=True)

    # ---------------------------------------------------------------- setup

    def run_setup(self, session: Session | None) -> None:
        for setup in self.project.setup:
            step = f"setup:{setup.name}"
            if self.setup_failed:
                self.record(step, SKIPPED, "an earlier setup command failed")
                continue
            if session is None:
                self.record(step, SKIPPED, "no workspace to run in")
                continue
            if setup.unless_exists is not None and (self.project.path / setup.unless_exists).exists():
                self.record(step, SKIPPED, f"{setup.unless_exists} exists")
                continue
            try:
                ok = self._run_setup(step, setup, session)
            except StepError as exc:
                self.record(step, FAILED, str(exc))
                ok = False
            if not ok:
                self.setup_failed = True

    def _setup_line(self, setup: SetupCommand) -> str:
        cwd = self.project.path / setup.cwd if setup.cwd else self.project.path
        return shell_line(cwd, setup.run)

    def _run_setup(self, step: str, setup: SetupCommand, session: Session) -> bool:
        line = self._setup_line(setup)
        detail = f"run '{setup.run}' and wait for it to finish"
        if session.kind == "none":
            return self._run_setup_in_terminal(step, setup, line, detail)
        if session.kind == "herdr":
            ensure_pane, is_idle, send = self._herdr_setup_pane(session)
        else:
            ensure_pane, is_idle, send = self._tmux_setup_pane(session)
        if self.dry_run:
            self.record(step, PLANNED, detail)
            return True
        started = self.s.clock()

        def run_and_wait() -> None:
            target = ensure_pane()
            if not is_idle(target):
                raise StepFailure("the setup tab is busy with something else")
            send(target, line)
            if not self.wait_until(lambda: is_idle(target), float(setup.timeout)):
                raise StepFailure(f"still running after {setup.timeout}s")

        try:
            run_and_wait()
        except StepError as exc:
            self.record(step, FAILED, f"{detail}: {exc}")
            return False
        self.record(step, STARTED, f"ran '{setup.run}' in {self.s.clock() - started:.1f}s")
        return True

    def _herdr_setup_pane(self, session: Session):
        herdr = self.s.herdr
        label = tab_label(SETUP_TAB)

        def ensure_pane() -> str:
            tab = None if session.created else herdr.find_tab(session.id, label)
            if tab is None:
                _, pane_id = herdr.create_tab(session.id, self.project.path, label)
                return pane_id
            pane = herdr.first_pane(session.id, tab.id)
            if pane is None:
                raise StepFailure(f"tab '{label}' has no pane")
            return pane.id

        return ensure_pane, (lambda pane_id: herdr.foreground(pane_id).idle), herdr.run

    def _tmux_setup_pane(self, session: Session):
        tmux = self.s.tmux
        name = window_name(SETUP_TAB)

        def ensure_pane() -> str:
            target = None if session.created else tmux.find_window(session.id, name)
            return target if target is not None else tmux.new_window(session.id, name, self.project.path)

        return ensure_pane, tmux.idle, tmux.run

    def _run_setup_in_terminal(self, step: str, setup: SetupCommand, line: str, detail: str) -> bool:
        cls = self.process_window_class(f"setup-{setup.name}")
        argv = ["omarchy-launch-tui", f"--app-id={cls}", "bash", "-lc", line]
        if self.dry_run:
            self.record(step, PLANNED, f"{detail} in a terminal window")
            return True
        if self.process_window(f"setup-{setup.name}") is not None:
            self.record(step, FAILED, "its terminal window from an earlier run is still open")
            return False
        started = self.s.clock()

        def gone() -> bool:
            self.refresh_windows()
            return self.process_window(f"setup-{setup.name}") is None

        try:
            self.s.runner.detach(argv, cwd=self.project.path)
            # Give the window time to appear before waiting for it to go.
            self.wait_until(lambda: not gone(), 5.0)
            if not self.wait_until(gone, float(setup.timeout)):
                raise StepFailure(f"still running after {setup.timeout}s")
        except StepError as exc:
            self.record(step, FAILED, f"{detail}: {exc}")
            return False
        self.record(step, STARTED, f"ran '{setup.run}' in a terminal window in {self.s.clock() - started:.1f}s")
        return True

    # ------------------------------------------------------------- commands

    def run_commands(self, session: Session | None) -> None:
        for command in self.project.commands:
            step = f"command:{command.name}"
            try:
                self._run_command(step, command, session)
            except StepError as exc:
                self.record(step, FAILED, str(exc))

    def _run_command(self, step: str, command: Command, session: Session | None) -> None:
        if self.setup_failed:
            self.record(step, SKIPPED, "a setup command failed")
            return
        if self.url_blocked:
            self.record(step, SKIPPED, "the page's port is held by another project")
            return
        port = self.project.command_port(command)
        if port is not None:
            state = self.ports.state(port)
            if state.owner == ports.PROJECT:
                self.record(step, SKIPPED, f"port {port} is already served by {state.detail}")
                return
            if state.owner == ports.UNKNOWN:
                self.record(step, SKIPPED, f"port {port} is listening ({state.detail}); not starting a second server")
                return
            if state.owner == ports.OTHER:
                self.record(step, FAILED, f"port {port} is held by {state.detail}; stop that project first")
                return
        if session is None:
            self.record(step, SKIPPED, "no workspace to run in")
            return
        cwd = self.project.path / command.cwd if command.cwd else self.project.path
        if session.kind == "herdr":
            self._run_in_herdr(step, command, session, cwd)
        elif session.kind == "tmux":
            self._run_in_tmux(step, command, session, cwd)
        else:
            self._run_in_terminal(step, command, cwd)

    def _run_in_herdr(self, step: str, command: Command, session: Session, cwd: Path) -> None:
        herdr = self.s.herdr
        label = tab_label(command.name)
        tab = None if session.created else herdr.find_tab(session.id, label)

        if tab is None:
            def create_and_run() -> None:
                _, pane_id = herdr.create_tab(session.id, cwd, label, env=command.env)
                herdr.run(pane_id, command.run)
                self.confirm_running(lambda: not herdr.foreground(pane_id).idle)

            self.act(step, f"create tab '{label}' and run '{command.run}'", create_and_run)
            return

        pane = herdr.first_pane(session.id, tab.id)
        if pane is None:
            self.record(step, FAILED, f"tab '{label}' has no pane")
            return
        foreground = herdr.foreground(pane.id)
        if not foreground.idle:
            self.record(step, SKIPPED, f"tab '{label}' is busy running {foreground.summary}")
            return

        def run() -> None:
            herdr.run(pane.id, command.run)
            self.confirm_running(lambda: not herdr.foreground(pane.id).idle)

        self.act(step, f"run '{command.run}' in tab '{label}'", run)

    def confirm_running(self, still_running: Callable[[], bool]) -> None:
        """A moment after sending a process, make sure it is still there.

        A server that cannot bind its port, or a command that is not
        installed, is back at the prompt within a second. Reporting that as
        started would hide the very failure the user needs to see.
        """
        self.s.sleep(SETTLE_SECONDS)
        if not still_running():
            raise StepFailure("it exited right away; open its tab to see why")

    def _run_in_tmux(self, step: str, command: Command, session: Session, cwd: Path) -> None:
        tmux = self.s.tmux
        name = window_name(command.name)
        target = None if session.created else tmux.find_window(session.id, name)

        if target is None:
            def create_and_run() -> None:
                created = tmux.new_window(session.id, name, cwd, env=command.env)
                tmux.run(created, command.run)
                self.confirm_running(lambda: not tmux.idle(created))

            self.act(step, f"create window '{name}' and run '{command.run}'", create_and_run)
            return

        if not tmux.idle(target):
            self.record(step, SKIPPED, f"window '{name}' is busy running {tmux.current_command(target)}")
            return

        def run() -> None:
            tmux.run(target, command.run)
            self.confirm_running(lambda: not tmux.idle(target))

        self.act(step, f"run '{command.run}' in window '{name}'", run)

    def _run_in_terminal(self, step: str, command: Command, cwd: Path) -> None:
        """"none" mode: the process gets a terminal window with a known class."""
        window = self.process_window(command.name)
        if window is not None:
            self.record(step, SKIPPED, f"its terminal window is open ({window.label})")
            return
        cls = self.process_window_class(command.name)
        argv = ["omarchy-launch-tui", f"--app-id={cls}", *(["env", *command.env] if command.env else []), "bash", "-lc", shell_line(cwd, command.run)]
        self.launch_placed(step, f"open a terminal window running '{command.run}'", argv,
                           workspace=self.project.workspaces.terminal,
                           appears=lambda w: w.cls == cls or w.initial_class == cls, cwd=cwd)

    # ------------------------------------------------------------- terminal

    def attach_terminal(self, session: Session | None) -> None:
        step = "terminal"
        if session is None:
            self.record(step, SKIPPED, "no workspace to attach to")
            return
        if session.kind == "none":
            self.record(step, SKIPPED, "no multiplexer to attach to")
            return
        try:
            window = self._terminal_window(session)
            if window is not None:
                self.focus(step, window, "terminal")
            else:
                self._open_terminal(step, session)
            if session.kind == "herdr" and not session.created:
                self.act("terminal:focus", f"show workspace {session.id} in herdr", lambda: self.s.herdr.focus_workspace(session.id), status=FOCUSED)
        except StepError as exc:
            self.record(step, FAILED, str(exc))

    def _terminal_window(self, session: Session) -> hypr.Window | None:
        if session.kind == "herdr":
            pids = herdr_client_pids(self.s.processes())
        else:
            pids = self.s.tmux.client_pids(session.id)
        for pid in pids:
            window = hypr.window_for_pid(self.windows(), pid, proc_root=self.s.proc_root)
            if window is not None:
                return window
        return None

    def _open_terminal(self, step: str, session: Session) -> None:
        argv = herdr_attach_argv() if session.kind == "herdr" else tmux_attach_argv(session.id)
        detail = f"open a terminal attached to {session.kind}"
        workspace = self.project.workspaces.terminal
        if workspace is None:
            self.act(step, detail, lambda: self.s.runner.detach(argv))
            return
        if self.dry_run:
            self.record(step, PLANNED, f"{detail}, on workspace {workspace}")
            return
        # The new terminal is found through the multiplexer client it runs,
        # the same way an existing one is, rather than by guessing its class.
        found: dict[str, hypr.Window | None] = {"window": None}

        def appeared() -> bool:
            self.refresh_windows()
            found["window"] = self._terminal_window(session)
            return found["window"] is not None

        try:
            self.s.runner.detach(argv)
            self.wait_until(appeared, PLACE_TIMEOUT)
            if found["window"] is not None:
                hypr.move_to_workspace(self.s.runner, found["window"], workspace)
        except StepError as exc:
            self.record(step, FAILED, f"{detail}: {exc}")
            return
        if found["window"] is None:
            self.record(step, STARTED, f"{detail}; it did not attach within {PLACE_TIMEOUT:g}s, so it was not moved to workspace {workspace}")
        else:
            self.record(step, STARTED, f"{detail}, moved to workspace {workspace}")

    # ------------------------------------------------------------------ url

    def wait_for_url(self) -> tuple[bool, bool]:
        """Returns (responding now, was already responding before this run).

        "Responding" means an HTTP answer, not merely an open port: docker
        binds the port the moment a container starts, before the app inside
        is ready.
        """
        step = "wait"
        url = self.project.url
        if url is None:
            self.record(step, SKIPPED, "no page configured")
            return False, False
        if self.setup_failed:
            self.record(step, SKIPPED, "a setup command failed")
            return False, False
        if self.url_blocked:
            self.record(step, SKIPPED, "the page's port is held by another project (see the page step)")
            return False, False
        host = urlsplit(url).hostname or LOCAL_HOST
        port = url_port(url)
        listening = self.s.probe(host, port)
        owner = ""
        if listening and ports.is_local_host(host):
            state = self.ports.state(port)
            if state.owner == ports.OTHER:
                self.url_blocked = True
                self.record(step, FAILED, f"{host}:{port} is served by {state.detail}, not by this project")
                return False, False
            owner = f" ({state.detail})"
        if listening and self.s.http_ok(url):
            self.record(step, SKIPPED, f"{url} already responds{owner}")
            return True, True
        timeout = self.project.wait_timeout
        if self.dry_run:
            self.record(step, PLANNED, f"wait up to {timeout}s for {url} to respond")
            return False, False
        started = self.s.clock()
        if self.s.wait_url(url, float(timeout)):
            self.record(step, STARTED, f"{url} responds after {self.s.clock() - started:.1f}s")
            return True, False
        why = "port is open but nothing answers HTTP" if self.s.probe(host, port) else "nothing is listening"
        self.record(step, FAILED, f"{url} did not respond within {timeout}s ({why})")
        return False, False

    def open_browser(self, reachable: bool, was_up: bool) -> None:
        step = "browser"
        url = self.project.url
        if url is None:
            self.record(step, SKIPPED, "no page configured")
            return
        if self.url_blocked:
            self.record(step, SKIPPED, "page belongs to another project (see the wait step)")
            return
        if self.setup_failed:
            self.record(step, SKIPPED, "a setup command failed")
            return
        mode = self.config.browser_for(self.project)
        workspace = self.project.workspaces.browser
        try:
            if mode == "webapp":
                window = self.webapp_window()
                if window is not None:
                    self.focus(step, window, "web app window")
                    return
                if not reachable and not self.dry_run:
                    self.record(step, SKIPPED, "page not available (see the wait step), not opening a web app window")
                    return
                pattern = self.webapp_pattern()
                self.launch_placed(step, f"open {url} as a web app window", ["omarchy-launch-webapp", url],
                                   workspace=workspace, appears=lambda w: hypr.class_matches(w, pattern))
                return
            if was_up:
                self.record(step, SKIPPED, "server was already up; an open tab cannot be detected, so none was opened")
                return
            if not reachable and not self.dry_run:
                self.record(step, SKIPPED, "page not available (see the wait step), not opening a browser tab")
                return
            # A tab lands in an existing browser window, which stays where it
            # is, so there is nothing to wait for or move. Only when no
            # browser window exists does xdg-open create one worth placing.
            if hypr.first(self.windows(), lambda w: hypr.class_matches(w, BROWSER_CLASSES)) is not None:
                self.act(step, f"open {url} as a tab in the browser", lambda: self.s.runner.detach(["xdg-open", url]))
                return
            self.launch_placed(step, f"open {url} in a new browser window", ["xdg-open", url],
                               workspace=workspace, appears=lambda w: hypr.class_matches(w, BROWSER_CLASSES))
        except StepError as exc:
            self.record(step, FAILED, str(exc))

    # --------------------------------------------------------- editor, apps

    def open_editor(self) -> None:
        step = "editor"
        editor = self.project.effective_editor
        argv = [fill(part, self.project) for part in editor.launch_argv]
        try:
            window = self.editor_window()
            if window is not None:
                self.focus(step, window, "editor")
                return
            detail = f"open editor: {' '.join(argv)}"
            launch = gui_argv(self.s.runner, argv)
            workspace = self.project.workspaces.editor
            if editor.match is None:
                # Without a class match the new window cannot be told apart,
                # so it is not moved.
                self.act(step, detail, lambda: self.s.runner.detach(launch, cwd=self.project.path))
                return
            pattern = fill(editor.match, self.project)
            if editor.share_window and hypr.first(self.windows(), lambda w: hypr.class_matches(w, pattern)) is not None:
                # The project joins the editor window that is already open,
                # wherever it is; that window is not moved.
                self.act(step, f"{detail} (into the open editor window)", lambda: self.s.runner.detach(launch, cwd=self.project.path))
                return
            if editor.share_window:
                # No editor window yet: the shared launch opens a fresh one,
                # which is ours to place. Only the class can identify it, as
                # its title settles after it appears.
                appears = lambda w: hypr.class_matches(w, pattern)  # noqa: E731
            else:
                appears = self.editor_predicate(pattern, self.project.path.name)
            self.launch_placed(step, detail, launch, workspace=workspace, appears=appears, cwd=self.project.path)
        except StepError as exc:
            self.record(step, FAILED, str(exc))

    def open_apps(self) -> None:
        for app in self.project.apps:
            step = f"app:{app.name}"
            argv = [fill(part, self.project) for part in app.launch]
            pattern = fill(app.match, self.project)
            try:
                window = self.app_window(app)
                if window is not None:
                    self.focus(step, window, app.name)
                    continue
                self.launch_placed(step, f"open {app.name}: {' '.join(argv)}", gui_argv(self.s.runner, argv),
                                   workspace=app.workspace, appears=lambda w, p=pattern: hypr.matches(w, p), cwd=self.project.path)
            except StepError as exc:
                self.record(step, FAILED, str(exc))


class StopRun(Run):
    """Tear down what Start brought up, in reverse, leaving the rest alone.

    The workspace or session and the terminal stay: the user's own panes live
    there. Browser tabs stay because one cannot be told from another; a web
    app window is closed because it can.
    """

    # ------------------------------------------------------------- commands

    def stop_commands(self, session: Session | None) -> None:
        for command in reversed(self.project.commands):
            step = f"command:{command.name}"
            if session is None:
                self.record(step, SKIPPED, "no workspace, nothing to stop")
                continue
            try:
                if session.kind == "herdr":
                    self._stop_in_herdr(step, command, session)
                elif session.kind == "tmux":
                    self._stop_in_tmux(step, command, session)
                else:
                    self._stop_in_terminal(step, command)
            except StepError as exc:
                self.record(step, FAILED, str(exc))
        self.close_setup_tab(session)

    def _port_closed(self, port: int | None) -> bool:
        return port is None or not self.s.probe(LOCAL_HOST, port)

    def _stop_in_herdr(self, step: str, command: Command, session: Session) -> None:
        herdr = self.s.herdr
        label = tab_label(command.name)
        tab = herdr.find_tab(session.id, label)
        if tab is None:
            self.record(step, SKIPPED, f"no tab '{label}'")
            return
        pane = herdr.first_pane(session.id, tab.id)
        if pane is None:
            self.act(step, f"close empty tab '{label}'", lambda: herdr.close_tab(tab.id), status=STOPPED)
            return
        foreground = herdr.foreground(pane.id)
        if foreground.idle:
            self.act(step, f"close idle tab '{label}'", lambda: herdr.close_tab(tab.id), status=STOPPED)
            return
        timeout = self.project.wait_timeout
        port = self.project.command_port(command)

        def interrupt_and_close() -> None:
            herdr.send_keys(pane.id, HERDR_INTERRUPT)
            settled = self.wait_until(lambda: herdr.foreground(pane.id).idle and self._port_closed(port), timeout)
            if not settled:
                raise StepFailure(f"still running after {timeout}s; tab left open")
            herdr.close_tab(tab.id)

        waiting = f", wait for port {port} to close" if port is not None else ""
        self.act(step, f"interrupt {foreground.summary}{waiting}, close tab '{label}'", interrupt_and_close, status=STOPPED)

    def _stop_in_tmux(self, step: str, command: Command, session: Session) -> None:
        tmux = self.s.tmux
        name = window_name(command.name)
        target = tmux.find_window(session.id, name)
        if target is None:
            self.record(step, SKIPPED, f"no window '{name}'")
            return
        if tmux.idle(target):
            self.act(step, f"close idle window '{name}'", lambda: tmux.kill_window(target), status=STOPPED)
            return
        running = tmux.current_command(target)
        timeout = self.project.wait_timeout
        port = self.project.command_port(command)

        def interrupt_and_close() -> None:
            tmux.send_keys(target, TMUX_INTERRUPT)
            settled = self.wait_until(lambda: tmux.idle(target) and self._port_closed(port), timeout)
            if not settled:
                raise StepFailure(f"still running after {timeout}s; window left open")
            tmux.kill_window(target)

        waiting = f", wait for port {port} to close" if port is not None else ""
        self.act(step, f"interrupt {running}{waiting}, close window '{name}'", interrupt_and_close, status=STOPPED)

    def _stop_in_terminal(self, step: str, command: Command) -> None:
        """"none" mode: closing the terminal window hangs up the process."""
        window = self.process_window(command.name)
        if window is None:
            self.record(step, SKIPPED, "no terminal window for it")
            return
        timeout = self.project.wait_timeout
        port = self.project.command_port(command)

        def close_and_wait() -> None:
            hypr.close(self.s.runner, window)
            if not self.wait_until(lambda: self._port_closed(port), timeout):
                raise StepFailure(f"port {port} still open after {timeout}s")

        waiting = f" and wait for port {port} to close" if port is not None else ""
        self.act(step, f"close its terminal window{waiting}", close_and_wait, status=STOPPED)

    def close_setup_tab(self, session: Session | None) -> None:
        """The setup tab is omadev's; close it when it is idle."""
        if session is None or session.kind == "none" or not self.project.setup:
            return
        step = "setup"
        try:
            if session.kind == "herdr":
                herdr = self.s.herdr
                tab = herdr.find_tab(session.id, tab_label(SETUP_TAB))
                if tab is None:
                    return
                pane = herdr.first_pane(session.id, tab.id)
                if pane is not None and not herdr.foreground(pane.id).idle:
                    self.record(step, SKIPPED, "setup tab is busy; left open")
                    return
                self.act(step, "close the setup tab", lambda: herdr.close_tab(tab.id), status=STOPPED)
            else:
                tmux = self.s.tmux
                target = tmux.find_window(session.id, window_name(SETUP_TAB))
                if target is None:
                    return
                if not tmux.idle(target):
                    self.record(step, SKIPPED, "setup window is busy; left open")
                    return
                self.act(step, "close the setup window", lambda: tmux.kill_window(target), status=STOPPED)
        except StepError as exc:
            self.record(step, FAILED, str(exc))

    # -------------------------------------------------------- stop_commands

    def run_stop_commands(self) -> None:
        """Project-declared cleanup, run directly in the project directory.

        Parsed like a POSIX shell would, but never through one: quoting
        works, pipes and variables do not.
        """
        for index, text in enumerate(self.project.stop_commands, start=1):
            step = f"stop:{index}"
            try:
                argv = split_command(text)
            except ValueError as exc:
                self.record(step, FAILED, f"'{text}': {exc}")
                continue

            def run(argv: list[str] = argv) -> None:
                result = self.s.runner.run(argv, timeout=float(self.project.wait_timeout), cwd=self.project.path)
                if not result.ok:
                    raise StepFailure(result.message)

            self.act(step, f"run '{text}' in {self.project.path}", run, status=STOPPED)

    # ------------------------------------------------------------- windows

    def close_editor(self) -> None:
        step = "editor"
        editor = self.project.effective_editor
        if editor.share_window:
            self.record(step, SKIPPED, "editor window is shared with other projects; left open")
            return
        if editor.match is None:
            self.record(step, SKIPPED, "editor has no window match; left open")
            return
        try:
            window = self.editor_window()
        except StepError as exc:
            self.record(step, FAILED, str(exc))
            return
        if window is None:
            self.record(step, SKIPPED, "no editor window for this project")
            return
        self.close_window(step, window, "editor")

    def close_apps(self) -> None:
        for app in self.project.apps:
            step = f"app:{app.name}"
            try:
                window = self.app_window(app)
            except StepError as exc:
                self.record(step, FAILED, str(exc))
                continue
            if window is None:
                self.record(step, SKIPPED, f"no {app.name} window")
                continue
            self.close_window(step, window, app.name)

    def close_browser(self) -> None:
        step = "browser"
        if self.project.url is None:
            self.record(step, SKIPPED, "no page configured")
            return
        if self.config.browser_for(self.project) != "webapp":
            self.record(step, SKIPPED, "browser tab left open; a tab cannot be told from the others")
            return
        try:
            window = self.webapp_window()
        except StepError as exc:
            self.record(step, FAILED, str(exc))
            return
        if window is None:
            self.record(step, SKIPPED, "no web app window open")
            return
        self.close_window(step, window, "web app window")


def stop(project: Project, config: Config, services: Services, *, dry_run: bool = False) -> list[StepResult]:
    run = StopRun(project, config, services, dry_run=dry_run)
    try:
        session = run.find_session()
    except StepError as exc:
        run.record("workspace", FAILED, str(exc))
        session = None
    else:
        if session is None:
            run.record("workspace", SKIPPED, f"no {project.multiplexer} workspace '{project.session_name}'")
        elif session.kind == "none":
            run.record("workspace", SKIPPED, "no multiplexer; processes run in their own terminal windows")
        else:
            run.record("workspace", SKIPPED, f"{project.multiplexer} workspace '{project.session_name}' left in place ({session.id})")
    run.stop_commands(session)
    run.run_stop_commands()
    run.close_apps()
    run.close_editor()
    run.close_browser()
    return run.results


def start(project: Project, config: Config, services: Services, *, dry_run: bool = False) -> list[StepResult]:
    run = StartRun(project, config, services, dry_run=dry_run)
    run.check_page_port()
    session = run.ensure_multiplexer()
    run.run_setup(session)
    run.run_commands(session)
    run.attach_terminal(session)
    if project.mode == "parallel":
        run.open_editor()
        run.open_apps()
    reachable, was_up = run.wait_for_url()
    run.open_browser(reachable, was_up)
    if project.mode != "parallel":
        run.open_editor()
        run.open_apps()
    return run.results


def succeeded(results: list[StepResult]) -> bool:
    return all(result.status != FAILED for result in results)


def status(project: Project, config: Config, services: Services) -> dict:
    """A read-only snapshot: what of this project is up right now."""
    snapshot: dict = {
        "name": project.name,
        "path": str(project.path),
        "multiplexer": project.multiplexer,
        "browser": config.browser_for(project),
    }

    lookup = Run(project, config, services, dry_run=True)
    try:
        if project.multiplexer == "none":
            snapshot["workspace"] = {"present": None, "id": None}
        elif project.multiplexer == "herdr":
            workspace = services.herdr.find_workspace(project.session_name) if services.herdr.available() and services.herdr.server_running() else None
            snapshot["workspace"] = {"present": workspace is not None, "id": workspace.id if workspace else None}
        else:
            existing = services.tmux.find_session(session_name(project.session_name)) if services.tmux.available() else None
            snapshot["workspace"] = {"present": existing is not None, "id": existing}
    except StepError as exc:
        snapshot["workspace"] = {"present": False, "id": None, "error": str(exc)}

    try:
        lookup.windows()
    except StepError as exc:
        snapshot["windows_error"] = str(exc)
        lookup._windows = []

    inspector = PortInspector(project, services)
    snapshot["commands"] = []
    for command in project.commands:
        port = project.command_port(command)
        entry: dict = {"name": command.name, "port": port, "listening": None, "owner": None, "detail": None}
        if port is not None:
            entry.update(inspector.state(port).to_dict())
        elif project.multiplexer == "none":
            entry["listening"] = lookup.process_window(command.name) is not None
        snapshot["commands"].append(entry)
    if project.url is not None:
        host = urlsplit(project.url).hostname or LOCAL_HOST
        port = url_port(project.url)
        listening = services.probe(host, port)
        entry = {"value": project.url, "reachable": listening and services.http_ok(project.url), "listening": listening, "owner": None, "detail": None}
        if listening and ports.is_local_host(host):
            state = inspector.state(port)
            entry.update({"owner": state.owner, "detail": state.detail})
        snapshot["url"] = entry
    else:
        snapshot["url"] = None

    snapshot["editor_open"] = None if project.effective_editor.match is None else lookup.editor_window() is not None
    snapshot["apps"] = [{"name": a.name, "open": lookup.app_window(a) is not None} for a in project.apps]
    return snapshot

"""Start a project as a series of check-then-act steps.

Every step first looks at what already exists and only then acts, so
pressing Start twice is harmless: the second run finds everything in place
and reports `skipped` or `focused` for each step. With `dry_run` nothing is
changed and every action is reported as `planned`.

Order for a sequential project:

  workspace  -> ensure the herdr workspace or tmux session exists
  command:*  -> send each command to its own tab or window, unless its
                port is already listening or its pane is busy
  terminal   -> focus the terminal attached to the multiplexer, or open one
  wait       -> wait until the project URL accepts connections
  browser    -> open or focus the URL
  editor     -> open or focus the editor on the project
  app:*      -> open or focus each helper app

A parallel project opens the editor and apps before waiting for the URL.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import hypr
from .config import Config, Project, url_port
from .herdr import Herdr, HerdrError, attach_argv as herdr_attach_argv, client_pids as herdr_client_pids, tab_label
from .system import Runner, SystemRunner, ToolMissing, gui_argv, list_processes, port_open, wait_for_port
from .tmux import Tmux, TmuxError, attach_argv as tmux_attach_argv, session_name, window_name

SKIPPED = "skipped"
STARTED = "started"
FOCUSED = "focused"
FAILED = "failed"
PLANNED = "planned"

LOCAL_HOST = "localhost"
SERVER_START_TIMEOUT = 15.0

StepError = (HerdrError, TmuxError, hypr.HyprError, ToolMissing, OSError)


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
    wait: Callable[[str, int, float], bool]
    processes: Callable[[], list[tuple[int, list[str]]]]
    windows: Callable[[], list[hypr.Window]]
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
            wait=wait_for_port,
            processes=list_processes,
            windows=lambda: hypr.clients(runner),
        )


@dataclass(frozen=True)
class Session:
    """The multiplexer container a project lives in."""

    kind: str          # "herdr" or "tmux"
    id: str            # herdr workspace id, or tmux session name
    created: bool      # True when this run created it (or would, in a dry run)


def fill(text: str, project: Project) -> str:
    """Substitute {path} and {name}. Plain replacement, so regex braces survive."""
    return text.replace("{path}", str(project.path)).replace("{name}", project.name)


class StartRun:
    def __init__(self, project: Project, config: Config, services: Services, *, dry_run: bool) -> None:
        self.project = project
        self.config = config
        self.s = services
        self.dry_run = dry_run
        self.results: list[StepResult] = []
        self._windows: list[hypr.Window] | None = None

    # ------------------------------------------------------------- recording

    def record(self, step: str, status: str, detail: str) -> StepResult:
        result = StepResult(step, status, detail)
        self.results.append(result)
        return result

    def act(self, step: str, detail: str, action: Callable[[], None], *, status: str = STARTED) -> bool:
        """Perform `action` unless dry-running; record the outcome."""
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

    def focus(self, step: str, window: hypr.Window, what: str) -> bool:
        # Details are imperative ("focus X"); the status carries the tense.
        return self.act(step, f"focus {what} ({window.label})", lambda: hypr.focus(self.s.runner, window), status=FOCUSED)

    # ------------------------------------------------------------ workspace

    def ensure_multiplexer(self) -> Session | None:
        step = "workspace"
        try:
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
            if not self._wait_for(herdr.server_running, SERVER_START_TIMEOUT):
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
        if tmux.has_session(name):
            self.record(step, SKIPPED, f"tmux session '{name}' exists")
            return Session("tmux", name, created=False)
        if not self.act(step, f"create tmux session '{name}' at {self.project.path}", lambda: tmux.new_session(name, self.project.path)):
            return None
        return Session("tmux", name, created=True)

    def _wait_for(self, condition: Callable[[], bool], timeout: float) -> bool:
        deadline = self.s.clock() + timeout
        while not condition():
            if self.s.clock() >= deadline:
                return False
            self.s.sleep(0.5)
        return True

    # ------------------------------------------------------------- commands

    def run_commands(self, session: Session | None) -> None:
        for command in self.project.commands:
            step = f"command:{command.name}"
            try:
                self._run_command(step, command, session)
            except StepError as exc:
                self.record(step, FAILED, str(exc))

    def _run_command(self, step: str, command, session: Session | None) -> None:
        if command.port is not None and self.s.probe(LOCAL_HOST, command.port):
            self.record(step, SKIPPED, f"port {command.port} is already listening")
            return
        if session is None:
            self.record(step, SKIPPED, "no workspace to run in")
            return
        cwd = self.project.path / command.cwd if command.cwd else self.project.path
        if session.kind == "herdr":
            self._run_in_herdr(step, command, session, cwd)
        else:
            self._run_in_tmux(step, command, session, cwd)

    def _run_in_herdr(self, step: str, command, session: Session, cwd: Path) -> None:
        herdr = self.s.herdr
        label = tab_label(command.name)
        tab = None if session.created else herdr.find_tab(session.id, label)

        if tab is None:
            def create_and_run() -> None:
                _, pane_id = herdr.create_tab(session.id, cwd, label)
                herdr.run(pane_id, command.run)

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
        self.act(step, f"run '{command.run}' in tab '{label}'", lambda: herdr.run(pane.id, command.run))

    def _run_in_tmux(self, step: str, command, session: Session, cwd: Path) -> None:
        tmux = self.s.tmux
        name = window_name(command.name)
        target = None if session.created else tmux.find_window(session.id, name)

        if target is None:
            def create_and_run() -> None:
                tmux.run(tmux.new_window(session.id, name, cwd), command.run)

            self.act(step, f"create window '{name}' and run '{command.run}'", create_and_run)
            return

        if not tmux.idle(target):
            self.record(step, SKIPPED, f"window '{name}' is busy running {tmux.current_command(target)}")
            return
        self.act(step, f"run '{command.run}' in window '{name}'", lambda: tmux.run(target, command.run))

    # ------------------------------------------------------------- terminal

    def attach_terminal(self, session: Session | None) -> None:
        step = "terminal"
        if session is None:
            self.record(step, SKIPPED, "no workspace to attach to")
            return
        try:
            if session.kind == "herdr":
                pids = herdr_client_pids(self.s.processes())
            else:
                pids = self.s.tmux.client_pids(session.id)
            window = None
            for pid in pids:
                window = hypr.window_for_pid(self.windows(), pid, proc_root=self.s.proc_root)
                if window is not None:
                    break
            if window is not None:
                self.focus(step, window, "terminal")
            else:
                argv = herdr_attach_argv() if session.kind == "herdr" else tmux_attach_argv(session.id)
                self.act(step, f"open a terminal attached to {session.kind}", lambda: self.s.runner.detach(argv))
            if session.kind == "herdr" and not session.created:
                self.act("terminal:focus", f"show workspace {session.id} in herdr", lambda: self.s.herdr.focus_workspace(session.id), status=FOCUSED)
        except StepError as exc:
            self.record(step, FAILED, str(exc))

    # ------------------------------------------------------------------ url

    def wait_for_url(self) -> tuple[bool, bool]:
        """Returns (reachable now, was already reachable before this run)."""
        step = "wait"
        url = self.project.url
        if url is None:
            self.record(step, SKIPPED, "no url configured")
            return False, False
        host = urlsplit(url).hostname or LOCAL_HOST
        port = url_port(url)
        if self.s.probe(host, port):
            self.record(step, SKIPPED, f"{host}:{port} is already reachable")
            return True, True
        timeout = self.project.wait_timeout
        if self.dry_run:
            self.record(step, PLANNED, f"wait up to {timeout}s for {host}:{port}")
            return False, False
        started = self.s.clock()
        if self.s.wait(host, port, float(timeout)):
            self.record(step, STARTED, f"{host}:{port} reachable after {self.s.clock() - started:.1f}s")
            return True, False
        self.record(step, FAILED, f"{host}:{port} not reachable after {timeout}s")
        return False, False

    def open_browser(self, reachable: bool, was_up: bool) -> None:
        step = "browser"
        url = self.project.url
        if url is None:
            self.record(step, SKIPPED, "no url configured")
            return
        mode = self.config.browser_for(self.project)
        try:
            if mode == "webapp":
                host = urlsplit(url).hostname or LOCAL_HOST
                # Chromium-family app windows carry the URL host in their
                # window class, e.g. "brave-localhost__-Default"; ordinary
                # browser windows are just "brave-browser".
                pattern = r"^(brave|chrom|google-chrome|microsoft-edge|vivaldi|opera|helium).*" + re.escape(host)
                window = hypr.first(self.windows(), lambda w: hypr.class_matches(w, pattern))
                if window is not None:
                    self.focus(step, window, "web app window")
                    return
                if not reachable and not self.dry_run:
                    self.record(step, SKIPPED, "url not reachable, not opening a web app window")
                    return
                self.act(step, f"open {url} as a web app window", lambda: self.s.runner.detach(["omarchy-launch-webapp", url]))
                return
            if was_up:
                self.record(step, SKIPPED, "server was already up; an open tab cannot be detected, so none was opened")
                return
            if not reachable and not self.dry_run:
                self.record(step, SKIPPED, "url not reachable, not opening a browser tab")
                return
            self.act(step, f"open {url} in the browser", lambda: self.s.runner.detach(["xdg-open", url]))
        except StepError as exc:
            self.record(step, FAILED, str(exc))

    # --------------------------------------------------------- editor, apps

    def open_editor(self) -> None:
        step = "editor"
        editor = self.project.effective_editor
        argv = [fill(part, self.project) for part in editor.launch]
        folder = self.project.path.name
        try:
            if editor.match is not None:
                pattern = fill(editor.match, self.project)
                window = hypr.first(self.windows(), lambda w: hypr.class_matches(w, pattern) and hypr.title_has_word(w, folder))
                if window is not None:
                    self.focus(step, window, "editor")
                    return
            self.act(step, f"open editor: {' '.join(argv)}", lambda: self.s.runner.detach(gui_argv(self.s.runner, argv), cwd=self.project.path))
        except StepError as exc:
            self.record(step, FAILED, str(exc))

    def open_apps(self) -> None:
        for app in self.project.apps:
            step = f"app:{app.name}"
            argv = [fill(part, self.project) for part in app.launch]
            pattern = fill(app.match, self.project)
            try:
                window = hypr.first(self.windows(), lambda w: hypr.matches(w, pattern))
                if window is not None:
                    self.focus(step, window, app.name)
                    continue
                self.act(step, f"open {app.name}: {' '.join(argv)}", lambda: self.s.runner.detach(gui_argv(self.s.runner, argv), cwd=self.project.path))
            except StepError as exc:
                self.record(step, FAILED, str(exc))


def start(project: Project, config: Config, services: Services, *, dry_run: bool = False) -> list[StepResult]:
    run = StartRun(project, config, services, dry_run=dry_run)
    session = run.ensure_multiplexer()
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
    snapshot: dict = {"name": project.name, "multiplexer": project.multiplexer}

    try:
        if project.multiplexer == "herdr":
            workspace = services.herdr.find_workspace(project.session_name) if services.herdr.available() and services.herdr.server_running() else None
            snapshot["workspace"] = {"present": workspace is not None, "id": workspace.id if workspace else None}
        else:
            name = session_name(project.session_name)
            present = services.tmux.available() and services.tmux.has_session(name)
            snapshot["workspace"] = {"present": present, "id": name if present else None}
    except StepError as exc:
        snapshot["workspace"] = {"present": False, "id": None, "error": str(exc)}

    snapshot["commands"] = [
        {"name": c.name, "port": c.port, "listening": services.probe(LOCAL_HOST, c.port) if c.port is not None else None}
        for c in project.commands
    ]
    if project.url is not None:
        host = urlsplit(project.url).hostname or LOCAL_HOST
        snapshot["url"] = {"value": project.url, "reachable": services.probe(host, url_port(project.url))}
    else:
        snapshot["url"] = None

    try:
        windows = services.windows()
    except StepError as exc:
        snapshot["windows_error"] = str(exc)
        windows = []
    editor = project.effective_editor
    folder = project.path.name
    if editor.match is not None:
        pattern = fill(editor.match, project)
        snapshot["editor_open"] = hypr.first(windows, lambda w: hypr.class_matches(w, pattern) and hypr.title_has_word(w, folder)) is not None
    else:
        snapshot["editor_open"] = None
    snapshot["apps"] = [
        {"name": a.name, "open": hypr.first(windows, lambda w, p=fill(a.match, project): hypr.matches(w, p)) is not None}
        for a in project.apps
    ]
    return snapshot

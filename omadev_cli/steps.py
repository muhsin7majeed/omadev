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

from . import hypr, ports
from .config import Config, Project, url_port
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
HERDR_INTERRUPT = "ctrl+c"
TMUX_INTERRUPT = "C-c"


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
    """The multiplexer container a project lives in."""

    kind: str          # "herdr" or "tmux"
    id: str            # herdr workspace id, or tmux session name
    created: bool      # True when this run created it (or would, in a dry run)


def fill(text: str, project: Project) -> str:
    """Substitute {path} and {name}. Plain replacement, so regex braces survive."""
    return text.replace("{path}", str(project.path)).replace("{name}", project.name)


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

    # ------------------------------------------------------------- lookups

    def find_session(self) -> Session | None:
        """The project's existing workspace or session, without creating it."""
        if self.project.multiplexer == "herdr":
            herdr = self.s.herdr
            if not herdr.available() or not herdr.server_running():
                return None
            workspace = herdr.find_workspace(self.project.session_name)
            return Session("herdr", workspace.id, created=False) if workspace else None
        name = session_name(self.project.session_name)
        if self.s.tmux.available() and self.s.tmux.has_session(name):
            return Session("tmux", name, created=False)
        return None

    def editor_window(self) -> hypr.Window | None:
        editor = self.project.effective_editor
        if editor.match is None:
            return None
        pattern = fill(editor.match, self.project)
        folder = self.project.path.name
        return hypr.first(self.windows(), lambda w: hypr.class_matches(w, pattern) and hypr.title_has_word(w, folder))

    def app_window(self, app) -> hypr.Window | None:
        pattern = fill(app.match, self.project)
        return hypr.first(self.windows(), lambda w: hypr.matches(w, pattern))

    def webapp_window(self) -> hypr.Window | None:
        # Chromium-family app windows carry the URL host in their window
        # class, e.g. "brave-localhost__-Default"; ordinary browser windows
        # are just "brave-browser".
        host = urlsplit(self.project.url or "").hostname or LOCAL_HOST
        pattern = r"^(brave|chrom|google-chrome|microsoft-edge|vivaldi|opera|helium).*" + re.escape(host)
        return hypr.first(self.windows(), lambda w: hypr.class_matches(w, pattern))


class StartRun(Run):
    def __init__(self, project: Project, config: Config, services: Services, *, dry_run: bool) -> None:
        super().__init__(project, config, services, dry_run=dry_run)
        # Set when the URL's port belongs to another project: the browser
        # must not open there, not even in a dry run's plan.
        self.url_blocked = False

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
        if tmux.has_session(name):
            self.record(step, SKIPPED, f"tmux session '{name}' exists")
            return Session("tmux", name, created=False)
        if not self.act(step, f"create tmux session '{name}' at {self.project.path}", lambda: tmux.new_session(name, self.project.path)):
            return None
        return Session("tmux", name, created=True)

    # ------------------------------------------------------------- commands

    def run_commands(self, session: Session | None) -> None:
        for command in self.project.commands:
            step = f"command:{command.name}"
            try:
                self._run_command(step, command, session)
            except StepError as exc:
                self.record(step, FAILED, str(exc))

    def _run_command(self, step: str, command, session: Session | None) -> None:
        if command.port is not None:
            state = self.ports.state(command.port)
            if state.owner == ports.PROJECT:
                self.record(step, SKIPPED, f"port {command.port} is already served by {state.detail}")
                return
            if state.owner == ports.UNKNOWN:
                self.record(step, SKIPPED, f"port {command.port} is listening ({state.detail}); not starting a second server")
                return
            if state.owner == ports.OTHER:
                self.record(step, FAILED, f"port {command.port} is held by {state.detail}; stop that project first")
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
        """Returns (responding now, was already responding before this run).

        "Responding" means an HTTP answer, not merely an open port: docker
        binds the port the moment a container starts, before the app inside
        is ready.
        """
        step = "wait"
        url = self.project.url
        if url is None:
            self.record(step, SKIPPED, "no url configured")
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
            self.record(step, SKIPPED, "no url configured")
            return
        if self.url_blocked:
            self.record(step, SKIPPED, "url belongs to another project (see the wait step)")
            return
        mode = self.config.browser_for(self.project)
        try:
            if mode == "webapp":
                window = self.webapp_window()
                if window is not None:
                    self.focus(step, window, "web app window")
                    return
                if not reachable and not self.dry_run:
                    self.record(step, SKIPPED, "url not available (see the wait step), not opening a web app window")
                    return
                self.act(step, f"open {url} as a web app window", lambda: self.s.runner.detach(["omarchy-launch-webapp", url]))
                return
            if was_up:
                self.record(step, SKIPPED, "server was already up; an open tab cannot be detected, so none was opened")
                return
            if not reachable and not self.dry_run:
                self.record(step, SKIPPED, "url not available (see the wait step), not opening a browser tab")
                return
            self.act(step, f"open {url} in the browser", lambda: self.s.runner.detach(["xdg-open", url]))
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
            self.act(step, f"open editor: {' '.join(argv)}", lambda: self.s.runner.detach(gui_argv(self.s.runner, argv), cwd=self.project.path))
        except StepError as exc:
            self.record(step, FAILED, str(exc))

    def open_apps(self) -> None:
        for app in self.project.apps:
            step = f"app:{app.name}"
            argv = [fill(part, self.project) for part in app.launch]
            try:
                window = self.app_window(app)
                if window is not None:
                    self.focus(step, window, app.name)
                    continue
                self.act(step, f"open {app.name}: {' '.join(argv)}", lambda: self.s.runner.detach(gui_argv(self.s.runner, argv), cwd=self.project.path))
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
                else:
                    self._stop_in_tmux(step, command, session)
            except StepError as exc:
                self.record(step, FAILED, str(exc))

    def _port_closed(self, port: int | None) -> bool:
        return port is None or not self.s.probe(LOCAL_HOST, port)

    def _stop_in_herdr(self, step: str, command, session: Session) -> None:
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

        def interrupt_and_close() -> None:
            herdr.send_keys(pane.id, HERDR_INTERRUPT)
            settled = self.wait_until(lambda: herdr.foreground(pane.id).idle and self._port_closed(command.port), timeout)
            if not settled:
                raise StepFailure(f"still running after {timeout}s; tab left open")
            herdr.close_tab(tab.id)

        waiting = f", wait for port {command.port} to close" if command.port is not None else ""
        self.act(step, f"interrupt {foreground.summary}{waiting}, close tab '{label}'", interrupt_and_close, status=STOPPED)

    def _stop_in_tmux(self, step: str, command, session: Session) -> None:
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

        def interrupt_and_close() -> None:
            tmux.send_keys(target, TMUX_INTERRUPT)
            settled = self.wait_until(lambda: tmux.idle(target) and self._port_closed(command.port), timeout)
            if not settled:
                raise StepFailure(f"still running after {timeout}s; window left open")
            tmux.kill_window(target)

        waiting = f", wait for port {command.port} to close" if command.port is not None else ""
        self.act(step, f"interrupt {running}{waiting}, close window '{name}'", interrupt_and_close, status=STOPPED)

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
            self.record(step, SKIPPED, "no url configured")
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
    snapshot: dict = {
        "name": project.name,
        "path": str(project.path),
        "multiplexer": project.multiplexer,
        "browser": config.browser_for(project),
    }

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

    inspector = PortInspector(project, services)
    snapshot["commands"] = []
    for command in project.commands:
        entry: dict = {"name": command.name, "port": command.port, "listening": None, "owner": None, "detail": None}
        if command.port is not None:
            entry.update(inspector.state(command.port).to_dict())
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

    lookup = Run(project, config, services, dry_run=True)
    try:
        lookup.windows()
    except StepError as exc:
        snapshot["windows_error"] = str(exc)
        lookup._windows = []
    snapshot["editor_open"] = None if project.effective_editor.match is None else lookup.editor_window() is not None
    snapshot["apps"] = [{"name": a.name, "open": lookup.app_window(a) is not None} for a in project.apps]
    return snapshot

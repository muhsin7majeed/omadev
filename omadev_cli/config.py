"""Load, validate and save the projects file.

The projects file is the single source of truth for what omadev manages. It
lives outside the plugin directory, at ~/.config/omadev/projects.json, so a
plugin update never touches it. Everything read from it is validated here,
once, so the rest of the CLI can trust field names and types without checking
again. Unknown fields are reported as warnings rather than errors, so a file
written by a newer version still loads.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
MULTIPLEXERS = ("herdr", "tmux", "none")
BROWSER_MODES = ("webapp", "browser")
MODES = ("sequential", "parallel")
MAX_PORT = 65535
DEFAULT_WAIT_TIMEOUT = 90
MAX_WAIT_TIMEOUT = 3600
DEFAULT_SETUP_TIMEOUT = 600
MAX_WORKSPACE = 99

# Environment entries are KEY=VALUE with a shell-style key.
ENV_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.DOTALL)

# Project and command names become multiplexer labels, window-match patterns
# and log lines, so they are kept to a short, readable alphabet.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")

_PROJECT_FIELDS = frozenset({
    "name", "path", "multiplexer", "session", "setup", "commands", "url", "browser",
    "editor", "apps", "stop_commands", "mode", "wait_timeout", "workspaces",
})
_COMMAND_FIELDS = frozenset({"name", "run", "port", "cwd", "env"})
_SETUP_FIELDS = frozenset({"name", "run", "cwd", "unless_exists", "timeout"})
_APP_FIELDS = frozenset({"name", "launch", "match", "workspace"})
_WORKSPACES_FIELDS = frozenset({"terminal", "browser", "editor"})
_EDITOR_FIELDS = frozenset({"launch", "launch_shared", "match", "share_window"})
_CONFIG_FIELDS = frozenset({"version", "default_browser", "projects"})


def config_dir() -> Path:
    """Directory holding user configuration, honouring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "omadev"


def state_dir() -> Path:
    """Directory for logs and run state, honouring XDG_STATE_HOME."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "omadev"


def projects_file() -> Path:
    """Default location of the projects file."""
    return config_dir() / "projects.json"


class ConfigError(ValueError):
    """A problem in the projects file, naming where it was found."""

    def __init__(self, where: str, message: str) -> None:
        super().__init__(f"{where}: {message}")
        self.where = where
        self.message = message


@dataclass(frozen=True)
class Command:
    """One long-running command, such as a dev server, run in its own pane.

    `port` is the port the command listens on. When set, Start skips the
    command if something already listens there and Stop waits for it to
    close. When unset, the command is always sent on Start. `cwd` is
    relative to the project path; the pane is opened there.
    """

    name: str
    run: str
    port: int | None = None
    cwd: str | None = None
    env: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetupCommand:
    """A one-shot command run before the processes, such as `npm install`.

    Runs in the project's setup tab and Start waits for it to finish.
    `unless_exists` skips it when that path (relative to the project) is
    already there, which is how `npm install` becomes a no-op once
    `node_modules` exists.
    """

    name: str
    run: str
    cwd: str | None = None
    unless_exists: str | None = None
    timeout: int = DEFAULT_SETUP_TIMEOUT


@dataclass(frozen=True)
class App:
    """A helper application opened alongside the project.

    `launch` is an argv list. `match` is a regular expression tested against
    the Hyprland window class and title to find an already open instance.
    Both may use the placeholders {path} and {name}. `workspace` is the
    Hyprland workspace a newly opened window is moved to.
    """

    name: str
    launch: tuple[str, ...]
    match: str
    workspace: int | None = None


@dataclass(frozen=True)
class Workspaces:
    """Hyprland workspaces for windows that Start opens itself.

    A window that is already open, on whatever workspace, is focused where
    it is; only new windows are moved. None means "wherever it opens".
    """

    terminal: int | None = None
    browser: int | None = None
    editor: int | None = None


@dataclass(frozen=True)
class Editor:
    """How to open the project in an editor.

    `launch` is an argv list; `match` is a regular expression tested against
    the window class of an already open editor window. When `match` is unset
    the editor is launched every time and is trusted to reuse its own window,
    which Zed, VS Code and Cursor all do for an already open folder.

    `share_window` opens the project inside the editor window that is
    already open, alongside other projects, using `launch_shared` (for Zed,
    VS Code and Cursor that is their `--add` flag). Stop then leaves the
    window alone, since closing it would close the other projects too.
    """

    launch: tuple[str, ...]
    match: str | None = None
    launch_shared: tuple[str, ...] | None = None
    share_window: bool = False

    @property
    def launch_argv(self) -> tuple[str, ...]:
        """The command Start uses, honouring `share_window` when it can."""
        if self.share_window and self.launch_shared:
            return self.launch_shared
        return self.launch


DEFAULT_EDITOR = Editor(launch=("omarchy-launch-editor", "{path}"))


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    multiplexer: str = "herdr"
    session: str | None = None
    setup: tuple[SetupCommand, ...] = ()
    commands: tuple[Command, ...] = ()
    url: str | None = None
    browser: str | None = None
    editor: Editor | None = None
    apps: tuple[App, ...] = ()
    stop_commands: tuple[str, ...] = ()
    mode: str = "sequential"
    wait_timeout: int = DEFAULT_WAIT_TIMEOUT
    workspaces: Workspaces = Workspaces()

    @property
    def session_name(self) -> str:
        """Multiplexer workspace label or tmux session name."""
        return self.session or self.name

    @property
    def effective_editor(self) -> Editor:
        return self.editor or DEFAULT_EDITOR

    @property
    def url_port(self) -> int | None:
        """Port implied by `url`, including the scheme default."""
        if self.url is None:
            return None
        return url_port(self.url)


@dataclass(frozen=True)
class Config:
    version: int = SCHEMA_VERSION
    default_browser: str = "webapp"
    projects: tuple[Project, ...] = ()
    warnings: tuple[str, ...] = ()

    def find(self, name: str) -> Project | None:
        for project in self.projects:
            if project.name == name:
                return project
        return None

    def browser_for(self, project: Project) -> str:
        return project.browser or self.default_browser


def url_port(url: str) -> int:
    """Port of a validated http(s) URL."""
    parts = urlsplit(url)
    if parts.port is not None:
        return parts.port
    return 443 if parts.scheme == "https" else 80


# --------------------------------------------------------------------------- parsing


def _expect(value: Any, where: str, kind: type, kind_name: str) -> Any:
    # bool is a subclass of int; a port of `true` must not pass as an int.
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ConfigError(where, f"must be {kind_name}")
    return value


def _string(value: Any, where: str, *, allow_empty: bool = False) -> str:
    text = _expect(value, where, str, "a string")
    if not allow_empty and not text.strip():
        raise ConfigError(where, "must not be empty")
    return text


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    items = _expect(value, where, list, "a list of strings")
    return tuple(_string(item, f"{where}[{i}]") for i, item in enumerate(items))


def _choice(value: Any, where: str, choices: tuple[str, ...]) -> str:
    text = _string(value, where)
    if text not in choices:
        raise ConfigError(where, "must be one of " + ", ".join(choices))
    return text


def _port(value: Any, where: str) -> int:
    port = _expect(value, where, int, "an integer")
    if not 1 <= port <= MAX_PORT:
        raise ConfigError(where, f"must be between 1 and {MAX_PORT}")
    return port


def _url(value: Any, where: str) -> str:
    text = _string(value, where)
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(where, "must be an http:// or https:// URL with a host")
    try:
        parts.port  # noqa: B018  (raises ValueError on an out-of-range port)
    except ValueError as exc:
        raise ConfigError(where, "has an invalid port") from exc
    return text


def _regex(value: Any, where: str) -> str:
    text = _string(value, where)
    try:
        re.compile(text)
    except re.error as exc:
        raise ConfigError(where, f"is not a valid regular expression ({exc})") from exc
    return text


def _warn_unknown(raw: dict[str, Any], known: frozenset[str], where: str, warnings: list[str]) -> None:
    for key in sorted(set(raw) - known):
        warnings.append(f"{where}: unknown field '{key}' ignored")


def _relative_dir(value: Any, where: str) -> str:
    text = _string(value, where)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ConfigError(where, "must be a relative path inside the project, without '..'")
    return text


def _short_name(value: Any, where: str) -> str:
    name = _string(value, where)
    if not NAME_PATTERN.match(name):
        raise ConfigError(where, "may use letters, digits, space, dot, underscore and dash, up to 64 characters")
    return name


def _workspace(value: Any, where: str) -> int:
    number = _expect(value, where, int, "an integer")
    if not 1 <= number <= MAX_WORKSPACE:
        raise ConfigError(where, f"must be a Hyprland workspace number between 1 and {MAX_WORKSPACE}")
    return number


def _env_list(value: Any, where: str) -> tuple[str, ...]:
    entries = _string_list(value, where)
    for i, entry in enumerate(entries):
        if not ENV_PATTERN.match(entry):
            raise ConfigError(f"{where}[{i}]", "must look like KEY=value")
    return entries


def _parse_command(raw: Any, where: str, warnings: list[str]) -> Command:
    data = _expect(raw, where, dict, "an object")
    _warn_unknown(data, _COMMAND_FIELDS, where, warnings)
    if "name" not in data or "run" not in data:
        raise ConfigError(where, "needs 'name' and 'run'")
    port = data.get("port")
    cwd = data.get("cwd")
    return Command(
        name=_short_name(data["name"], f"{where}.name"),
        run=_string(data["run"], f"{where}.run"),
        port=None if port is None else _port(port, f"{where}.port"),
        cwd=None if cwd is None else _relative_dir(cwd, f"{where}.cwd"),
        env=_env_list(data.get("env", []), f"{where}.env"),
    )


def _parse_setup(raw: Any, where: str, warnings: list[str]) -> SetupCommand:
    data = _expect(raw, where, dict, "an object")
    _warn_unknown(data, _SETUP_FIELDS, where, warnings)
    if "name" not in data or "run" not in data:
        raise ConfigError(where, "needs 'name' and 'run'")
    cwd = data.get("cwd")
    unless = data.get("unless_exists")
    timeout = _expect(data.get("timeout", DEFAULT_SETUP_TIMEOUT), f"{where}.timeout", int, "an integer")
    if not 1 <= timeout <= MAX_WAIT_TIMEOUT:
        raise ConfigError(f"{where}.timeout", f"must be between 1 and {MAX_WAIT_TIMEOUT} seconds")
    return SetupCommand(
        name=_short_name(data["name"], f"{where}.name"),
        run=_string(data["run"], f"{where}.run"),
        cwd=None if cwd is None else _relative_dir(cwd, f"{where}.cwd"),
        unless_exists=None if unless is None else _relative_dir(unless, f"{where}.unless_exists"),
        timeout=timeout,
    )


def _parse_workspaces(raw: Any, where: str, warnings: list[str]) -> Workspaces:
    data = _expect(raw, where, dict, "an object")
    _warn_unknown(data, _WORKSPACES_FIELDS, where, warnings)
    values = {}
    for key in ("terminal", "browser", "editor"):
        value = data.get(key)
        values[key] = None if value is None else _workspace(value, f"{where}.{key}")
    return Workspaces(**values)


def _parse_editor(raw: Any, where: str, warnings: list[str]) -> Editor:
    data = _expect(raw, where, dict, "an object")
    _warn_unknown(data, _EDITOR_FIELDS, where, warnings)
    if "launch" not in data:
        raise ConfigError(where, "needs 'launch'")
    launch = _string_list(data["launch"], f"{where}.launch")
    if not launch:
        raise ConfigError(f"{where}.launch", "must not be empty")
    match = data.get("match")
    shared = data.get("launch_shared")
    share = data.get("share_window", False)
    if not isinstance(share, bool):
        raise ConfigError(f"{where}.share_window", "must be true or false")
    return Editor(
        launch=launch,
        match=None if match is None else _regex(match, f"{where}.match"),
        launch_shared=None if shared is None else _string_list(shared, f"{where}.launch_shared") or None,
        share_window=share,
    )


def _parse_app(raw: Any, where: str, warnings: list[str]) -> App:
    data = _expect(raw, where, dict, "an object")
    _warn_unknown(data, _APP_FIELDS, where, warnings)
    for key in ("name", "launch", "match"):
        if key not in data:
            raise ConfigError(where, f"needs '{key}'")
    launch = _string_list(data["launch"], f"{where}.launch")
    if not launch:
        raise ConfigError(f"{where}.launch", "must not be empty")
    workspace = data.get("workspace")
    return App(
        name=_string(data["name"], f"{where}.name"),
        launch=launch,
        match=_regex(data["match"], f"{where}.match"),
        workspace=None if workspace is None else _workspace(workspace, f"{where}.workspace"),
    )


def _parse_project(raw: Any, where: str, warnings: list[str], *, check_paths: bool) -> Project:
    data = _expect(raw, where, dict, "an object")
    _warn_unknown(data, _PROJECT_FIELDS, where, warnings)
    if "name" not in data or "path" not in data:
        raise ConfigError(where, "needs 'name' and 'path'")

    name = _string(data["name"], f"{where}.name")
    if not NAME_PATTERN.match(name):
        raise ConfigError(f"{where}.name", "may use letters, digits, space, dot, underscore and dash, up to 64 characters")
    where = f"project '{name}'"

    path = Path(_string(data["path"], f"{where}.path")).expanduser()
    if check_paths and not path.is_dir():
        raise ConfigError(f"{where}.path", f"directory does not exist: {path}")

    session = data.get("session")
    url = data.get("url")
    browser = data.get("browser")
    editor = data.get("editor")

    commands = tuple(
        _parse_command(item, f"{where}.commands[{i}]", warnings)
        for i, item in enumerate(_expect(data.get("commands", []), f"{where}.commands", list, "a list"))
    )
    seen_commands: set[str] = set()
    for command in commands:
        if command.name in seen_commands:
            raise ConfigError(f"{where}.commands", f"duplicate command name '{command.name}'")
        seen_commands.add(command.name)

    setup = tuple(
        _parse_setup(item, f"{where}.setup[{i}]", warnings)
        for i, item in enumerate(_expect(data.get("setup", []), f"{where}.setup", list, "a list"))
    )
    workspaces_raw = data.get("workspaces")
    workspaces = Workspaces() if workspaces_raw is None else _parse_workspaces(workspaces_raw, f"{where}.workspaces", warnings)

    apps = tuple(
        _parse_app(item, f"{where}.apps[{i}]", warnings)
        for i, item in enumerate(_expect(data.get("apps", []), f"{where}.apps", list, "a list"))
    )

    wait_timeout = _expect(data.get("wait_timeout", DEFAULT_WAIT_TIMEOUT), f"{where}.wait_timeout", int, "an integer")
    if not 1 <= wait_timeout <= MAX_WAIT_TIMEOUT:
        raise ConfigError(f"{where}.wait_timeout", f"must be between 1 and {MAX_WAIT_TIMEOUT} seconds")

    return Project(
        name=name,
        path=path,
        multiplexer=_choice(data.get("multiplexer", "herdr"), f"{where}.multiplexer", MULTIPLEXERS),
        session=None if session is None else _string(session, f"{where}.session"),
        setup=setup,
        commands=commands,
        url=None if url is None else _url(url, f"{where}.url"),
        browser=None if browser is None else _choice(browser, f"{where}.browser", BROWSER_MODES),
        editor=None if editor is None else _parse_editor(editor, f"{where}.editor", warnings),
        apps=apps,
        stop_commands=_string_list(data.get("stop_commands", []), f"{where}.stop_commands"),
        mode=_choice(data.get("mode", "sequential"), f"{where}.mode", MODES),
        wait_timeout=wait_timeout,
        workspaces=workspaces,
    )


def parse_project(data: Any, *, check_paths: bool = True) -> tuple[Project, tuple[str, ...]]:
    """Validate one project on its own, as `add` and `edit` receive it.

    Returns the project and any unknown-field warnings.
    """
    warnings: list[str] = []
    project = _parse_project(data, "project", warnings, check_paths=check_paths)
    return project, tuple(warnings)


def with_project(config: Config, project: Project, *, replacing: str | None = None) -> Config:
    """A copy of `config` with `project` added, or replacing the project named `replacing`."""
    others = [p for p in config.projects if p.name != replacing]
    if any(p.name == project.name for p in others):
        raise ConfigError("project.name", f"a project named '{project.name}' already exists")
    if replacing is not None and config.find(replacing) is None:
        raise ConfigError("project", f"no project named '{replacing}'")
    if replacing is None:
        projects = tuple(others) + (project,)
    else:
        projects = tuple(project if p.name == replacing else p for p in config.projects)
    return Config(version=config.version, default_browser=config.default_browser, projects=projects, warnings=config.warnings)


def without_project(config: Config, name: str) -> Config:
    if config.find(name) is None:
        raise ConfigError("project", f"no project named '{name}'")
    projects = tuple(p for p in config.projects if p.name != name)
    return Config(version=config.version, default_browser=config.default_browser, projects=projects, warnings=config.warnings)


def parse(data: Any, *, check_paths: bool = True) -> Config:
    """Turn decoded JSON into a Config, or raise ConfigError.

    `check_paths=False` skips the directory-exists check, for tests and for
    editing a file whose projects are on a drive that is not mounted.
    """
    root = _expect(data, "projects file", dict, "a JSON object")
    warnings: list[str] = []
    _warn_unknown(root, _CONFIG_FIELDS, "projects file", warnings)

    version = _expect(root.get("version"), "version", int, "an integer")
    if version != SCHEMA_VERSION:
        raise ConfigError("version", f"unsupported version {version}; this omadev understands {SCHEMA_VERSION}")

    projects = tuple(
        _parse_project(item, f"projects[{i}]", warnings, check_paths=check_paths)
        for i, item in enumerate(_expect(root.get("projects", []), "projects", list, "a list"))
    )
    seen: set[str] = set()
    for project in projects:
        if project.name in seen:
            raise ConfigError("projects", f"duplicate project name '{project.name}'")
        seen.add(project.name)

    return Config(
        version=version,
        default_browser=_choice(root.get("default_browser", "webapp"), "default_browser", BROWSER_MODES),
        projects=projects,
        warnings=tuple(warnings),
    )


def load(path: Path | None = None, *, check_paths: bool = True) -> Config:
    """Read and validate the projects file. A missing file is an empty Config."""
    target = path or projects_file()
    if not target.exists():
        return Config()
    try:
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(str(target), f"is not valid JSON (line {exc.lineno}, column {exc.colno})") from exc
    except OSError as exc:
        raise ConfigError(str(target), f"could not be read ({exc.strerror})") from exc
    return parse(data, check_paths=check_paths)


# --------------------------------------------------------------------------- saving


def project_to_dict(item: Project) -> dict[str, Any]:
    """The JSON shape of one project, omitting unset optional fields."""

    def command(item: Command) -> dict[str, Any]:
        data: dict[str, Any] = {"name": item.name, "run": item.run}
        if item.port is not None:
            data["port"] = item.port
        if item.cwd is not None:
            data["cwd"] = item.cwd
        if item.env:
            data["env"] = list(item.env)
        return data

    def setup(item: SetupCommand) -> dict[str, Any]:
        data: dict[str, Any] = {"name": item.name, "run": item.run}
        if item.cwd is not None:
            data["cwd"] = item.cwd
        if item.unless_exists is not None:
            data["unless_exists"] = item.unless_exists
        if item.timeout != DEFAULT_SETUP_TIMEOUT:
            data["timeout"] = item.timeout
        return data

    def app(item: App) -> dict[str, Any]:
        data: dict[str, Any] = {"name": item.name, "launch": list(item.launch), "match": item.match}
        if item.workspace is not None:
            data["workspace"] = item.workspace
        return data

    def workspaces(item: Workspaces) -> dict[str, Any]:
        return {key: value for key, value in (("terminal", item.terminal), ("browser", item.browser), ("editor", item.editor)) if value is not None}

    def editor(item: Editor) -> dict[str, Any]:
        data: dict[str, Any] = {"launch": list(item.launch)}
        if item.match is not None:
            data["match"] = item.match
        if item.launch_shared is not None:
            data["launch_shared"] = list(item.launch_shared)
        if item.share_window:
            data["share_window"] = True
        return data

    data: dict[str, Any] = {
        "name": item.name,
        "path": str(item.path),
        "multiplexer": item.multiplexer,
        "mode": item.mode,
    }
    if item.wait_timeout != DEFAULT_WAIT_TIMEOUT:
        data["wait_timeout"] = item.wait_timeout
    if item.session is not None:
        data["session"] = item.session
    if item.setup:
        data["setup"] = [setup(s) for s in item.setup]
    if item.commands:
        data["commands"] = [command(c) for c in item.commands]
    placed = workspaces(item.workspaces)
    if placed:
        data["workspaces"] = placed
    if item.url is not None:
        data["url"] = item.url
    if item.browser is not None:
        data["browser"] = item.browser
    if item.editor is not None:
        data["editor"] = editor(item.editor)
    if item.apps:
        data["apps"] = [app(a) for a in item.apps]
    if item.stop_commands:
        data["stop_commands"] = list(item.stop_commands)
    return data


def to_dict(config: Config) -> dict[str, Any]:
    """The JSON shape of a Config."""
    return {
        "version": config.version,
        "default_browser": config.default_browser,
        "projects": [project_to_dict(p) for p in config.projects],
    }


def save(config: Config, path: Path | None = None) -> None:
    """Write the projects file atomically, readable by the owner only.

    Writes to a temporary file in the same directory and renames it over the
    target, so a crash mid-write can never leave a half-written file.
    """
    target = path or projects_file()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = json.dumps(to_dict(config), indent=2, ensure_ascii=False) + "\n"

    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, prefix=".projects-", suffix=".tmp", delete=False)
    try:
        with handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise

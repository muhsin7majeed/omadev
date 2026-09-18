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
MULTIPLEXERS = ("herdr", "tmux")
BROWSER_MODES = ("webapp", "browser")
MODES = ("sequential", "parallel")
MAX_PORT = 65535

# Project names become multiplexer labels, window-match patterns and log
# lines, so they are kept to a short, readable alphabet.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")

_PROJECT_FIELDS = frozenset({
    "name", "path", "multiplexer", "session", "commands", "url", "browser",
    "editor", "apps", "stop_commands", "mode",
})
_COMMAND_FIELDS = frozenset({"name", "run", "port"})
_APP_FIELDS = frozenset({"name", "launch", "match"})
_CONFIG_FIELDS = frozenset({"version", "default_browser", "projects"})


def config_dir() -> Path:
    """Directory holding user configuration, honouring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
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
    close. When unset, the command is always sent on Start.
    """

    name: str
    run: str
    port: int | None = None


@dataclass(frozen=True)
class App:
    """A helper application opened alongside the project.

    `launch` is an argv list. `match` is a regular expression tested against
    the Hyprland window class and title to find an already open instance.
    """

    name: str
    launch: tuple[str, ...]
    match: str


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    multiplexer: str = "herdr"
    session: str | None = None
    commands: tuple[Command, ...] = ()
    url: str | None = None
    browser: str | None = None
    editor: tuple[str, ...] | None = None
    apps: tuple[App, ...] = ()
    stop_commands: tuple[str, ...] = ()
    mode: str = "sequential"

    @property
    def session_name(self) -> str:
        """Multiplexer workspace label or tmux session name."""
        return self.session or self.name

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


def _parse_command(raw: Any, where: str, warnings: list[str]) -> Command:
    data = _expect(raw, where, dict, "an object")
    _warn_unknown(data, _COMMAND_FIELDS, where, warnings)
    if "name" not in data or "run" not in data:
        raise ConfigError(where, "needs 'name' and 'run'")
    port = data.get("port")
    return Command(
        name=_string(data["name"], f"{where}.name"),
        run=_string(data["run"], f"{where}.run"),
        port=None if port is None else _port(port, f"{where}.port"),
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
    return App(
        name=_string(data["name"], f"{where}.name"),
        launch=launch,
        match=_regex(data["match"], f"{where}.match"),
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
    apps = tuple(
        _parse_app(item, f"{where}.apps[{i}]", warnings)
        for i, item in enumerate(_expect(data.get("apps", []), f"{where}.apps", list, "a list"))
    )

    return Project(
        name=name,
        path=path,
        multiplexer=_choice(data.get("multiplexer", "herdr"), f"{where}.multiplexer", MULTIPLEXERS),
        session=None if session is None else _string(session, f"{where}.session"),
        commands=commands,
        url=None if url is None else _url(url, f"{where}.url"),
        browser=None if browser is None else _choice(browser, f"{where}.browser", BROWSER_MODES),
        editor=None if editor is None else _string_list(editor, f"{where}.editor") or None,
        apps=apps,
        stop_commands=_string_list(data.get("stop_commands", []), f"{where}.stop_commands"),
        mode=_choice(data.get("mode", "sequential"), f"{where}.mode", MODES),
    )


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


def to_dict(config: Config) -> dict[str, Any]:
    """The JSON shape of a Config, omitting unset optional fields."""

    def command(item: Command) -> dict[str, Any]:
        data: dict[str, Any] = {"name": item.name, "run": item.run}
        if item.port is not None:
            data["port"] = item.port
        return data

    def app(item: App) -> dict[str, Any]:
        return {"name": item.name, "launch": list(item.launch), "match": item.match}

    def project(item: Project) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": item.name,
            "path": str(item.path),
            "multiplexer": item.multiplexer,
            "mode": item.mode,
        }
        if item.session is not None:
            data["session"] = item.session
        if item.commands:
            data["commands"] = [command(c) for c in item.commands]
        if item.url is not None:
            data["url"] = item.url
        if item.browser is not None:
            data["browser"] = item.browser
        if item.editor is not None:
            data["editor"] = list(item.editor)
        if item.apps:
            data["apps"] = [app(a) for a in item.apps]
        if item.stop_commands:
            data["stop_commands"] = list(item.stop_commands)
        return data

    return {
        "version": config.version,
        "default_browser": config.default_browser,
        "projects": [project(p) for p in config.projects],
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

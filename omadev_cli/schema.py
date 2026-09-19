"""The project form, described as data.

The widget renders its add/edit form from this description, so a new
project field is added here and in `config.py`, and the form follows. A test
keeps the two in step: every field the validator knows appears here with the
same choices, and nothing appears here that the validator would reject.

Field kinds the form knows how to render:

  string   one line of text
  path     a directory (absolute or ~); `relative: true` means inside the project
  url      an http(s) URL
  regex    a regular expression
  argv     a command line; the form shows one string, the CLI splits it
  integer  a whole number within `min`..`max`
  enum     one of `options` ({value, label})
  object   a group of `fields`; `presets` offer ready-made values
  list     repeated `item`s, each a scalar kind or an object with `fields`

`optional: true` means the field may be left empty; the CLI turns an empty
value into "unset". Everything else is required.
"""

from __future__ import annotations

from . import config as cfg

COMMAND_FIELDS = [
    {"key": "name", "kind": "string", "label": "Name", "help": "Short label; the tab is called omadev-<name>."},
    {"key": "run", "kind": "string", "label": "Command", "help": "Typed into the tab as-is, so shell syntax works."},
    {"key": "port", "kind": "integer", "label": "Port", "optional": True, "min": 1, "max": cfg.MAX_PORT,
     "help": "Port this command listens on. Start skips it when the port is already served by this project."},
    {"key": "cwd", "kind": "path", "label": "Folder", "optional": True, "relative": True,
     "help": "Relative to the project, for monorepos. Empty means the project root."},
]

APP_FIELDS = [
    {"key": "name", "kind": "string", "label": "Name"},
    {"key": "launch", "kind": "argv", "label": "Launch command", "help": "May use {path} and {name}."},
    {"key": "match", "kind": "regex", "label": "Window match",
     "help": "Regular expression tested against the window class and title to find an open instance."},
]

EDITOR_FIELDS = [
    {"key": "launch", "kind": "argv", "label": "Launch command", "help": "May use {path} and {name}."},
    {"key": "match", "kind": "regex", "label": "Window class match", "optional": True,
     "help": "Leave empty to launch every time and let the editor reuse its own window."},
]

EDITOR_PRESETS = [
    {"label": "Omarchy default editor", "value": None},
    {"label": "Zed", "value": {"launch": ["zed", "{path}"], "match": r"^dev\.zed\.Zed$"}},
    {"label": "VS Code", "value": {"launch": ["code", "{path}"], "match": r"^(code|Code)"}},
    {"label": "Cursor", "value": {"launch": ["cursor", "{path}"], "match": r"^(cursor|Cursor)"}},
]

PROJECT_FIELDS = [
    {"key": "name", "kind": "string", "label": "Name",
     "help": "Letters, digits, space, dot, underscore and dash. Also the herdr workspace label or tmux session name unless a session is set."},
    {"key": "path", "kind": "path", "label": "Folder", "help": "The project directory. ~ is allowed."},
    {"key": "multiplexer", "kind": "enum", "label": "Terminal multiplexer", "default": "herdr",
     "options": [{"value": "herdr", "label": "herdr"}, {"value": "tmux", "label": "tmux"}]},
    {"key": "session", "kind": "string", "label": "Workspace or session name", "optional": True,
     "help": "Defaults to the project name."},
    {"key": "commands", "kind": "list", "label": "Commands", "item": {"kind": "object", "fields": COMMAND_FIELDS},
     "help": "Each runs in its own tab. Dev servers, watchers, databases."},
    {"key": "url", "kind": "url", "label": "URL", "optional": True,
     "help": "Opened once it answers HTTP. Also decides which port Start waits for."},
    {"key": "browser", "kind": "enum", "label": "Open the URL as", "optional": True,
     "options": [{"value": "", "label": "Default"}, {"value": "browser", "label": "Tab in the browser"},
                 {"value": "webapp", "label": "Web app window"}],
     "help": "A web app window has its own window, so Start can focus it instead of opening it again."},
    {"key": "editor", "kind": "object", "label": "Editor", "optional": True, "fields": EDITOR_FIELDS,
     "presets": EDITOR_PRESETS},
    {"key": "apps", "kind": "list", "label": "Helper apps", "item": {"kind": "object", "fields": APP_FIELDS},
     "help": "lazydocker, Postman, a SQL client. Focused if already open, launched otherwise."},
    {"key": "stop_commands", "kind": "list", "label": "Stop commands", "item": {"kind": "string"},
     "help": "Run in the project folder on Stop, after the commands are interrupted. For example: docker compose down"},
    {"key": "mode", "kind": "enum", "label": "Start order", "default": "sequential",
     "options": [{"value": "sequential", "label": "Wait for the URL before opening editor and apps"},
                 {"value": "parallel", "label": "Open editor and apps right away"}]},
    {"key": "wait_timeout", "kind": "integer", "label": "Wait timeout (seconds)", "default": cfg.DEFAULT_WAIT_TIMEOUT,
     "min": 1, "max": cfg.MAX_WAIT_TIMEOUT, "help": "How long Start waits for the URL, and Stop for a command to end."},
]


def describe() -> dict:
    """What the CLI hands the widget for `omadev schema --json`."""
    return {"fields": PROJECT_FIELDS, "version": cfg.SCHEMA_VERSION}

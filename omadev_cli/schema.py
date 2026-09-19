"""The project form, described as data.

The widget renders its add/edit form from this description, so a new
project field is added here and in `config.py`, and the form follows. A test
keeps the two in step: every field the validator knows appears here with the
same choices, and nothing appears here that the validator would reject.

Every field carries `help` (what it is for and when to use it) and text
fields carry an `example`, which the form shows as placeholder text.

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
    {"key": "name", "kind": "string", "label": "Name", "example": "app",
     "help": "A short label for this command. Its terminal tab is called omadev-<name>, "
             "which is how Start finds it again and Stop knows what to interrupt."},
    {"key": "run", "kind": "string", "label": "Command", "example": "docker compose up",
     "help": "Typed into the tab exactly as written and followed by Enter, so anything your shell "
             "accepts works: npm run dev, docker compose up, rails s, python manage.py runserver."},
    {"key": "port", "kind": "integer", "label": "Port", "optional": True, "min": 1, "max": cfg.MAX_PORT, "example": "3000",
     "help": "The port this command listens on once it is up. Start checks it before typing anything: "
             "already served by this project means skip, held by another project means refuse. "
             "Stop waits for it to close after Ctrl-C. Leave empty for commands that do not listen, "
             "such as a file watcher."},
    {"key": "cwd", "kind": "path", "label": "Sub-folder", "optional": True, "relative": True, "example": "client",
     "help": "Run the command from this folder inside the project instead of the project root. "
             "For monorepos: client for the frontend, server for the API. Empty means the project root."},
]

APP_FIELDS = [
    {"key": "name", "kind": "string", "label": "Name", "example": "lazydocker",
     "help": "How the app is called in the status line and in step results."},
    {"key": "launch", "kind": "argv", "label": "Launch command", "example": "omarchy-launch-tui lazydocker",
     "help": "Started when no window matches. Written like a shell command; {path} becomes the project "
             "folder and {name} the project name. Terminal apps: omarchy-launch-tui lazydocker. "
             "Desktop apps: postman, dbeaver. Web apps: omarchy-launch-webapp https://app.example.com"},
    {"key": "match", "kind": "regex", "label": "Window match", "example": "lazydocker",
     "help": "A regular expression tested against the window class and title (case-insensitive). "
             "When a window matches, Start focuses it instead of launching another. "
             "Find the class with: hyprctl clients -j | jq '.[].class'"},
]

EDITOR_FIELDS = [
    {"key": "launch", "kind": "argv", "label": "Launch command", "example": "zed {path}",
     "help": "How to open the project in the editor. {path} becomes the project folder."},
    {"key": "match", "kind": "regex", "label": "Window class match", "optional": True, "example": r"^dev\.zed\.Zed$",
     "help": "The editor's window class. Start focuses a window with this class whose title contains the "
             "project folder name; Stop closes it. Leave empty to launch every time and let the editor "
             "reuse its own window, which Zed, VS Code and Cursor do for an already open folder."},
]

EDITOR_PRESETS = [
    {"label": "Omarchy default editor", "value": None},
    {"label": "Zed", "value": {"launch": ["zed", "{path}"], "match": r"^dev\.zed\.Zed$"}},
    {"label": "VS Code", "value": {"launch": ["code", "{path}"], "match": r"^(code|Code)"}},
    {"label": "Cursor", "value": {"launch": ["cursor", "{path}"], "match": r"^(cursor|Cursor)"}},
]

PROJECT_FIELDS = [
    {"key": "name", "kind": "string", "label": "Name", "example": "kadha",
     "help": "How the project appears in the list, and the name you pass to omadev start. "
             "Letters, digits, space, dot, underscore and dash. Unless a session name is set below, "
             "it is also the herdr workspace label or tmux session name Start looks for."},
    {"key": "path", "kind": "path", "label": "Folder", "example": "~/Development/kadha",
     "help": "The project directory. Commands run here, the editor opens it, and the multiplexer "
             "workspace is created here. ~ is allowed."},
    {"key": "multiplexer", "kind": "enum", "label": "Terminal multiplexer", "default": "herdr",
     "options": [{"value": "herdr", "label": "herdr"}, {"value": "tmux", "label": "tmux"}],
     "help": "Where the commands run. herdr: one workspace per project, one tab per command. "
             "tmux: one session per project, one window per command. Start reuses an existing "
             "workspace or session and never types into a pane that is busy."},
    {"key": "session", "kind": "string", "label": "Workspace or session name", "optional": True, "example": "kadha",
     "help": "Only needed when your existing herdr workspace or tmux session is not named after the "
             "project. Start attaches to the workspace with this label instead of creating one."},
    {"key": "commands", "kind": "list", "label": "Commands", "item": {"kind": "object", "fields": COMMAND_FIELDS},
     "help": "Long-running processes the project needs: dev servers, watchers, databases. Each gets its "
             "own tab. Start sends a command only if its port is not already served; Stop interrupts "
             "it with Ctrl-C, waits for the port to close, and closes the tab."},
    {"key": "url", "kind": "url", "label": "URL", "optional": True, "example": "http://localhost:3000",
     "help": "The page to open once the project is up. Start waits until this URL answers HTTP, "
             "not just until the port is open, then opens the browser. Also shown as up or down in the list."},
    {"key": "browser", "kind": "enum", "label": "Open the URL as", "optional": True,
     "options": [{"value": "", "label": "Default (web app window)"}, {"value": "browser", "label": "Tab in the browser"},
                 {"value": "webapp", "label": "Web app window"}],
     "help": "Tab in the browser: opened with xdg-open in your running browser; a second Start cannot "
             "tell whether the tab is still open, so it only opens one when it started the server itself. "
             "Web app window: a separate window without tabs, so Start can focus it and Stop can close it."},
    {"key": "editor", "kind": "object", "label": "Editor", "optional": True, "fields": EDITOR_FIELDS,
     "presets": EDITOR_PRESETS,
     "help": "Opened on the project folder by Start and closed by Stop. Pick a preset, or Custom to set "
             "the launch command and window class yourself. Omarchy default uses whatever "
             "omarchy default editor is set to."},
    {"key": "apps", "kind": "list", "label": "Helper apps", "item": {"kind": "object", "fields": APP_FIELDS},
     "help": "Other windows you want alongside the project: lazydocker, Postman, a database client, "
             "a second web app. Start focuses each one if a window matches, launches it otherwise; "
             "Stop closes them."},
    {"key": "stop_commands", "kind": "list", "label": "Stop commands", "item": {"kind": "string"},
     "example": "docker compose down",
     "help": "Run in the project folder on Stop, after the commands have been interrupted. Use them for "
             "cleanup that Ctrl-C does not do, such as docker compose down. Written like a shell "
             "command but run without a shell: quoting works, pipes and variables do not."},
    {"key": "mode", "kind": "enum", "label": "Start order", "default": "sequential",
     "options": [{"value": "sequential", "label": "Wait for the URL before opening editor and apps"},
                 {"value": "parallel", "label": "Open editor and apps right away"}],
     "help": "Sequential keeps the terminal in front while the server comes up, then opens everything "
             "once the URL answers. Parallel opens the editor and apps immediately and lets the browser "
             "follow when the URL is ready."},
    {"key": "wait_timeout", "kind": "integer", "label": "Wait timeout (seconds)", "default": cfg.DEFAULT_WAIT_TIMEOUT,
     "min": 1, "max": cfg.MAX_WAIT_TIMEOUT, "example": str(cfg.DEFAULT_WAIT_TIMEOUT),
     "help": "How long Start waits for the URL to answer before giving up on the browser step, and how "
             "long Stop waits for a command to end after Ctrl-C before leaving its tab open. "
             "Raise it for docker builds that take minutes."},
]


def describe() -> dict:
    """What the CLI hands the widget for `omadev schema --json`."""
    return {"fields": PROJECT_FIELDS, "version": cfg.SCHEMA_VERSION}

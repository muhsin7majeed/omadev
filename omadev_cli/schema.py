"""The project form, described as data.

The widget renders its add/edit form from this description, so a new
project field is added here and in `config.py`, and the form follows. A test
keeps the two in step: every field the validator knows appears here with the
same choices, and nothing appears here that the validator would reject.

Two views of the same fields:

- `PROJECT_FIELDS` is the flat list, one entry per stored key, mirroring
  the validator. This is what parity is checked against.
- `SECTIONS` is how the form lays them out: grouped by what a person is
  thinking about, in that order. A section may reach into a stored object
  with a dotted key (`workspaces.terminal`), so a workspace number sits next
  to the window it places instead of in a block of its own.

Every field carries `help` (what it is for and when to use it) and text
fields carry an `example`, which the form shows as placeholder text.

Field kinds the form knows how to render:

  string     one line of text
  path       a directory (absolute or ~); `relative: true` means inside the project
  url        an http(s) URL
  regex      a regular expression
  argv       a command line; the form shows one string, the CLI splits it
  integer    a whole number within `min`..`max`
  workspace  a Hyprland workspace number, picked from a list, or none
  boolean    a toggle
  enum       one of `options` ({value, label})
  object     a group of `fields`; `presets` offer ready-made values
  list       repeated `item`s, each a scalar kind or an object with `fields`

Field flags:

  optional    may be left empty; the CLI turns an empty value into "unset"
  advanced    inside a group, hidden behind "More options" until asked for
  always      inside a group with presets, shown even while a preset is
              selected (the others appear only for "Custom")
  visible_if  hidden until another field has a value ({"key": "url"}) or
              differs from one ({"key": "multiplexer", "not": "none"});
              presentation only, validation is unchanged
"""

from __future__ import annotations

from . import config as cfg

WORKSPACE_CHOICES = 10

COMMAND_FIELDS = [
    {"key": "run", "kind": "string", "label": "Command", "example": "docker compose up",
     "help": "Typed into the tab exactly as written and followed by Enter, so anything your shell "
             "accepts works: npm run dev, docker compose up, rails s, cargo watch -x run."},
    {"key": "port", "kind": "integer", "label": "Port", "optional": True, "min": 1, "max": cfg.MAX_PORT, "example": "3000",
     "help": "The port this process listens on once it is up. Start checks it before typing anything: "
             "already served by this project means skip, held by another project means refuse. "
             "Stop waits for it to close after Ctrl-C. Leave empty for processes that do not listen, "
             "such as a file watcher; those are skipped when their tab is still busy."},
    {"key": "cwd", "kind": "path", "label": "Sub-folder", "optional": True, "relative": True, "example": "client",
     "help": "Run the process from this folder inside the project instead of the project root. "
             "For monorepos: client for the frontend, server for the API. Empty means the project root."},
    {"key": "name", "kind": "string", "label": "Name", "optional": True, "advanced": True, "example": "app",
     "help": "A short label for this process; its terminal tab is called omadev-<name>. "
             "Empty makes one from the command, so docker compose up becomes docker-compose-up."},
    {"key": "env", "kind": "list", "label": "Environment", "advanced": True, "item": {"kind": "string"}, "example": "PORT=3000",
     "help": "KEY=value entries set in the tab's shell when the tab is created. A tab that already "
             "exists keeps the environment it was created with."},
]

SETUP_FIELDS = [
    {"key": "run", "kind": "string", "label": "Command", "example": "npm install",
     "help": "Run to completion before the processes start, in the project's setup tab, where you can "
             "watch it. Start waits until the shell prompt is back."},
    {"key": "unless_exists", "kind": "path", "label": "Skip if this exists", "optional": True, "relative": True,
     "example": "node_modules",
     "help": "A file or folder, relative to the project, whose presence means this step is done. "
             "With node_modules here, npm install runs on a fresh clone and is skipped afterwards."},
    {"key": "cwd", "kind": "path", "label": "Sub-folder", "optional": True, "relative": True, "advanced": True, "example": "client",
     "help": "Run from this folder inside the project. Empty means the project root."},
    {"key": "name", "kind": "string", "label": "Name", "optional": True, "advanced": True, "example": "install",
     "help": "A short label shown in the step results. Empty makes one from the command."},
    {"key": "timeout", "kind": "integer", "label": "Timeout (seconds)", "optional": True, "advanced": True,
     "min": 1, "max": cfg.MAX_WAIT_TIMEOUT, "example": str(cfg.DEFAULT_SETUP_TIMEOUT),
     "help": "How long Start waits for this command. Default 600. When it runs out the step fails and "
             "the processes are not started."},
]

APP_FIELDS = [
    {"key": "launch", "kind": "argv", "label": "Launch command", "example": "omarchy-launch-tui lazydocker",
     "help": "Started when no window matches. Written like a shell command; {path} becomes the project "
             "folder and {name} the project name. Terminal apps: omarchy-launch-tui lazydocker. "
             "Desktop apps: postman, dbeaver. Web apps: omarchy-launch-webapp https://app.example.com"},
    {"key": "match", "kind": "regex", "label": "Window match", "example": "lazydocker",
     "help": "A regular expression tested against the window class and title (case-insensitive). "
             "When a window matches, Start focuses it instead of launching another. "
             "Find the class with: hyprctl clients -j | jq '.[].class'"},
    {"key": "workspace", "kind": "workspace", "label": "Workspace", "optional": True, "always": True,
     "help": "Hyprland workspace a newly opened window is moved to. A window that is already open stays "
             "where it is and is focused there."},
    {"key": "name", "kind": "string", "label": "Name", "optional": True, "advanced": True, "example": "lazydocker",
     "help": "How the app is called in the status line and step results. Empty makes one from the launch command."},
]

APP_PRESETS = [
    {"label": "lazydocker", "value": {"name": "lazydocker", "launch": ["omarchy-launch-tui", "lazydocker"], "match": "lazydocker"}},
    {"label": "lazygit", "value": {"name": "lazygit", "launch": ["omarchy-launch-tui", "lazygit"], "match": "lazygit"}},
    {"label": "btop", "value": {"name": "btop", "launch": ["omarchy-launch-tui", "btop"], "match": "btop"}},
    {"label": "Postman", "value": {"name": "Postman", "launch": ["postman"], "match": "postman"}},
    {"label": "Bruno", "value": {"name": "Bruno", "launch": ["bruno"], "match": "bruno"}},
    {"label": "DBeaver", "value": {"name": "DBeaver", "launch": ["dbeaver"], "match": "dbeaver"}},
]

EDITOR_FIELDS = [
    {"key": "share_window", "kind": "boolean", "label": "Open in the existing editor window", "always": True,
     "help": "Off: the project gets its own editor window, which Stop closes. On: the project is added "
             "to the editor window that is already open, next to whatever else is open there, using the "
             "shared launch command. Stop then leaves that window alone, because closing it would "
             "close the other projects too."},
    {"key": "launch", "kind": "argv", "label": "Launch command", "example": "zed {path}",
     "help": "How to open the project in the editor. {path} becomes the project folder."},
    {"key": "launch_shared", "kind": "argv", "label": "Shared launch command", "optional": True, "example": "zed --add {path}",
     "help": "Used instead of the launch command when the toggle is on. Zed, VS Code and Cursor all "
             "take --add to put a folder into the current window. Empty falls back to the launch command."},
    {"key": "match", "kind": "regex", "label": "Window class match", "optional": True, "example": r"^dev\.zed\.Zed$",
     "help": "The editor's window class. Start focuses a window with this class whose title contains the "
             "project folder name; Stop closes it. Leave empty to launch every time and let the editor "
             "reuse its own window, which Zed, VS Code and Cursor do for an already open folder."},
]

EDITOR_PRESETS = [
    {"label": "Omarchy default editor", "value": None},
    {"label": "Zed", "value": {"launch": ["zed", "{path}"], "launch_shared": ["zed", "--add", "{path}"], "match": r"^dev\.zed\.Zed$"}},
    {"label": "VS Code", "value": {"launch": ["code", "{path}"], "launch_shared": ["code", "--add", "{path}"], "match": r"^(code|Code)"}},
    {"label": "Cursor", "value": {"launch": ["cursor", "{path}"], "launch_shared": ["cursor", "--add", "{path}"], "match": r"^(cursor|Cursor)"}},
]

WORKSPACES_FIELDS = [
    {"key": "terminal", "kind": "workspace", "label": "Terminal workspace", "optional": True,
     "help": "Where a terminal that Start opens goes: the herdr or tmux terminal, or each process window "
             "when running in separate terminal windows. A terminal that is already open stays put."},
    {"key": "browser", "kind": "workspace", "label": "Browser workspace", "optional": True,
     "help": "Where a new browser window or web app window goes. A tab opened in an existing browser "
             "window stays with that window."},
    {"key": "editor", "kind": "workspace", "label": "Editor workspace", "optional": True,
     "help": "Where a new editor window goes. Needs the editor's window class match to find the window. "
             "With a shared editor window, the existing window stays where it is."},
]

PROJECT_FIELDS = [
    {"key": "name", "kind": "string", "label": "Name", "example": "kadha",
     "help": "How the project appears in the list, and the name you pass to omadev start. "
             "Letters, digits, space, dot, underscore and dash. Unless a session name is set, "
             "it is also the herdr workspace label or tmux session name Start looks for."},
    {"key": "path", "kind": "path", "label": "Folder", "example": "~/Development/kadha",
     "help": "The project directory. Processes run here, the editor opens it, and the multiplexer "
             "workspace is created here. ~ is allowed."},
    {"key": "multiplexer", "kind": "enum", "label": "Run processes in", "default": "herdr",
     "options": [{"value": "herdr", "label": "a herdr workspace, one tab per process"},
                 {"value": "tmux", "label": "a tmux session, one window per process"},
                 {"value": "none", "label": "separate terminal windows"}],
     "help": "Start reuses what exists and never types into a pane that is busy. With separate terminal "
             "windows, Stop closes each window rather than interrupting the process gently."},
    {"key": "session", "kind": "string", "label": "Workspace or session name", "optional": True, "example": "kadha",
     "visible_if": {"key": "multiplexer", "not": "none"},
     "help": "Only needed when your existing herdr workspace or tmux session is not named after the "
             "project. Start attaches to the workspace with this label instead of creating one."},
    {"key": "setup", "kind": "list", "label": "Setup commands", "item": {"kind": "object", "fields": SETUP_FIELDS},
     "help": "One-shot commands run to completion before the processes: npm install, docker compose pull, "
             "make deps. Each can be skipped when a file or folder already exists, so they cost nothing "
             "once done."},
    {"key": "commands", "kind": "list", "label": "Processes", "item": {"kind": "object", "fields": COMMAND_FIELDS},
     "help": "Long-running processes the project needs: dev servers, watchers, databases. Each gets its "
             "own tab. Start sends a process only if its port is not already served; Stop interrupts "
             "it with Ctrl-C, waits for the port to close, and closes the tab."},
    {"key": "url", "kind": "url", "label": "Page to open", "optional": True, "example": "http://localhost:3000",
     "help": "A page to open once the project is up. Start waits until it answers HTTP, not just until "
             "the port is open, then opens it. Also shown as up or down in the list. Leave empty for "
             "projects without a page."},
    {"key": "browser", "kind": "enum", "label": "Open the page as", "optional": True,
     "visible_if": {"key": "url"},
     "options": [{"value": "", "label": "Default"}, {"value": "browser", "label": "Tab in the browser"},
                 {"value": "webapp", "label": "Web app window"}],
     "help": "Tab in the browser: opened with xdg-open in your running browser; a second Start cannot "
             "tell whether the tab is still open, so it only opens one when it started the server itself. "
             "Web app window: a separate window without tabs, so Start can focus it, place it and close it."},
    {"key": "editor", "kind": "object", "label": "Editor", "optional": True, "fields": EDITOR_FIELDS,
     "presets": EDITOR_PRESETS,
     "help": "Opened on the project folder by Start and closed by Stop. Pick a preset, or Custom to set "
             "the launch command and window class yourself. Omarchy default uses whatever "
             "omarchy default editor is set to."},
    {"key": "apps", "kind": "list", "label": "Helper apps",
     "item": {"kind": "object", "fields": APP_FIELDS, "presets": APP_PRESETS},
     "help": "Other windows you want alongside the project: lazydocker, Postman, a database client, "
             "a second web app. Start focuses each one if a window matches, launches it otherwise; "
             "Stop closes them."},
    {"key": "stop_commands", "kind": "list", "label": "Stop commands", "item": {"kind": "string"},
     "example": "docker compose down",
     "help": "Run in the project folder on Stop, after the processes have been interrupted. Use them for "
             "cleanup that Ctrl-C does not do, such as docker compose down. Written like a shell "
             "command but run without a shell: quoting works, pipes and variables do not."},
    {"key": "mode", "kind": "enum", "label": "Start order", "default": "sequential",
     "options": [{"value": "sequential", "label": "Wait for the page before opening editor and apps"},
                 {"value": "parallel", "label": "Open editor and apps right away"}],
     "help": "Sequential keeps the terminal in front while the server comes up, then opens everything "
             "once the page answers. Parallel opens the editor and apps immediately and lets the browser "
             "follow when the page is ready."},
    {"key": "wait_timeout", "kind": "integer", "label": "Wait timeout (seconds)", "default": cfg.DEFAULT_WAIT_TIMEOUT,
     "min": 1, "max": cfg.MAX_WAIT_TIMEOUT, "example": str(cfg.DEFAULT_WAIT_TIMEOUT),
     "help": "How long Start waits for the page to answer before giving up on the browser step, and how "
             "long Stop waits for a process to end after Ctrl-C before leaving its tab open. "
             "Raise it for docker builds that take minutes."},
    {"key": "workspaces", "kind": "object", "label": "Hyprland workspaces", "optional": True, "fields": WORKSPACES_FIELDS,
     "help": "Where windows go when Start opens them fresh. Anything already open, on any workspace, "
             "is focused where it is and never moved."},
]

# How the form lays the fields out. Dotted keys reach into stored objects.
SECTIONS = [
    {"key": "project", "title": "Project", "fields": ["name", "path"]},
    {"key": "terminal", "title": "Terminal", "fields": ["multiplexer", "session", "workspaces.terminal"]},
    {"key": "setup", "title": "Setup", "fields": ["setup"]},
    {"key": "processes", "title": "Processes", "fields": ["commands"]},
    {"key": "page", "title": "Page", "fields": ["url", "browser", "workspaces.browser"]},
    {"key": "editor", "title": "Editor", "fields": ["editor", "workspaces.editor"]},
    {"key": "apps", "title": "Helper apps", "fields": ["apps"]},
    {"key": "advanced", "title": "Advanced", "collapsed": True, "fields": ["stop_commands", "mode", "wait_timeout"]},
]


def _field(key: str) -> dict:
    """The spec for a top-level or dotted key, with `key` set to the full path."""
    if "." in key:
        parent_key, child_key = key.split(".", 1)
        parent = next(f for f in PROJECT_FIELDS if f["key"] == parent_key)
        child = next(f for f in parent["fields"] if f["key"] == child_key)
        return {**child, "key": key}
    # A copy, so callers such as describe() can adjust labels without
    # touching the module-level schema.
    return dict(next(f for f in PROJECT_FIELDS if f["key"] == key))


def sections() -> list[dict]:
    """Sections with their field specs resolved, as the form renders them."""
    return [{**section, "fields": [_field(key) for key in section["fields"]]} for section in SECTIONS]


def describe(default_browser: str = "webapp") -> dict:
    """What the CLI hands the widget for `omadev schema --json`.

    `default_browser` names the file's default so the "Default" choice says
    what it will actually do.
    """
    label = {"webapp": "web app window", "browser": "tab in the browser"}.get(default_browser, default_browser)
    resolved = sections()
    for section in resolved:
        for field in section["fields"]:
            if field["key"] == "browser":
                options = [dict(o) for o in field["options"]]
                options[0]["label"] = f"Default ({label})"
                field["options"] = options
    return {"fields": PROJECT_FIELDS, "sections": resolved, "version": cfg.SCHEMA_VERSION}

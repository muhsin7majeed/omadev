# omadev — Omarchy project launcher

Guidance for humans and AI agents working in this repository. Read it fully before
changing anything.

## Goal

A bar plugin for [Omarchy](https://omarchy.org) that starts and stops a whole
development project with one click, reliably and idempotently.

Today the owner opens a project by hand: open a terminal, attach to the
multiplexer, run the dev server, open the browser at localhost, open the editor,
open helper apps (lazydocker, Postman, a SQL client). After every reboot or
project switch this is repeated. Herdr and tmux remember layout, nothing
remembers the rest.

The plugin shows the configured projects in a popup from the bar. Each project
has **Start** and **Stop**. Start never duplicates what already exists: it
attaches to a running multiplexer workspace, skips a dev server whose port is
already listening, and focuses an already open browser, editor or app window
instead of launching a second one. Stop tears the same things down in reverse.

Non-goals: replacing the multiplexer, managing git, managing Docker beyond the
commands a project declares, running anything as root.

## Working rules for AI agents

1. **Ask before every file change and before every commit.** Describe what will
   change and wait for a yes. One confirmation covers one change set, not the
   session. This is the owner's explicit instruction.
2. **Check first, then act.** Read the relevant code, the Omarchy shell sources
   under `/usr/share/omarchy/shell/` (read only) and the tool docs before
   proposing a change. If information is missing, ask; do not guess.
3. **Never edit `/usr/share/omarchy/`.** It is owned by the omarchy package.
4. **No new runtime dependencies.** The CLI is Python 3 standard library only.
   The widget uses only what the Omarchy shell already ships (Quickshell, the
   `qs.Commons` and `qs.Ui` modules).
5. **Report outcomes faithfully.** If a test fails, quote it. If a step was
   skipped, say so.

## Architecture

Two layers with a hard boundary. The widget renders; the CLI decides and acts.
A UI bug must never leave a project half started, and the CLI must be fully
usable from a terminal or keybinding without the widget.

```
manifest.json          Omarchy plugin manifest (id: potato.omadev, kind: bar-widget)
Widget.qml             bar icon + popup panel; calls the CLI, renders its JSON
components/            small QML pieces used by Widget.qml
bin/omadev             executable entry point (python3, no build step)
omadev_cli/            the CLI package: config, checks, steps, runner, output
tests/                 unittest suites for the CLI (run: python3 -m unittest)
scripts/dev-install    rsync the repo into ~/.config/omarchy/plugins/potato.omadev/
```

### The CLI (`bin/omadev`)

- `omadev list --json` — projects with their configured fields.
- `omadev status [<project>] --json` — live state per project: multiplexer
  workspace present, port listening, browser/editor/app windows present.
  Runs the checks once and exits. Nothing polls.
- `omadev start <project> [--json]` — runs the start steps in order.
- `omadev stop <project> [--json]` — runs the stop steps in reverse.
- `omadev add|edit|remove <project> ...` — edits the projects file; the widget's
  form calls these instead of writing JSON itself.
- `omadev validate` — checks the projects file and reports problems.

Every step is **check, then act**, and reports one of `skipped`, `started`,
`focused`, `stopped`, `failed` with a short reason. Output is human text by
default and one JSON document with `--json`. Exit code 0 only when every step
succeeded or was skipped.

Start order (sequential is the default; `mode: "parallel"` runs the
independent tail steps together):

1. multiplexer workspace or session for the project path (herdr or tmux)
2. commands, each in its own pane or window, only if their port is not listening
3. wait for the project URL's port to accept connections (bounded timeout)
4. browser at the URL (`webapp` window or tab in the existing browser)
5. editor on the project path
6. extra apps

Stop order: send Ctrl-C to command panes and wait for their ports to close,
run declared `stop_commands` (for example `docker compose down`), close the
windows Start opened or focused, then close the workspace or session.

### Idempotency checks and their sources

| What | How it is checked | How it is acted on |
|------|-------------------|--------------------|
| herdr workspace | `herdr workspace list` JSON, match on `label` | `herdr workspace create --cwd --label`, `herdr pane run` |
| tmux session | `tmux has-session -t` | `tmux new-session -d`, `tmux send-keys` |
| dev server | TCP connect to the URL's host and port | run the command in a pane |
| browser (webapp) | `hyprctl clients -j`, match window class | `omarchy-launch-webapp`, else `hyprctl dispatch focuswindow` |
| browser (tab) | not detectable; opened only when the server was not already up | `xdg-open` |
| editor, apps | `hyprctl clients -j`, match class or title | `omarchy-launch-or-focus` or `uwsm-app` |

Prefer the `omarchy-launch-*` commands over reimplementing them; they already
encode how Omarchy launches terminals, TUIs, web apps and editors.

### Configuration

Projects live outside the plugin directory so a plugin update never touches
them: `~/.config/omadev/projects.json`, with a top-level `"version": 1`.
State and logs live in `~/.local/state/omadev/`. Per-widget display options
(popup width and similar) live inline in `shell.json` like every Omarchy widget.

Per project: `name`, `path`, `multiplexer` (`herdr` | `tmux`), `session`
(workspace label or tmux session name), `commands` (list of `{name, run, port}`),
`url`, `browser` (`webapp` | `browser`, overriding the global default),
`editor`, `apps` (list of `{name, launch, match}`), `stop_commands`, `mode`.

### The widget (`Widget.qml`)

- Extends `BarWidget` from `qs.Ui`; the popup uses the shell's `Panel`.
- All colors and spacing come from `Color` and `Style` in `qs.Commons` and from
  the injected `bar` object. No hard-coded colors, fonts or pixel sizes, so
  every Omarchy theme applies automatically.
- Shows three states: CLI missing (with the install hint), no projects (with
  the add form), and the project list with Start/Stop and live step results.
- The add/edit form collects the fields above and calls `omadev add`/`edit`.

### Idle behaviour

The shell is a long-running process shared with the whole desktop, so this
widget must cost nothing while nobody is looking at it.

- **No `Timer` polling.** Status is fetched when the popup opens and after a
  Start or Stop finishes, never on an interval.
- The projects file is watched with `FileView { watchChanges: true }`, which
  uses inotify, not polling.
- Every `Process` is short-lived and owned by the widget; none is left running
  when the popup closes. Start and Stop run to completion in the CLI, which
  detaches launched apps (`setsid`) so the CLI exits promptly.
- Only one instance of the popup content exists; the bar creates one widget per
  monitor, so anything stateful lives in a single place and is looked up, not
  duplicated per screen.

## Reliability, security, readability

- Never `shell=True`. Commands are argv lists. The only place a user-written
  command string is executed is inside the user's own multiplexer pane, which is
  the documented purpose of the `run` field.
- Every subprocess has a timeout. Every external call handles a missing binary
  with a clear message naming what to install.
- Validate the projects file on load: unknown fields warn, wrong types fail
  with the field name, paths must exist, ports must be 1–65535.
- No `sudo`, no network access other than local TCP connect checks, no writes
  outside `~/.config/omadev/` and `~/.local/state/omadev/`.
- Small functions, explicit names, docstrings that say why. Type hints on
  every public function. Prefer boring code over clever code.
- Errors surface to the user in the popup with the failing step and reason.
  Logs go to `~/.local/state/omadev/omadev.log`, rotated by size.

## Distribution

Hassle-free install is a requirement, which is why the CLI is a Python script
inside the plugin directory rather than a compiled binary.

```
omarchy plugin add https://github.com/<owner>/omadev.git
omarchy plugin enable potato.omadev
```

Users have Python 3 already (it is a dependency of core Omarchy packages).
Target Python 3.11+. Optional tools (herdr, tmux, a browser, an editor) are
detected at run time; the widget names what is missing.

## Development workflow

```bash
python3 -m unittest discover -s tests      # CLI tests
python3 -m compileall -q omadev_cli bin    # syntax check
omarchy plugin validate .                  # manifest check
scripts/dev-install                        # copy into the live plugin dir; the shell hot-reloads
journalctl --user -u omarchy-shell -f      # or: omarchy-shell shell rescanPlugins
```

The plugin directory must not contain symlinks (the validator rejects them),
so development copies files in rather than linking the repo.

## Decisions log

- 2026-09-19 Python 3 stdlib for the CLI, not Rust: the Omarchy installer only
  clones a repo and never runs build or install steps, so a compiled binary
  would need a separate AUR package. Zero-install wins.
- 2026-09-19 Plugin id `potato.omadev`. Valid per the registry regex; the
  author prefix is convention only.
- 2026-09-19 Browser mode is configurable per project with a global default,
  values `webapp` and `browser`.
- 2026-09-19 Confirmation before edits and commits is a chat rule, not enforced
  by harness settings.

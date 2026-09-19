# omadev

Start and stop a whole development project from the Omarchy bar with one
click: terminal workspace, dev servers, browser, editor and helper apps.
Start never duplicates what is already running. Stop tears down only what
Start brought up.

Built for [Omarchy](https://omarchy.org) 4 with Hyprland, herdr or tmux, and
any editor.

## What it does

Click the project icon in the bar. Each configured project shows whether it is
up, with **Start** and **Stop** buttons.

**Start** works through a fixed list of steps, and every step looks before it
acts:

| Step | What happens | When it is skipped |
|------|--------------|--------------------|
| workspace | herdr workspace or tmux session for the project folder | it already exists |
| command | each command in its own tab, named `omadev-<name>` | its port is already served by this project, or the tab is busy |
| terminal | focus the terminal attached to herdr or tmux, or open one | never |
| wait | wait until the URL answers HTTP | it already answers |
| browser | open the URL, as a browser tab or a web app window | the web app window is open (focused instead) |
| editor | open the project in the editor | a window for it is open (focused instead) |
| apps | open each helper app | a window for it is open (focused instead) |

A port held by **another** project is refused, not reused, so Start never
opens the browser on the wrong site. Pressing Start twice is harmless.

**Stop** interrupts each command with Ctrl-C, waits for its port to close,
closes its tab, runs any stop commands (for example `docker compose down`),
and closes the editor, app and web app windows. Your workspace, your other
tabs, your terminal and your browser tabs are left alone.

## Install

```bash
omarchy plugin add https://github.com/<owner>/omadev.git
omarchy plugin enable potato.omadev
```

The plugin appears in the bar's right section. Everything runs from the
plugin directory; nothing else is installed. Requirements: Python 3.11 or
newer (already present on Omarchy), plus whichever of herdr or tmux you use.

## Configure a project

Click `+` in the popup. Every field has a `?` with an explanation; the `?` in
the header shows all of them at once. The essentials:

- **Name** and **Folder**.
- **Commands**: each long-running process, such as `docker compose up` or
  `npm run dev`, with the **port** it listens on. The port is how Start knows
  it is already running and how Stop knows it has stopped.
- **URL**: what to open once it answers, for example `http://localhost:3000`.
- **Open the URL as**: a tab in your browser, or a web app window. A web app
  window can be recognised and focused again later; a tab cannot.
- **Editor**: pick Zed, VS Code, Cursor or Omarchy's default. "Open in the
  existing editor window" adds the project to the editor window already open,
  next to your other projects, instead of opening a new one.
- **Helper apps**: lazydocker, Postman, a database client. Each has a launch
  command and a window match so it is focused rather than relaunched.

Projects are stored in `~/.config/omadev/projects.json`. The file is validated
on every read, and a plugin update never touches it.

### Example

```json
{
  "version": 1,
  "projects": [
    {
      "name": "kadha",
      "path": "~/Development/kadha",
      "multiplexer": "herdr",
      "commands": [{ "name": "app", "run": "docker compose up", "port": 3000 }],
      "url": "http://localhost:3000",
      "browser": "browser",
      "editor": { "launch": ["zed", "{path}"], "match": "^dev\\.zed\\.Zed$" }
    }
  ]
}
```

## The command line

The widget is a thin face over a CLI you can use directly, or bind to a key:

```bash
omadev list
omadev status
omadev start kadha --dry-run     # show the plan, change nothing
omadev start kadha
omadev stop kadha
omadev add '{"name": "demo", "path": "~/dev/demo", "url": "http://localhost:5173"}'
omadev edit demo '{...}'
omadev remove demo
```

`omadev` lives at `~/.config/omarchy/plugins/potato.omadev/bin/omadev`. Add
`--json` to any command for machine-readable output. Step results are logged
to `~/.local/state/omadev/omadev.log`.

To open the popup from a key, add to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + P", "Projects", "omarchy-shell potato.omadev toggle")
```

## Known limits

- **Browser tabs cannot be detected.** In tab mode, Start opens the URL only
  when it started the server itself, and Stop leaves tabs alone. Use web app
  mode for a window that can be focused and closed.
- **One project per port.** Ownership is worked out from the listening
  process's folder, or from the container's compose folder for docker. A port
  whose owner cannot be seen is left alone and reported.
- **Editors without a window match** are launched every time and trusted to
  reuse their own window, which Zed, VS Code and Cursor do.
- **Stop is polite.** It sends Ctrl-C and waits. A command that ignores it is
  reported and its tab left open; nothing is killed.

## How it is built

Two layers with a hard boundary. The QML widget renders; the Python CLI
decides and acts. The CLI is standard library only, runs no shell, gives every
subprocess a timeout, and writes only under `~/.config/omadev/` and
`~/.local/state/omadev/`. The widget uses the shell's own theme tokens and
components, has no timers, and runs nothing while the popup is closed.

Development notes, the design, and the rules for changing the code are in
[AGENTS.md](AGENTS.md). Tests:

```bash
python3 -m unittest discover -s tests -t .
```

## License

MIT. See [LICENSE](LICENSE).

# omadev

**Project sessions for Omarchy.** Bring a whole project up or down in one
click: terminal workspace, processes, page, editor, tools. Never twice.

Start converges your desktop toward what a project needs and never duplicates
what is already running. Stop tears down only what Start brought up. Built for
[Omarchy](https://omarchy.org) 4 with Hyprland; works with herdr, tmux, or
plain terminal windows, and with any editor.

## What a session is

A project declares what it needs. Start makes it so, checking before every
action:

| Step | What happens | When nothing happens |
|------|--------------|----------------------|
| workspace | herdr workspace or tmux session for the project folder | it already exists |
| setup | one-shot commands such as `npm install`, run to completion | their marker path exists |
| processes | each long-running process in its own tab, named `omadev-<name>` | its port is already served by this project, or the tab is busy |
| terminal | focus the terminal attached to herdr or tmux, or open one | never |
| wait | wait until the page answers HTTP | it already answers |
| page | open the page, as a browser tab or a web app window | the web app window is open (focused instead) |
| editor | open the project in the editor | a window for it is open (focused instead) |
| apps | open each helper app | a window for it is open (focused instead) |

A port held by **another** project is refused, not reused, so Start never
opens the page of the wrong project. Two projects that both want port 3000
cannot run at once; Stop the one and Start the other. A process that dies
right after starting, because its port was taken or the command is missing,
is reported as failed rather than started. Pressing Start twice is harmless,
and Start after a reboot resumes rather than duplicates.

**Stop** interrupts each process with Ctrl-C, waits for its port to close,
closes its tab, runs any stop commands (for example `docker compose down`),
and closes the editor, app and web app windows. Your workspace, your other
tabs, your terminal and your browser tabs are left alone.

**Workspaces.** Windows that Start opens itself can be placed: browser on 1,
terminal on 2, editor on 3. Anything already open, on any workspace, is
focused where it is and never moved.

## Install

```bash
omarchy plugin add https://github.com/muhsin7majeed/omadev.git
```

Omarchy warns that plugins run unsandboxed, clones the repository, and asks
whether to enable the plugin. Say yes and the rocket appears in the bar's
right section. If you said no, or want to enable it later:

```bash
omarchy plugin enable potato.omadev
```

Everything runs from the plugin directory; nothing else is installed.
Requirements: Python 3.11 or newer (already present on Omarchy), plus herdr
or tmux if you use one. Updates come with `omarchy plugin update`.

## Configure a project

Click `+` in the popup. Every field has a `?` with an explanation; the `?` in
the header shows all of them at once. The essentials:

- **Name** and **Folder**.
- **Terminal multiplexer**: herdr, tmux, or none for plain terminal windows.
- **Setup commands**: run once before the processes, such as `npm install`
  skipped while `node_modules` exists.
- **Processes**: each long-running process, such as `docker compose up` or
  `npm run dev`, with the **port** it listens on, an optional sub-folder for
  monorepos, and environment entries.
- **Page to open**: for example `http://localhost:3000`, as a browser tab or
  a web app window. A web app window can be recognised and focused again
  later; a tab cannot.
- **Editor**: Zed, VS Code, Cursor or Omarchy's default. "Open in the
  existing editor window" adds the project to the editor window already
  open, next to your other projects, instead of opening a new one.
- **Helper apps**: lazydocker, Postman, a database client, each with a
  launch command, a window match, and optionally a workspace.
- **Hyprland workspaces**: where new terminal, browser and editor windows go.

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
      "setup": [{ "name": "install", "run": "npm install", "unless_exists": "node_modules" }],
      "commands": [{ "name": "app", "run": "docker compose up", "port": 3000 }],
      "url": "http://localhost:3000",
      "browser": "browser",
      "editor": { "launch": ["zed", "{path}"], "match": "^dev\\.zed\\.Zed$" },
      "workspaces": { "browser": 1, "terminal": 2, "editor": 3 }
    }
  ]
}
```

## Beyond web projects

Only the page is web-shaped, and it is optional. A Rust service is a process
with a port; a compiler in watch mode is a process without one, left alone
while its tab is busy; a notebook is a page; an emulator, a serial monitor or
a database client is a helper app. Setup commands cover `cargo fetch`,
`poetry install` or `make deps` the same way they cover `npm install`.

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

- **Browser tabs cannot be detected.** In tab mode, Start opens the page only
  when it started the server itself, and Stop leaves tabs alone. Use web app
  mode for a window that can be focused, placed and closed.
- **One project per port.** Ownership is worked out from the listening
  process's folder, or from the container's compose folder for docker. A port
  whose owner cannot be seen is left alone and reported.
- **Editors without a window match** are launched every time, trusted to
  reuse their own window, and cannot be placed on a workspace.
- **Stop is polite.** It sends Ctrl-C and waits. A process that ignores it is
  reported and its tab left open; nothing is killed. In "none" mode Stop
  closes the process's terminal window instead, which hangs the process up.
- **Setup commands** report done when the shell prompt returns; their exit
  status is not read.

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

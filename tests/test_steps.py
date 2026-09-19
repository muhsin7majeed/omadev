"""End-to-end tests of the Start flow against faked herdr, tmux and Hyprland."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omadev_cli import config as cfg
from omadev_cli import ports, steps
from omadev_cli.system import CommandResult
from tests.fakes import FakeServices, window
from tests.test_herdr import BUSY, IDLE, PANES, TABS, WORKSPACES


def kadha(path: Path, **overrides: object) -> cfg.Config:
    project = {
        "name": "kadha",
        "path": str(path),
        "multiplexer": "herdr",
        "commands": [{"name": "app", "run": "docker compose up", "port": 3000}],
        "url": "http://localhost:3000",
        "browser": "browser",
        "editor": {"launch": ["zed", "{path}"], "match": r"^dev\.zed\.Zed$"},
        "apps": [{"name": "lazydocker", "launch": ["omarchy-launch-tui", "lazydocker"], "match": "lazydocker"}],
    }
    project.update(overrides)
    return cfg.parse({"version": 1, "projects": [project]}, check_paths=False)


def by_step(results: list[steps.StepResult]) -> dict[str, steps.StepResult]:
    return {r.step: r for r in results}


class StartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "kadha"
        self.path.mkdir()
        self.fake = FakeServices()
        self.runner = self.fake.runner

    def start(self, config: cfg.Config, **kwargs: object) -> dict[str, steps.StepResult]:
        project = config.projects[0]
        results = steps.start(project, config, self.fake.build(), **kwargs)
        return by_step(results)

    def everything_running(self) -> None:
        """Workspace, tab, server, terminal, editor and app all exist."""
        self.runner.on_json("herdr", "workspace", "list", result=WORKSPACES)
        self.runner.on_json("herdr", "tab", "list", result=TABS)
        self.runner.on_json("herdr", "pane", "list", result=PANES)
        self.runner.on_json("herdr", "pane", "process-info", result=BUSY)
        self.runner.on("herdr", "workspace", "focus")
        self.runner.on("hyprctl", "dispatch", stdout="ok\n")
        # Port 3000 is published by kadha's own compose stack: ss sees only
        # the root-run docker proxy, docker ps attributes it to the project.
        self.fake.open_ports = {3000}
        self.fake.listeners[3000] = ports.Listener(pid=None, name="", cwd=None)
        self.fake.containers = [ports.Container("kadha-client-1", frozenset({3000}), self.path)]
        self.fake.processes = [(100, ["herdr"]), (1, ["/usr/bin/herdr", "server"])]
        self.fake.windows = [
            window(address="0xc", cls="com.mitchellh.ghostty", title="nitrogen: kadha", pid=50),
            window(address="0xa", cls="dev.zed.Zed", title="kadha — App.tsx", pid=7),
            window(address="0xe", cls="org.omarchy.lazydocker", title="lazydocker", pid=8),
        ]
        # /proc: herdr client 100 -> bash 60 -> ghostty 50
        proc = Path(self.tmp.name) / "proc"
        for pid, ppid in ((50, 1), (60, 50), (100, 60)):
            (proc / str(pid)).mkdir(parents=True)
            (proc / str(pid) / "status").write_text(f"PPid:\t{ppid}\n")
        self.fake.proc_root = proc

    def test_second_start_changes_nothing(self) -> None:
        self.everything_running()
        results = self.start(kadha(self.path))

        self.assertEqual(results["workspace"].status, steps.SKIPPED)
        self.assertIn("w5", results["workspace"].detail)
        self.assertEqual(results["command:app"].status, steps.SKIPPED)
        self.assertIn("kadha-client-1", results["command:app"].detail)
        self.assertEqual(self.fake.docker_asked, 1, "docker ps is asked at most once per run")
        self.assertEqual(results["terminal"].status, steps.FOCUSED)
        self.assertEqual(results["terminal:focus"].status, steps.FOCUSED)
        self.assertEqual(results["wait"].status, steps.SKIPPED)
        self.assertEqual(results["browser"].status, steps.SKIPPED)
        self.assertEqual(results["editor"].status, steps.FOCUSED)
        self.assertEqual(results["app:lazydocker"].status, steps.FOCUSED)
        self.assertTrue(steps.succeeded(list(results.values())))

        self.assertEqual(self.runner.detached, [])
        focus_calls = [c[2] for c in self.runner.calls if c[:2] == ("hyprctl", "dispatch")]
        self.assertEqual(focus_calls, [f'hl.dsp.focus({{ window = "address:{a}" }})' for a in ("0xc", "0xa", "0xe")])
        self.assertIn(("herdr", "workspace", "focus", "w5"), self.runner.calls)

    def test_busy_pane_is_never_typed_into(self) -> None:
        self.everything_running()
        self.fake.open_ports = set()          # port down, but the tab is busy (compose still starting)
        self.fake.ports_after_wait = {3000}
        results = self.start(kadha(self.path))
        self.assertEqual(results["command:app"].status, steps.SKIPPED)
        self.assertIn("busy", results["command:app"].detail)
        self.assertNotIn(("herdr", "pane", "run", "w5:p9", "docker compose up"), self.runner.calls)
        self.assertEqual(results["wait"].status, steps.STARTED)
        self.assertEqual(results["browser"].status, steps.STARTED)
        self.assertIn(("xdg-open", "http://localhost:3000"), self.runner.detached)

    def test_idle_existing_tab_gets_the_command(self) -> None:
        self.everything_running()
        self.fake.open_ports = set()
        self.fake.ports_after_wait = {3000}
        self.runner.on_json("herdr", "pane", "process-info", result=IDLE)
        self.runner.on("herdr", "pane", "run")
        results = self.start(kadha(self.path))
        self.assertEqual(results["command:app"].status, steps.STARTED)
        self.assertIn(("herdr", "pane", "run", "w5:p9", "docker compose up"), self.runner.calls)

    def test_fresh_machine_creates_everything(self) -> None:
        self.runner.on_json("herdr", "workspace", "list", result={"workspaces": []})
        self.runner.on_json("herdr", "workspace", "create", result={"workspace": {"workspace_id": "w9"}, "tab": {"tab_id": "w9:t1"}, "root_pane": {"pane_id": "w9:p1"}})
        self.runner.on_json("herdr", "tab", "create", result={"tab": {"tab_id": "w9:t2"}, "root_pane": {"pane_id": "w9:p2"}})
        self.runner.on("herdr", "pane", "run")
        self.fake.ports_after_wait = {3000}
        results = self.start(kadha(self.path))

        self.assertEqual(results["workspace"].status, steps.STARTED)
        self.assertEqual(results["command:app"].status, steps.STARTED)
        self.assertIn(("herdr", "tab", "create", "--workspace", "w9", "--cwd", str(self.path), "--label", "omadev-app", "--no-focus"), self.runner.calls)
        self.assertIn(("herdr", "pane", "run", "w9:p2", "docker compose up"), self.runner.calls)
        self.assertEqual(results["terminal"].status, steps.STARTED)
        self.assertIn(("omarchy-launch-terminal", "herdr"), self.runner.detached)
        self.assertEqual(results["wait"].status, steps.STARTED)
        self.assertEqual(results["browser"].status, steps.STARTED)
        self.assertEqual(results["editor"].status, steps.STARTED)
        self.assertIn(("uwsm-app", "--", "zed", str(self.path)), self.runner.detached)
        self.assertEqual(results["app:lazydocker"].status, steps.STARTED)
        self.assertIn(("omarchy-launch-tui", "lazydocker"), self.runner.detached)
        # No tab lookup on a workspace this run just created.
        self.assertNotIn(("herdr", "tab", "list", "--workspace", "w9"), self.runner.calls)

    def test_dry_run_touches_nothing(self) -> None:
        self.runner.on_json("herdr", "workspace", "list", result={"workspaces": []})
        results = self.start(kadha(self.path), dry_run=True)
        self.assertEqual({r.status for r in results.values()}, {steps.PLANNED})
        self.assertEqual(self.runner.detached, [])
        self.assertEqual(self.runner.mutating_calls(), [])

    def test_dry_run_reports_existing_state(self) -> None:
        self.everything_running()
        results = self.start(kadha(self.path), dry_run=True)
        self.assertEqual(results["workspace"].status, steps.SKIPPED)
        self.assertEqual(results["command:app"].status, steps.SKIPPED)
        self.assertEqual(results["terminal"].status, steps.PLANNED)
        self.assertEqual(self.runner.mutating_calls(), [])

    def test_unreachable_url_skips_browser_and_fails_wait(self) -> None:
        self.runner.on_json("herdr", "workspace", "list", result=WORKSPACES)
        self.runner.on_json("herdr", "tab", "list", result={"tabs": []})
        self.runner.on_json("herdr", "tab", "create", result={"tab": {"tab_id": "w5:t8"}, "root_pane": {"pane_id": "w5:p10"}})
        self.runner.on("herdr", "pane", "run")
        self.runner.on("herdr", "workspace", "focus")
        results = self.start(kadha(self.path))
        self.assertEqual(results["wait"].status, steps.FAILED)
        self.assertEqual(results["browser"].status, steps.SKIPPED)
        self.assertFalse(steps.succeeded(list(results.values())))
        self.assertEqual(self.fake.waited, [("http://localhost:3000", 90.0)])
        self.assertIn("nothing is listening", results["wait"].detail)

    def test_open_port_without_http_answer_is_not_ready(self) -> None:
        # Docker binds the port before the app answers; Start must wait for HTTP.
        self.everything_running()
        self.fake.http_dead = {"http://localhost:3000"}
        results = self.start(kadha(self.path))
        self.assertEqual(results["command:app"].status, steps.SKIPPED)
        self.assertEqual(results["wait"].status, steps.STARTED)
        self.assertIn("responds after", results["wait"].detail)
        self.assertEqual(self.fake.waited, [("http://localhost:3000", 90.0)])
        # The server was not answering before this run, so the tab is opened.
        self.assertEqual(results["browser"].status, steps.STARTED)

    def test_herdr_missing_fails_workspace_and_skips_dependents(self) -> None:
        self.fake.runner.tools.discard("herdr")
        results = self.start(kadha(self.path, url=None))
        self.assertEqual(results["workspace"].status, steps.FAILED)
        self.assertEqual(results["command:app"].status, steps.SKIPPED)
        self.assertEqual(results["terminal"].status, steps.SKIPPED)
        self.assertEqual(results["editor"].status, steps.STARTED)

    def test_server_not_running_is_started_first(self) -> None:
        # The first two `workspace list` calls fail (server down), later ones
        # succeed once the terminal has brought the server up.
        answers = {"n": 0}

        def workspace_list(argv: tuple[str, ...]) -> CommandResult | None:
            if argv[:3] != ("herdr", "workspace", "list"):
                return None
            answers["n"] += 1
            if answers["n"] <= 2:
                return CommandResult(argv, 1, "", "connection refused")
            return CommandResult(argv, 0, '{"result": {"workspaces": []}}', "")

        self.runner.respond_with(workspace_list)
        self.runner.on_json("herdr", "workspace", "create", result={"workspace": {"workspace_id": "w1"}})
        self.runner.on_json("herdr", "tab", "create", result={"tab": {"tab_id": "w1:t2"}, "root_pane": {"pane_id": "w1:p2"}})
        self.runner.on("herdr", "pane", "run")
        self.fake.ports_after_wait = {3000}
        results = self.start(kadha(self.path))
        self.assertEqual(results["herdr"].status, steps.STARTED)
        self.assertEqual(self.runner.detached[0], ("omarchy-launch-terminal", "herdr"))
        self.assertEqual(results["workspace"].status, steps.STARTED)

    def test_parallel_mode_opens_editor_before_waiting(self) -> None:
        self.runner.on_json("herdr", "workspace", "list", result={"workspaces": []})
        self.runner.on_json("herdr", "workspace", "create", result={"workspace": {"workspace_id": "w9"}})
        self.runner.on_json("herdr", "tab", "create", result={"tab": {"tab_id": "w9:t2"}, "root_pane": {"pane_id": "w9:p2"}})
        self.runner.on("herdr", "pane", "run")
        self.fake.ports_after_wait = {3000}
        config = kadha(self.path, mode="parallel")
        results = steps.start(config.projects[0], config, self.fake.build())
        order = [r.step for r in results]
        self.assertLess(order.index("editor"), order.index("wait"))

    def test_port_held_by_another_project_refuses(self) -> None:
        self.everything_running()
        other = Path(self.tmp.name) / "muhsi.in"
        other.mkdir()
        self.fake.listeners[3000] = ports.Listener(pid=77, name="node", cwd=other)
        self.fake.containers = []
        results = self.start(kadha(self.path))
        self.assertEqual(results["command:app"].status, steps.FAILED)
        self.assertIn("muhsi.in", results["command:app"].detail)
        self.assertEqual(results["wait"].status, steps.FAILED)
        self.assertEqual(results["browser"].status, steps.SKIPPED)
        self.assertIn("another project", results["browser"].detail)
        self.assertNotIn(("xdg-open", "http://localhost:3000"), self.runner.detached)
        self.assertFalse(steps.succeeded(list(results.values())))
        # The rest still happens: the terminal is focused and the editor opens.
        self.assertEqual(results["terminal"].status, steps.FOCUSED)
        self.assertEqual(results["editor"].status, steps.FOCUSED)

        # A dry run must not plan to open the browser on the other project either.
        plan = self.start(kadha(self.path), dry_run=True)
        self.assertEqual(plan["browser"].status, steps.SKIPPED)
        self.assertEqual(plan["wait"].status, steps.FAILED)

    def test_port_with_unknown_owner_is_left_alone(self) -> None:
        self.everything_running()
        self.fake.containers = None      # docker not installed
        results = self.start(kadha(self.path))
        self.assertEqual(results["command:app"].status, steps.SKIPPED)
        self.assertIn("not starting a second server", results["command:app"].detail)
        self.assertEqual(results["wait"].status, steps.SKIPPED)

    def test_webapp_mode_focuses_app_window(self) -> None:
        self.everything_running()
        self.fake.windows.append(window(address="0xf", cls="brave-localhost__-Default", title="Kadha", pid=9))
        results = self.start(kadha(self.path, browser="webapp"))
        self.assertEqual(results["browser"].status, steps.FOCUSED)
        self.assertIn(("hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:0xf" })'), self.runner.calls)


class StopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "kadha"
        self.path.mkdir()
        self.fake = FakeServices()
        self.runner = self.fake.runner

    def stop(self, config: cfg.Config, **kwargs: object) -> dict[str, steps.StepResult]:
        project = config.projects[0]
        return by_step(steps.stop(project, config, self.fake.build(), **kwargs))

    def running_project(self) -> None:
        self.runner.on_json("herdr", "workspace", "list", result=WORKSPACES)
        self.runner.on_json("herdr", "tab", "list", result=TABS)
        self.runner.on_json("herdr", "pane", "list", result=PANES)
        self.runner.on_json("herdr", "pane", "process-info", result=BUSY)
        self.runner.on("herdr", "tab", "close")
        self.runner.on("hyprctl", "dispatch", stdout="ok\n")
        self.fake.open_ports = {3000}
        self.fake.windows = [
            window(address="0xa", cls="dev.zed.Zed", title="kadha", pid=7),
            window(address="0xe", cls="org.omarchy.lazydocker", title="lazydocker", pid=8),
            window(address="0xc", cls="com.mitchellh.ghostty", title="nitrogen: kadha", pid=50),
        ]

        def interrupted(argv: tuple[str, ...]) -> CommandResult | None:
            if argv[:3] != ("herdr", "pane", "send-keys"):
                return None
            # Ctrl-C lands: the pane goes back to a prompt and the port closes.
            self.runner.on_json("herdr", "pane", "process-info", result=IDLE)
            self.fake.open_ports.discard(3000)
            return CommandResult(argv, 0, "", "")

        self.runner.respond_with(interrupted)

    def test_stop_tears_down_in_reverse_and_leaves_the_workspace(self) -> None:
        self.running_project()
        self.runner.tools.add("docker")
        self.runner.on("docker", "compose", "down")
        results = self.stop(kadha(self.path, stop_commands=["docker compose down"]))

        order = [r.step for r in results.values()]
        self.assertEqual(order, ["workspace", "command:app", "stop:1", "app:lazydocker", "editor", "browser"])
        self.assertEqual(results["stop:1"].status, steps.STOPPED)
        self.assertEqual(results["workspace"].status, steps.SKIPPED)
        self.assertIn("left in place", results["workspace"].detail)
        self.assertEqual(results["command:app"].status, steps.STOPPED)
        self.assertIn("interrupt docker", results["command:app"].detail)
        self.assertIn(("herdr", "pane", "send-keys", "w5:p9", "ctrl+c"), self.runner.calls)
        self.assertIn(("herdr", "tab", "close", "w5:t7"), self.runner.calls)
        self.assertEqual(results["editor"].status, steps.STOPPED)
        self.assertEqual(results["app:lazydocker"].status, steps.STOPPED)
        self.assertEqual(results["browser"].status, steps.SKIPPED)
        self.assertIn("tab left open", results["browser"].detail)
        closes = [c[2] for c in self.runner.calls if c[:2] == ("hyprctl", "dispatch")]
        self.assertEqual(closes, ['hl.dsp.close({ window = "address:0xe" })', 'hl.dsp.close({ window = "address:0xa" })'])
        # Never touched: the workspace and the terminal.
        self.assertNotIn(("herdr", "workspace", "close", "w5"), self.runner.calls)
        self.assertNotIn('hl.dsp.close({ window = "address:0xc" })', closes)

    def test_stop_command_runs_in_project_dir_without_a_shell(self) -> None:
        self.running_project()
        self.runner.on("docker", "compose", "down")
        self.runner.tools.add("docker")
        results = self.stop(kadha(self.path, stop_commands=["docker compose down", "echo 'all done'"]))
        self.assertEqual(results["stop:1"].status, steps.STOPPED)
        self.assertIn(("docker", "compose", "down"), self.runner.calls)
        # 'echo' is not a registered tool in the fake: reported as failed, run continues.
        self.assertEqual(results["stop:2"].status, steps.FAILED)
        self.assertEqual(results["editor"].status, steps.STOPPED)

    def test_stop_gives_up_when_the_command_will_not_die(self) -> None:
        self.running_project()
        self.runner.on("herdr", "pane", "send-keys")          # Ctrl-C has no effect this time
        self.runner._responders = [r for r in self.runner._responders if r.__name__ != "interrupted"]
        results = self.stop(kadha(self.path))
        self.assertEqual(results["command:app"].status, steps.FAILED)
        self.assertIn("still running after 90s", results["command:app"].detail)
        self.assertNotIn(("herdr", "tab", "close", "w5:t7"), self.runner.calls)
        self.assertGreaterEqual(self.fake.now, 90.0)

    def test_stop_when_nothing_is_running(self) -> None:
        self.runner.on_json("herdr", "workspace", "list", result={"workspaces": []})
        results = self.stop(kadha(self.path))
        self.assertEqual({r.status for r in results.values()}, {steps.SKIPPED})
        self.assertEqual(self.runner.mutating_calls(), [])

    def test_stop_dry_run_touches_nothing(self) -> None:
        self.running_project()
        results = self.stop(kadha(self.path, stop_commands=["docker compose down"]), dry_run=True)
        planned = {k for k, r in results.items() if r.status == steps.PLANNED}
        self.assertEqual(planned, {"command:app", "stop:1", "editor", "app:lazydocker"})
        self.assertEqual(self.runner.mutating_calls(), [])
        self.assertEqual(self.fake.open_ports, {3000})

    def test_stop_idle_tab_is_just_closed(self) -> None:
        self.running_project()
        self.runner.on_json("herdr", "pane", "process-info", result=IDLE)
        results = self.stop(kadha(self.path))
        self.assertEqual(results["command:app"].status, steps.STOPPED)
        self.assertIn("close idle tab", results["command:app"].detail)
        self.assertNotIn(("herdr", "pane", "send-keys", "w5:p9", "ctrl+c"), self.runner.calls)

    def test_stop_webapp_window_is_closed(self) -> None:
        self.running_project()
        self.fake.windows.append(window(address="0xf", cls="brave-localhost__-Default", title="Kadha", pid=9))
        results = self.stop(kadha(self.path, browser="webapp"))
        self.assertEqual(results["browser"].status, steps.STOPPED)

    def test_stop_tmux_project(self) -> None:
        self.runner.on("tmux", "has-session")
        self.runner.on("tmux", "list-windows", stdout="site:0\tbash\nsite:1\tomadev-web\n")
        self.runner.on("tmux", "display-message", stdout="node\n")
        self.runner.on("tmux", "kill-window")
        self.fake.open_ports = {8080}

        def interrupted(argv: tuple[str, ...]) -> CommandResult | None:
            if argv[:2] != ("tmux", "send-keys"):
                return None
            self.runner.on("tmux", "display-message", stdout="bash\n")
            self.fake.open_ports.discard(8080)
            return CommandResult(argv, 0, "", "")

        self.runner.respond_with(interrupted)
        config = cfg.parse({"version": 1, "projects": [{
            "name": "site", "path": str(self.path), "multiplexer": "tmux",
            "commands": [{"name": "web", "run": "npm run dev", "port": 8080}],
        }]}, check_paths=False)
        results = by_step(steps.stop(config.projects[0], config, self.fake.build()))
        self.assertEqual(results["command:web"].status, steps.STOPPED)
        self.assertIn(("tmux", "send-keys", "-t", "site:1", "C-c"), self.runner.calls)
        self.assertIn(("tmux", "kill-window", "-t", "site:1"), self.runner.calls)
        self.assertEqual(results["editor"].status, steps.SKIPPED)
        self.assertNotIn(("tmux", "kill-session", "-t", "=site"), self.runner.calls)


class TmuxStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.fake = FakeServices()
        self.runner = self.fake.runner

    def test_fresh_tmux_session(self) -> None:
        self.runner.on("tmux", "has-session", returncode=1)
        self.runner.on("tmux", "new-session")
        self.runner.on("tmux", "new-window", stdout="my_site:1\n")
        self.runner.on("tmux", "send-keys")
        self.runner.on("tmux", "list-clients", returncode=1)
        self.fake.ports_after_wait = {8080}
        config = cfg.parse({"version": 1, "projects": [{
            "name": "my.site", "path": str(self.path), "multiplexer": "tmux",
            "commands": [{"name": "web", "run": "npm run dev", "port": 8080, "cwd": "client"}],
            "url": "http://localhost:8080",
        }]}, check_paths=False)
        results = by_step(steps.start(config.projects[0], config, self.fake.build()))
        self.assertEqual(results["workspace"].status, steps.STARTED)
        self.assertIn(("tmux", "new-session", "-d", "-s", "my_site", "-c", str(self.path)), self.runner.calls)
        new_window = next(c for c in self.runner.calls if c[:2] == ("tmux", "new-window"))
        self.assertIn(str(self.path / "client"), new_window)
        self.assertIn(("tmux", "send-keys", "-t", "my_site:1", "-l", "npm run dev"), self.runner.calls)
        self.assertEqual(results["terminal"].status, steps.STARTED)
        self.assertEqual(self.runner.detached[0], ("omarchy-launch-terminal", "tmux", "new-session", "-A", "-s", "my_site"))
        self.assertEqual(results["editor"].status, steps.STARTED)
        self.assertEqual(self.runner.detached[-1], ("omarchy-launch-editor", str(self.path)))


class StatusTests(unittest.TestCase):
    def test_status_snapshot(self) -> None:
        fake = FakeServices()
        fake.runner.on_json("herdr", "workspace", "list", result=WORKSPACES)
        fake.open_ports = {3000}
        fake.windows = [window(cls="dev.zed.Zed", title="kadha — x")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kadha"
            fake.listeners[3000] = ports.Listener(pid=4, name="node", cwd=path / "client")
            config = kadha(path)
            snapshot = steps.status(config.projects[0], config, fake.build())
        self.assertEqual(snapshot["workspace"], {"present": True, "id": "w5"})
        command = snapshot["commands"][0]
        self.assertEqual((command["name"], command["port"], command["listening"], command["owner"]), ("app", 3000, True, "project"))
        self.assertTrue(snapshot["url"]["reachable"])
        self.assertEqual(snapshot["url"]["owner"], "project")
        self.assertTrue(snapshot["editor_open"])
        self.assertEqual(snapshot["apps"], [{"name": "lazydocker", "open": False}])
        self.assertEqual(fake.runner.mutating_calls(), [])


if __name__ == "__main__":
    unittest.main()

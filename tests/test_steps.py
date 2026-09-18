"""End-to-end tests of the Start flow against faked herdr, tmux and Hyprland."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omadev_cli import config as cfg
from omadev_cli import steps
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
        self.runner.on("hyprctl", "dispatch")
        self.fake.open_ports = {3000}
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
        self.assertIn("3000", results["command:app"].detail)
        self.assertEqual(results["terminal"].status, steps.FOCUSED)
        self.assertEqual(results["terminal:focus"].status, steps.FOCUSED)
        self.assertEqual(results["wait"].status, steps.SKIPPED)
        self.assertEqual(results["browser"].status, steps.SKIPPED)
        self.assertEqual(results["editor"].status, steps.FOCUSED)
        self.assertEqual(results["app:lazydocker"].status, steps.FOCUSED)
        self.assertTrue(steps.succeeded(list(results.values())))

        self.assertEqual(self.runner.detached, [])
        focus_calls = [c for c in self.runner.calls if c[:3] == ("hyprctl", "dispatch", "focuswindow")]
        self.assertEqual([c[3] for c in focus_calls], ["address:0xc", "address:0xa", "address:0xe"])
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
        self.assertEqual(self.fake.waited, [("localhost", 3000, 90.0)])

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

    def test_webapp_mode_focuses_app_window(self) -> None:
        self.everything_running()
        self.fake.windows.append(window(address="0xf", cls="brave-localhost__-Default", title="Kadha", pid=9))
        results = self.start(kadha(self.path, browser="webapp"))
        self.assertEqual(results["browser"].status, steps.FOCUSED)
        self.assertIn(("hyprctl", "dispatch", "focuswindow", "address:0xf"), self.runner.calls)


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
            config = kadha(Path(tmp) / "kadha")
            snapshot = steps.status(config.projects[0], config, fake.build())
        self.assertEqual(snapshot["workspace"], {"present": True, "id": "w5"})
        self.assertEqual(snapshot["commands"], [{"name": "app", "port": 3000, "listening": True}])
        self.assertTrue(snapshot["url"]["reachable"])
        self.assertTrue(snapshot["editor_open"])
        self.assertEqual(snapshot["apps"], [{"name": "lazydocker", "open": False}])
        self.assertEqual(fake.runner.mutating_calls(), [])


if __name__ == "__main__":
    unittest.main()

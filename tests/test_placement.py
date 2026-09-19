"""Tests for workspace placement, setup commands, "none" mode and env entries."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omadev_cli import config as cfg
from omadev_cli import hypr, ports, steps
from omadev_cli.system import CommandResult
from tests.fakes import FakeServices, window
from tests.test_herdr import BUSY, IDLE, PANES, TABS, WORKSPACES


def by_step(results: list[steps.StepResult]) -> dict[str, steps.StepResult]:
    return {r.step: r for r in results}


def project(path: Path, **fields: object) -> cfg.Config:
    data = {"name": "kadha", "path": str(path), "multiplexer": "herdr"}
    data.update(fields)
    return cfg.parse({"version": 1, "projects": [data]}, check_paths=False)


class ConfigFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_new_fields_round_trip(self) -> None:
        config = project(
            Path(self.dir),
            setup=[{"name": "install", "run": "npm install", "cwd": "client", "unless_exists": "client/node_modules", "timeout": 120}],
            commands=[{"name": "web", "run": "npm run dev", "port": 5173, "env": ["PORT=5173", "DEBUG=app:*"]}],
            apps=[{"name": "ld", "launch": ["lazydocker"], "match": "lazydocker", "workspace": 4}],
            workspaces={"terminal": 2, "browser": 1, "editor": 3},
        )
        p = config.projects[0]
        self.assertEqual(p.setup[0].unless_exists, "client/node_modules")
        self.assertEqual(p.setup[0].timeout, 120)
        self.assertEqual(p.commands[0].env, ("PORT=5173", "DEBUG=app:*"))
        self.assertEqual(p.apps[0].workspace, 4)
        self.assertEqual((p.workspaces.terminal, p.workspaces.browser, p.workspaces.editor), (2, 1, 3))
        self.assertEqual(cfg.parse(cfg.to_dict(config), check_paths=False), config)
        self.assertEqual(cfg.project_to_dict(p)["workspaces"], {"terminal": 2, "browser": 1, "editor": 3})

    def test_validation_of_new_fields(self) -> None:
        for fields, where in (
            ({"commands": [{"name": "a", "run": "x", "env": ["NOEQUALS"]}]}, "project 'kadha'.commands[0].env[0]"),
            ({"commands": [{"name": "a", "run": "x", "env": ["1BAD=x"]}]}, "project 'kadha'.commands[0].env[0]"),
            ({"workspaces": {"browser": 0}}, "project 'kadha'.workspaces.browser"),
            ({"workspaces": {"browser": 100}}, "project 'kadha'.workspaces.browser"),
            ({"apps": [{"name": "a", "launch": ["x"], "match": "x", "workspace": "3"}]}, "project 'kadha'.apps[0].workspace"),
            ({"setup": [{"name": "a", "run": "x", "timeout": 0}]}, "project 'kadha'.setup[0].timeout"),
            ({"setup": [{"name": "a", "run": "x", "unless_exists": "/etc"}]}, "project 'kadha'.setup[0].unless_exists"),
            ({"multiplexer": "zellij"}, "project 'kadha'.multiplexer"),
        ):
            with self.subTest(fields=fields), self.assertRaises(cfg.ConfigError) as ctx:
                project(Path(self.dir), **fields)
            self.assertEqual(ctx.exception.where, where)

    def test_none_multiplexer_is_valid(self) -> None:
        self.assertEqual(project(Path(self.dir), multiplexer="none").projects[0].multiplexer, "none")


class PlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "kadha"
        self.path.mkdir()
        self.fake = FakeServices()
        self.runner = self.fake.runner
        self.runner.on_json("herdr", "workspace", "list", result=WORKSPACES)
        self.runner.on_json("herdr", "tab", "list", result=TABS)
        self.runner.on_json("herdr", "pane", "list", result=PANES)
        self.runner.on_json("herdr", "pane", "process-info", result=BUSY)
        self.runner.on("herdr", "workspace", "focus")
        self.runner.on("hyprctl", "dispatch", stdout="ok\n")
        self.fake.open_ports = {3000}
        self.fake.listeners[3000] = ports.Listener(None, "", None)
        self.fake.containers = [ports.Container("kadha-client-1", frozenset({3000}), self.path)]

    def appear_on_launch(self, argv_head: str, new_window: hypr.Window) -> None:
        def hook(argv: tuple[str, ...]) -> None:
            if argv_head in argv:
                self.fake.windows.append(new_window)

        self.runner.detach_hooks.append(hook)

    def moves(self) -> list[str]:
        return [c[2] for c in self.runner.calls if c[:2] == ("hyprctl", "dispatch") and "move" in c[2]]

    def start(self, config: cfg.Config, **kwargs: object) -> dict[str, steps.StepResult]:
        return by_step(steps.start(config.projects[0], config, self.fake.build(), **kwargs))

    def test_new_editor_window_is_moved_existing_one_is_not(self) -> None:
        editor = {"launch": ["zed", "{path}"], "match": r"^dev\.zed\.Zed$"}
        config = project(self.path, editor=editor, workspaces={"editor": 3}, url="http://localhost:3000",
                         commands=[{"name": "app", "run": "docker compose up", "port": 3000}])

        # Nothing open: the editor is launched, its window appears, it is moved.
        self.appear_on_launch("zed", window(address="0xe1", cls="dev.zed.Zed", title="kadha — x"))
        results = self.start(config)
        self.assertEqual(results["editor"].status, steps.STARTED)
        self.assertIn("moved to workspace 3", results["editor"].detail)
        self.assertEqual(self.moves(), ['hl.dsp.window.move({ window = "address:0xe1", workspace = 3, silent = true })'])

        # Already open on another workspace: focused there, never moved.
        self.runner.calls.clear()
        self.runner.detached.clear()
        results = self.start(config)
        self.assertEqual(results["editor"].status, steps.FOCUSED)
        self.assertEqual(self.moves(), [])
        self.assertEqual([d for d in self.runner.detached if "zed" in d], [])

    def test_window_that_never_appears_is_reported_not_failed(self) -> None:
        editor = {"launch": ["zed", "{path}"], "match": r"^dev\.zed\.Zed$"}
        config = project(self.path, editor=editor, workspaces={"editor": 3})
        results = self.start(config)
        self.assertEqual(results["editor"].status, steps.STARTED)
        self.assertIn("did not appear", results["editor"].detail)
        self.assertEqual(self.moves(), [])
        self.assertGreaterEqual(self.fake.now, steps.PLACE_TIMEOUT)

    def test_dry_run_mentions_the_workspace_and_moves_nothing(self) -> None:
        editor = {"launch": ["zed", "{path}"], "match": r"^dev\.zed\.Zed$"}
        config = project(self.path, editor=editor, workspaces={"editor": 3})
        results = self.start(config, dry_run=True)
        self.assertEqual(results["editor"].status, steps.PLANNED)
        self.assertIn("on workspace 3", results["editor"].detail)
        self.assertEqual(self.runner.mutating_calls(), [])

    def test_webapp_window_and_app_window_are_placed(self) -> None:
        config = project(self.path, url="http://localhost:3000", browser="webapp",
                         workspaces={"browser": 1},
                         apps=[{"name": "ld", "launch": ["omarchy-launch-tui", "lazydocker"], "match": "lazydocker", "workspace": 4}])
        self.appear_on_launch("omarchy-launch-webapp", window(address="0xe3", cls="brave-localhost__-Default", title="Kadha"))
        self.appear_on_launch("lazydocker", window(address="0xe4", cls="org.omarchy.lazydocker", title="lazydocker"))
        results = self.start(config)
        self.assertIn("moved to workspace 1", results["browser"].detail)
        self.assertIn("moved to workspace 4", results["app:ld"].detail)
        self.assertEqual(len(self.moves()), 2)

    def test_browser_tab_in_existing_window_is_not_moved(self) -> None:
        # A browser window exists; xdg-open adds a tab to it; no new window appears.
        self.fake.windows.append(window(address="0xb", cls="brave-browser", title="Something - Brave"))
        self.fake.open_ports = set()
        self.fake.ports_after_wait = {3000}
        self.runner.on_json("herdr", "tab", "list", result={"tabs": []})
        self.runner.on_json("herdr", "tab", "create", result={"tab": {"tab_id": "w5:t9"}, "root_pane": {"pane_id": "w5:p9"}})
        self.runner.on("herdr", "pane", "run")
        config = project(self.path, url="http://localhost:3000", browser="browser", workspaces={"browser": 1},
                         commands=[{"name": "app", "run": "docker compose up", "port": 3000}])
        results = self.start(config)
        self.assertEqual(results["browser"].status, steps.STARTED)
        self.assertIn("did not appear", results["browser"].detail)
        self.assertEqual(self.moves(), [])

    def test_new_terminal_is_found_through_its_client_and_moved(self) -> None:
        proc = Path(self.tmp.name) / "proc"
        for pid, ppid in ((50, 1), (60, 50), (100, 60)):
            (proc / str(pid)).mkdir(parents=True)
            (proc / str(pid) / "status").write_text(f"PPid:\t{ppid}\n")
        self.fake.proc_root = proc

        def hook(argv: tuple[str, ...]) -> None:
            if argv[:2] == ("omarchy-launch-terminal", "herdr"):
                self.fake.windows.append(window(address="0xe5", cls="com.mitchellh.ghostty", pid=50))
                self.fake.processes.append((100, ["herdr"]))

        self.runner.detach_hooks.append(hook)
        config = project(self.path, workspaces={"terminal": 2})
        results = self.start(config)
        self.assertEqual(results["terminal"].status, steps.STARTED)
        self.assertIn("moved to workspace 2", results["terminal"].detail)
        self.assertEqual(self.moves(), ['hl.dsp.window.move({ window = "address:0xe5", workspace = 2, silent = true })'])


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "kadha"
        self.path.mkdir()
        self.fake = FakeServices()
        self.runner = self.fake.runner
        self.runner.on_json("herdr", "workspace", "list", result=WORKSPACES)
        self.runner.on_json("herdr", "tab", "list", result={"tabs": []})
        self.runner.on_json("herdr", "pane", "list", result={"panes": []})
        self.runner.on("herdr", "workspace", "focus")
        self.fake.processes = []

        created = {"n": 0}

        def tab_create(argv: tuple[str, ...]) -> CommandResult | None:
            if argv[:3] != ("herdr", "tab", "create"):
                return None
            created["n"] += 1
            return CommandResult(argv, 0, '{"result": {"tab": {"tab_id": "w5:t%d"}, "root_pane": {"pane_id": "w5:p%d"}}}' % (created["n"], created["n"]), "")

        self.runner.respond_with(tab_create)
        self.runner.on_json("herdr", "pane", "process-info", result=IDLE)

        def pane_run(argv: tuple[str, ...]) -> CommandResult | None:
            if argv[:3] != ("herdr", "pane", "run"):
                return None
            # The command starts: busy now, idle again after a couple of polls.
            self.runner.on_json("herdr", "pane", "process-info", result=BUSY)
            polls = {"n": 0}

            def info(inner: tuple[str, ...]) -> CommandResult | None:
                if inner[:3] != ("herdr", "pane", "process-info"):
                    return None
                polls["n"] += 1
                import json
                return CommandResult(inner, 0, json.dumps({"result": IDLE if polls["n"] > 2 else BUSY}), "")

            self.runner.respond_with(info)
            return CommandResult(argv, 0, "", "")

        self.runner.respond_with(pane_run)

    def start(self, config: cfg.Config, **kwargs: object) -> dict[str, steps.StepResult]:
        return by_step(steps.start(config.projects[0], config, self.fake.build(), **kwargs))

    def test_setup_runs_to_completion_before_commands(self) -> None:
        config = project(self.path, setup=[{"name": "install", "run": "npm install", "cwd": "client"}],
                         commands=[{"name": "web", "run": "npm run dev", "port": 5173}])
        results = self.start(config)
        self.assertEqual(results["setup:install"].status, steps.STARTED)
        self.assertIn("ran 'npm install'", results["setup:install"].detail)
        order = [r.step for r in results.values()]
        self.assertLess(order.index("setup:install"), order.index("command:web"))
        runs = [c for c in self.runner.calls if c[:3] == ("herdr", "pane", "run")]
        self.assertTrue(runs[0][4].startswith("cd '") and runs[0][4].endswith("client' && npm install"), runs[0])
        self.assertIn(("herdr", "tab", "create", "--workspace", "w5", "--cwd", str(self.path), "--label", "omadev-setup", "--no-focus"), self.runner.calls)
        self.assertEqual(results["command:web"].status, steps.STARTED)

    def test_setup_is_skipped_when_its_marker_exists(self) -> None:
        (self.path / "node_modules").mkdir()
        config = project(self.path, setup=[{"name": "install", "run": "npm install", "unless_exists": "node_modules"}])
        results = self.start(config)
        self.assertEqual(results["setup:install"].status, steps.SKIPPED)
        self.assertIn("node_modules exists", results["setup:install"].detail)
        self.assertNotIn(("herdr", "tab", "create", "--workspace", "w5", "--cwd", str(self.path), "--label", "omadev-setup", "--no-focus"), self.runner.calls)

    def test_setup_timeout_fails_and_holds_the_processes(self) -> None:
        self.runner._responders = [r for r in self.runner._responders if r.__name__ != "pane_run"]
        self.runner.on("herdr", "pane", "run")
        self.runner.on_json("herdr", "pane", "process-info", result=IDLE)

        def stays_busy(argv: tuple[str, ...]) -> CommandResult | None:
            if argv[:3] != ("herdr", "pane", "run"):
                return None
            self.runner.on_json("herdr", "pane", "process-info", result=BUSY)
            return CommandResult(argv, 0, "", "")

        self.runner.respond_with(stays_busy)
        config = project(self.path, setup=[{"name": "deps", "run": "make deps", "timeout": 30}, {"name": "later", "run": "true"}],
                         commands=[{"name": "web", "run": "npm run dev"}], url="http://localhost:3000")
        results = self.start(config)
        self.assertEqual(results["setup:deps"].status, steps.FAILED)
        self.assertIn("still running after 30s", results["setup:deps"].detail)
        self.assertEqual(results["setup:later"].status, steps.SKIPPED)
        self.assertEqual(results["command:web"].status, steps.SKIPPED)
        self.assertEqual(results["wait"].status, steps.SKIPPED)
        self.assertEqual(results["browser"].status, steps.SKIPPED)

    def test_env_is_passed_when_the_tab_is_created(self) -> None:
        config = project(self.path, commands=[{"name": "web", "run": "npm run dev", "env": ["PORT=5173", "A=b c"]}])
        self.start(config)
        create = next(c for c in self.runner.calls if c[:3] == ("herdr", "tab", "create"))
        self.assertEqual(create[-4:], ("--env", "PORT=5173", "--env", "A=b c"))

    def test_stop_closes_an_idle_setup_tab(self) -> None:
        self.runner.on_json("herdr", "tab", "list", result={"tabs": [{"tab_id": "w5:t7", "label": "omadev-setup"}]})
        self.runner.on_json("herdr", "pane", "list", result={"panes": [{"pane_id": "w5:p7", "tab_id": "w5:t7"}]})
        self.runner.on_json("herdr", "pane", "process-info", result=IDLE)
        self.runner.on("herdr", "tab", "close")
        config = project(self.path, setup=[{"name": "install", "run": "npm install"}])
        results = by_step(steps.stop(config.projects[0], config, self.fake.build()))
        self.assertEqual(results["setup"].status, steps.STOPPED)
        self.assertIn(("herdr", "tab", "close", "w5:t7"), self.runner.calls)


class NoneModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "my proj"
        self.path.mkdir()
        self.fake = FakeServices()
        self.runner = self.fake.runner
        self.runner.on("hyprctl", "dispatch", stdout="ok\n")
        self.config = project(self.path, multiplexer="none", workspaces={"terminal": 2},
                              commands=[{"name": "web", "run": "npm run dev", "port": 5173, "env": ["PORT=5173"]}])
        self.config = cfg.Config(projects=(cfg.parse_project({**cfg.project_to_dict(self.config.projects[0]), "name": "My Proj"}, check_paths=False)[0],))

    def test_process_gets_its_own_terminal_window(self) -> None:
        cls = hypr.app_id("My Proj", "web")
        self.assertEqual(cls, "omadev.my-proj.web")
        self.runner.detach_hooks.append(lambda argv: self.fake.windows.append(window(address="0xe2", cls=cls, title="npm")) if "omarchy-launch-tui" in argv else None)
        results = by_step(steps.start(self.config.projects[0], self.config, self.fake.build()))
        self.assertEqual(results["workspace"].status, steps.SKIPPED)
        self.assertEqual(results["terminal"].status, steps.SKIPPED)
        self.assertEqual(results["command:web"].status, steps.STARTED)
        launch = self.runner.detached[0]
        self.assertEqual(launch[:2], ("omarchy-launch-tui", f"--app-id={cls}"))
        self.assertEqual(launch[2:5], ("env", "PORT=5173", "bash"))
        self.assertTrue(launch[-1].endswith("&& npm run dev"))
        self.assertIn("moved to workspace 2", results["command:web"].detail)

        # Second start: the window is there, no second terminal is launched.
        self.runner.detached.clear()
        results = by_step(steps.start(self.config.projects[0], self.config, self.fake.build()))
        self.assertEqual(results["command:web"].status, steps.SKIPPED)
        self.assertEqual([d for d in self.runner.detached if d[0] == "omarchy-launch-tui"], [])

    def test_stop_closes_the_terminal_window_and_waits_for_the_port(self) -> None:
        cls = hypr.app_id("My Proj", "web")
        self.fake.windows.append(window(address="0xe2", cls=cls, title="npm"))
        self.fake.open_ports = {5173}

        def closed(argv: tuple[str, ...]) -> CommandResult | None:
            if argv[:2] == ("hyprctl", "dispatch") and "close" in argv[2]:
                self.fake.open_ports.discard(5173)
                return CommandResult(argv, 0, "ok\n", "")
            return None

        self.runner.respond_with(closed)
        results = by_step(steps.stop(self.config.projects[0], self.config, self.fake.build()))
        self.assertEqual(results["command:web"].status, steps.STOPPED)
        self.assertIn('hl.dsp.window.close({ window = "address:0xe2" })', [c[2] for c in self.runner.calls if c[:2] == ("hyprctl", "dispatch")])

    def test_status_reports_the_window_as_listening(self) -> None:
        cls = hypr.app_id("My Proj", "web")
        self.fake.windows.append(window(address="0xe2", cls=cls, title="npm"))
        self.fake.open_ports = {5173}
        self.fake.listeners[5173] = ports.Listener(9, "node", self.path)
        snapshot = steps.status(self.config.projects[0], self.config, self.fake.build())
        self.assertEqual(snapshot["workspace"], {"present": None, "id": None})
        self.assertTrue(snapshot["commands"][0]["listening"])


if __name__ == "__main__":
    unittest.main()

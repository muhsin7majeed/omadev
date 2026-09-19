"""Tests for the herdr wrapper, against recorded response shapes."""

from __future__ import annotations

import unittest
from pathlib import Path

from omadev_cli import herdr
from tests.fakes import FakeRunner

WORKSPACES = {"type": "workspace_list", "workspaces": [
    {"workspace_id": "w3", "label": "realms", "focused": False},
    {"workspace_id": "w5", "label": "kadha", "focused": False},
]}
TABS = {"type": "tab_list", "tabs": [
    {"tab_id": "w5:t1", "label": "1", "workspace_id": "w5"},
    {"tab_id": "w5:t7", "label": "omadev-app", "workspace_id": "w5"},
]}
PANES = {"type": "pane_list", "panes": [
    {"pane_id": "w5:p1", "tab_id": "w5:t1", "workspace_id": "w5", "cwd": "/x"},
    {"pane_id": "w5:p9", "tab_id": "w5:t7", "workspace_id": "w5", "cwd": "/x"},
]}
IDLE = {"type": "pane_process_info", "process_info": {"shell_pid": 838037, "foreground_process_group_id": 838037,
        "foreground_processes": [{"argv": ["/usr/bin/bash"], "name": "bash", "pid": 838037}]}}
BUSY = {"type": "pane_process_info", "process_info": {"shell_pid": 838037, "foreground_process_group_id": 900,
        "foreground_processes": [{"argv": ["docker", "compose", "up"], "name": "docker", "pid": 900}]}}


class HerdrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = FakeRunner()
        self.h = herdr.Herdr(self.runner)

    def test_find_workspace_by_label(self) -> None:
        self.runner.on_json("herdr", "workspace", "list", result=WORKSPACES)
        found = self.h.find_workspace("kadha")
        self.assertEqual(found, herdr.Workspace("w5", "kadha"))
        self.assertEqual(self.h.find_workspace("Kadha"), herdr.Workspace("w5", "kadha"))
        self.assertIsNone(self.h.find_workspace("nope"))
        self.assertTrue(self.h.server_running())

    def test_server_down_is_not_running(self) -> None:
        self.runner.on("herdr", "workspace", "list", returncode=1, stderr="connection refused")
        self.assertFalse(self.h.server_running())
        with self.assertRaises(herdr.HerdrError):
            self.h.workspaces()

    def test_create_workspace_returns_id(self) -> None:
        self.runner.on_json("herdr", "workspace", "create", result={"workspace": {"workspace_id": "w9"}, "tab": {"tab_id": "w9:t1"}, "root_pane": {"pane_id": "w9:p1"}})
        created = self.h.create_workspace(Path("/home/x/proj"), "proj")
        self.assertEqual(created.id, "w9")
        self.assertEqual(self.runner.calls[-1], ("herdr", "workspace", "create", "--cwd", "/home/x/proj", "--label", "proj", "--no-focus"))

    def test_tabs_panes_and_foreground(self) -> None:
        self.runner.on_json("herdr", "tab", "list", result=TABS)
        self.runner.on_json("herdr", "pane", "list", result=PANES)
        self.runner.on_json("herdr", "pane", "process-info", result=BUSY)
        tab = self.h.find_tab("w5", herdr.tab_label("app"))
        self.assertEqual(tab.id, "w5:t7")
        pane = self.h.first_pane("w5", tab.id)
        self.assertEqual(pane.id, "w5:p9")
        foreground = self.h.foreground(pane.id)
        self.assertFalse(foreground.idle)
        self.assertEqual(foreground.summary, "docker")

    def test_idle_shell(self) -> None:
        self.runner.on_json("herdr", "pane", "process-info", result=IDLE)
        self.assertTrue(self.h.foreground("w5:p3").idle)

    def test_create_tab_and_run(self) -> None:
        self.runner.on_json("herdr", "tab", "create", result={"tab": {"tab_id": "w5:t8"}, "root_pane": {"pane_id": "w5:p10"}})
        self.runner.on("herdr", "pane", "run")
        tab, pane_id = self.h.create_tab("w5", Path("/p"), "omadev-app")
        self.assertEqual((tab.id, pane_id), ("w5:t8", "w5:p10"))
        self.h.run(pane_id, "docker compose up")
        self.assertEqual(self.runner.calls[-1], ("herdr", "pane", "run", "w5:p10", "docker compose up"))

    def test_bad_shapes_raise(self) -> None:
        self.runner.on("herdr", "tab", "create", stdout="{}")
        with self.assertRaises(herdr.HerdrError):
            self.h.create_tab("w5", Path("/p"), "x")
        self.runner.on("herdr", "workspace", "list", stdout="garbage")
        with self.assertRaises(herdr.HerdrError):
            self.h.workspaces()


class ClientPidTests(unittest.TestCase):
    def test_excludes_server_and_other_programs(self) -> None:
        processes = [(1, ["/usr/bin/herdr", "server"]), (2, ["herdr"]), (3, ["/usr/bin/herdr", "--session", "x"]), (4, ["herdr-not"]), (5, ["bash"])]
        self.assertEqual(herdr.client_pids(processes), [2, 3])


if __name__ == "__main__":
    unittest.main()

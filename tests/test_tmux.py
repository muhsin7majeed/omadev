"""Tests for the tmux wrapper."""

from __future__ import annotations

import unittest
from pathlib import Path

from omadev_cli import tmux
from tests.fakes import FakeRunner


class TmuxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = FakeRunner()
        self.t = tmux.Tmux(self.runner)

    def test_session_name_is_sanitised(self) -> None:
        self.assertEqual(tmux.session_name("muhsi.in"), "muhsi_in")
        self.assertEqual(tmux.session_name("a:b"), "a_b")
        self.assertEqual(tmux.window_name("app"), "omadev-app")

    def test_has_session_uses_exact_match(self) -> None:
        self.runner.on("tmux", "has-session", returncode=1)
        self.assertFalse(self.t.has_session("kadha"))
        self.assertEqual(self.runner.calls[-1], ("tmux", "has-session", "-t", "=kadha"))

    def test_find_session_ignores_case_after_exact_match(self) -> None:
        self.runner.on("tmux", "has-session", returncode=1)
        self.runner.on("tmux", "list-sessions", stdout="0\nkadha\nother\n")
        self.assertEqual(self.t.find_session("Kadha"), "kadha")
        self.assertIsNone(self.t.find_session("nope"))
        self.runner.on("tmux", "has-session", returncode=0)
        self.assertEqual(self.t.find_session("Kadha"), "Kadha")

    def test_windows_and_find(self) -> None:
        self.runner.on("tmux", "list-windows", stdout="kadha:0\tbash\nkadha:1\tomadev-app\n")
        self.assertEqual(self.t.windows("kadha"), [("kadha:0", "bash"), ("kadha:1", "omadev-app")])
        self.assertEqual(self.t.find_window("kadha", "omadev-app"), "kadha:1")
        self.assertIsNone(self.t.find_window("kadha", "omadev-db"))

    def test_new_window_reports_target(self) -> None:
        self.runner.on("tmux", "new-window", stdout="kadha:2\n")
        self.assertEqual(self.t.new_window("kadha", "omadev-app", Path("/p")), "kadha:2")
        call = self.runner.calls[-1]
        self.assertIn("-c", call)
        self.assertIn("/p", call)

    def test_run_sends_literal_then_enter(self) -> None:
        self.runner.on("tmux", "send-keys")
        self.t.run("kadha:2", "npm run dev; echo done")
        self.assertEqual(self.runner.calls[-2], ("tmux", "send-keys", "-t", "kadha:2", "-l", "npm run dev; echo done"))
        self.assertEqual(self.runner.calls[-1], ("tmux", "send-keys", "-t", "kadha:2", "Enter"))

    def test_idle_detection(self) -> None:
        self.runner.on("tmux", "display-message", stdout="zsh\n")
        self.assertTrue(self.t.idle("kadha:2"))
        self.runner.on("tmux", "display-message", stdout="node\n")
        self.assertFalse(self.t.idle("kadha:2"))

    def test_client_pids(self) -> None:
        self.runner.on("tmux", "list-clients", stdout="4242\n4300\n")
        self.assertEqual(self.t.client_pids("kadha"), [4242, 4300])
        self.runner.on("tmux", "list-clients", returncode=1)
        self.assertEqual(self.t.client_pids("kadha"), [])

    def test_failures_raise(self) -> None:
        self.runner.on("tmux", "new-session", returncode=1, stderr="duplicate session")
        with self.assertRaises(tmux.TmuxError):
            self.t.new_session("kadha", Path("/p"))


if __name__ == "__main__":
    unittest.main()

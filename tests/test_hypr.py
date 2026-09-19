"""Tests for Hyprland window lookup."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omadev_cli import hypr
from tests.fakes import FakeRunner, window

SAMPLE = [
    {"address": "0xa", "class": "dev.zed.Zed", "initialClass": "dev.zed.Zed", "title": "kadha — App.tsx", "initialTitle": "kadha", "pid": 274658, "workspace": {"id": 3, "name": "3"}},
    {"address": "0xb", "class": "brave-browser", "initialClass": "brave-browser", "title": "Kadha - Your private movie history - Brave", "initialTitle": "New Tab - Brave", "pid": 31511, "workspace": {"id": 1, "name": "1"}},
    {"address": "0xc", "class": "com.mitchellh.ghostty", "initialClass": "com.mitchellh.ghostty", "title": "nitrogen: kadha", "initialTitle": "Ghostty", "pid": 664733, "workspace": {"id": 2, "name": "2"}},
]


class ClientsTests(unittest.TestCase):
    def test_parses_hyprctl_output(self) -> None:
        runner = FakeRunner().on("hyprctl", "clients", "-j", stdout=json.dumps(SAMPLE))
        windows = hypr.clients(runner)
        self.assertEqual([w.cls for w in windows], ["dev.zed.Zed", "brave-browser", "com.mitchellh.ghostty"])
        self.assertEqual(windows[0].pid, 274658)
        self.assertEqual(windows[2].workspace, "2")

    def test_failures_become_hypr_error(self) -> None:
        runner = FakeRunner().on("hyprctl", "clients", "-j", stdout="not json")
        with self.assertRaises(hypr.HyprError):
            hypr.clients(runner)
        runner = FakeRunner().on("hyprctl", "clients", "-j", returncode=1, stderr="no socket")
        with self.assertRaises(hypr.HyprError):
            hypr.clients(runner)

    def test_focus_uses_lua_dispatch_first(self) -> None:
        runner = FakeRunner().on("hyprctl", "dispatch", stdout="ok\n")
        hypr.focus(runner, window(address="0xa"))
        self.assertEqual(runner.calls, [("hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:0xa" })')])

    def test_focus_falls_back_to_classic_dispatch(self) -> None:
        runner = FakeRunner()
        runner.on("hyprctl", "dispatch", "focuswindow", stdout="ok\n")
        runner.on("hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:0xa" })', returncode=7, stderr="error: unknown")
        hypr.focus(runner, window(address="0xa"))
        self.assertEqual(runner.calls[-1], ("hyprctl", "dispatch", "focuswindow", "address:0xa"))

    def test_focus_raises_when_both_forms_fail(self) -> None:
        # Hyprland reports some dispatch errors with exit 0 and a message
        # instead of "ok", so the output is checked too.
        runner = FakeRunner().on("hyprctl", "dispatch", stdout="Invalid dispatcher\n")
        with self.assertRaises(hypr.HyprError):
            hypr.focus(runner, window(address="0xa"))
        self.assertEqual(len(runner.calls), 2)

    def test_odd_window_address_is_refused_before_dispatch(self) -> None:
        runner = FakeRunner().on("hyprctl", "dispatch", stdout="ok\n")
        for address in ('0x1" }); os.exit(', "", "12", "0xZZ"):
            with self.subTest(address=address), self.assertRaises(hypr.HyprError):
                hypr.focus(runner, window(address=address))
        self.assertEqual(runner.calls, [])

    def test_close_dispatch_forms(self) -> None:
        runner = FakeRunner().on("hyprctl", "dispatch", stdout="ok\n")
        hypr.close(runner, window(address="0xb"))
        self.assertEqual(runner.calls[-1], ("hyprctl", "dispatch", 'hl.dsp.window.close({ window = "address:0xb" })'))


class MatchTests(unittest.TestCase):
    def test_class_match_is_case_insensitive_and_class_only(self) -> None:
        zed = window(cls="dev.zed.Zed", title="kadha — x")
        self.assertTrue(hypr.class_matches(zed, r"^dev\.zed\.zed$"))
        self.assertFalse(hypr.class_matches(zed, "kadha"))
        self.assertTrue(hypr.matches(zed, "kadha"))

    def test_title_has_word_handles_zed_and_vscode_shapes(self) -> None:
        self.assertTrue(hypr.title_has_word(window(title="kadha — App.tsx"), "kadha"))
        self.assertTrue(hypr.title_has_word(window(title="App.tsx - kadha - Visual Studio Code"), "kadha"))
        self.assertTrue(hypr.title_has_word(window(title="", initial_title="kadha"), "kadha"))
        self.assertFalse(hypr.title_has_word(window(title="kadha-old — App.tsx"), "kadha"))
        self.assertFalse(hypr.title_has_word(window(title="Kadha - Brave"), "kadha"))
        self.assertTrue(hypr.title_has_word(window(title="muhsi.in — index.html"), "muhsi.in"))


class PidTests(unittest.TestCase):
    def test_window_for_pid_walks_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            for pid, ppid in ((50, 1), (60, 50), (100, 60), (200, 1)):
                (proc / str(pid)).mkdir()
                (proc / str(pid) / "status").write_text(f"PPid:\t{ppid}\n")
            windows = [window(address="0xc", cls="ghostty", pid=50), window(address="0xd", cls="zed", pid=999)]
            found = hypr.window_for_pid(windows, 100, proc_root=proc)
            self.assertIsNotNone(found)
            self.assertEqual(found.address, "0xc")
            self.assertIsNone(hypr.window_for_pid(windows, 200, proc_root=proc))
            self.assertIsNone(hypr.window_for_pid(windows, 12345, proc_root=proc))


if __name__ == "__main__":
    unittest.main()

"""Tests for the process and network helpers."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from omadev_cli import system


class PortTests(unittest.TestCase):
    def test_port_open_against_a_real_listener(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertTrue(system.port_open("127.0.0.1", port))
        self.assertFalse(system.port_open("127.0.0.1", port))

    def test_wait_for_port_polls_until_open_or_timeout(self) -> None:
        clock = {"t": 0.0}
        attempts = {"n": 0}
        slept: list[float] = []

        def probe(host: str, port: int) -> bool:
            attempts["n"] += 1
            return attempts["n"] >= 3

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            clock["t"] += seconds

        self.assertTrue(system.wait_for_port("h", 1, 5.0, interval=0.5, probe=probe, clock=lambda: clock["t"], sleep=sleep))
        self.assertEqual(slept, [0.5, 0.5])

        attempts["n"] = -100
        clock["t"] = 0.0
        self.assertFalse(system.wait_for_port("h", 1, 1.0, interval=0.5, probe=probe, clock=lambda: clock["t"], sleep=sleep))


class HttpTests(unittest.TestCase):
    def test_http_ok_needs_an_http_answer_not_just_a_port(self) -> None:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(503)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        hits = {"n": 0}

        class Redirecting(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                hits["n"] += 1
                self.send_response(302)
                self.send_header("Location", "http://example.invalid/never-fetched")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        for handler in (Handler, Redirecting):
            server = http.server.HTTPServer(("127.0.0.1", 0), handler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                # An error status still counts: the app is answering. So does
                # a redirect, which is answered without being followed.
                self.assertTrue(system.http_ok(f"http://127.0.0.1:{port}/"))
            finally:
                server.shutdown()
                server.server_close()
        self.assertEqual(hits["n"], 1, "the redirect target was not fetched")

        # A bare listening socket that never speaks HTTP is not "responding".
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            silent = listener.getsockname()[1]
            self.assertTrue(system.port_open("127.0.0.1", silent))
            self.assertFalse(system.http_ok(f"http://127.0.0.1:{silent}/", timeout=0.3))
        self.assertFalse(system.http_ok(f"http://127.0.0.1:{silent}/", timeout=0.3))

    def test_split_command(self) -> None:
        self.assertEqual(system.split_command("docker compose down"), ["docker", "compose", "down"])
        self.assertEqual(system.split_command("echo 'a b' c"), ["echo", "a b", "c"])
        with self.assertRaises(ValueError):
            system.split_command("   ")


class ProcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name)

    def add(self, pid: int, argv: list[str], ppid: int) -> None:
        directory = self.proc / str(pid)
        directory.mkdir()
        (directory / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (directory / "status").write_text(f"Name:\t{argv[0]}\nPPid:\t{ppid}\n")

    def test_list_processes_and_parent_pid(self) -> None:
        self.add(50, ["/usr/bin/ghostty"], 1)
        self.add(60, ["/usr/bin/bash", "--posix"], 50)
        self.add(100, ["herdr"], 60)
        (self.proc / "self").mkdir()
        self.assertEqual(sorted(system.list_processes(self.proc)), [(50, ["/usr/bin/ghostty"]), (60, ["/usr/bin/bash", "--posix"]), (100, ["herdr"])])
        self.assertEqual(system.parent_pid(100, self.proc), 60)
        self.assertEqual(system.parent_pid(60, self.proc), 50)
        self.assertIsNone(system.parent_pid(999, self.proc))


class RunnerTests(unittest.TestCase):
    def test_system_runner_runs_and_reports_missing_tools(self) -> None:
        runner = system.SystemRunner()
        result = runner.run(["true"])
        self.assertTrue(result.ok)
        with self.assertRaises(system.ToolMissing):
            runner.run(["definitely-not-a-real-tool-omadev"])

    def test_timeout_is_reported_not_raised(self) -> None:
        result = system.SystemRunner().run(["sleep", "5"], timeout=0.2)
        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out", result.stderr)

    def test_gui_argv_wraps_unless_omarchy_launcher(self) -> None:
        class R:
            def has(self, tool: str) -> bool:
                return tool == "uwsm-app"

        self.assertEqual(system.gui_argv(R(), ["zed", "/p"]), ["uwsm-app", "--", "zed", "/p"])
        self.assertEqual(system.gui_argv(R(), ["omarchy-launch-editor", "/p"]), ["omarchy-launch-editor", "/p"])


if __name__ == "__main__":
    unittest.main()

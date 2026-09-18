"""Tests for port ownership classification."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from omadev_cli import ports
from tests.fakes import FakeRunner

SS_NODE = 'LISTEN 0      511    *:3000 *:* users:(("node-MainThread",pid=1662415,fd=24))\n'
SS_NO_PID = "LISTEN 0      4096   127.0.0.1:5432 0.0.0.0:*\n"
DOCKER_PS = (
    '{"Names":"acta-acta-server-1","Ports":"127.0.0.1:8090-\\u003e8090/tcp","State":"running",'
    '"Labels":"com.docker.compose.project=acta,com.docker.compose.project.working_dir=/home/u/Development/Acta"}\n'
    '{"Names":"kadha-client-1","Ports":"0.0.0.0:3000-\\u003e3000/tcp, [::]:3000-\\u003e3000/tcp","State":"running",'
    '"Labels":"com.docker.compose.project.working_dir=/home/u/Development/kadha,com.docker.compose.service=client"}\n'
    '{"Names":"plain","Ports":"","State":"running","Labels":""}\n'
)


class ListenerTests(unittest.TestCase):
    def test_listener_reads_pid_name_and_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "proc"
            (proc / "1662415").mkdir(parents=True)
            (proc / "1662415" / "comm").write_text("node\n")
            project = Path(tmp) / "site"
            project.mkdir()
            os.symlink(project, proc / "1662415" / "cwd")
            runner = FakeRunner(tools=["ss"]).on("ss", stdout=SS_NODE)
            who = ports.listener(runner, 3000, proc_root=proc)
        self.assertEqual((who.pid, who.name, who.cwd), (1662415, "node", project))
        self.assertEqual(runner.calls[-1], ("ss", "-ltnpH", "sport = :3000"))

    def test_listener_without_visible_pid(self) -> None:
        runner = FakeRunner(tools=["ss"]).on("ss", stdout=SS_NO_PID)
        who = ports.listener(runner, 5432)
        self.assertEqual(who, ports.Listener(pid=None, name="", cwd=None))
        self.assertEqual(who.description, "a process")

    def test_nobody_listening(self) -> None:
        runner = FakeRunner(tools=["ss"]).on("ss", stdout="")
        self.assertIsNone(ports.listener(runner, 3000))


class ContainerTests(unittest.TestCase):
    def test_parses_docker_ps(self) -> None:
        runner = FakeRunner(tools=["docker"]).on("docker", "ps", stdout=DOCKER_PS)
        found = ports.containers(runner)
        self.assertEqual(len(found), 3)
        self.assertEqual(found[0].ports, frozenset({8090}))
        self.assertEqual(found[0].working_dir, Path("/home/u/Development/Acta"))
        self.assertEqual(found[1].ports, frozenset({3000}))
        self.assertEqual(found[2].ports, frozenset())
        self.assertIsNone(found[2].working_dir)

    def test_docker_missing_or_failing_is_none(self) -> None:
        self.assertIsNone(ports.containers(FakeRunner(tools=[])))
        runner = FakeRunner(tools=["docker"]).on("docker", "ps", returncode=1, stderr="Cannot connect to the Docker daemon")
        self.assertIsNone(ports.containers(runner))


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = Path("/home/u/Development/kadha")
        self.other = Path("/home/u/Development/muhsi.in")
        self.docker_calls = 0

    def classify(self, port: int, *, listening: bool, who: ports.Listener | None, running: list | None) -> ports.PortState:
        def find_containers():
            self.docker_calls += 1
            return running

        return ports.classify(port, self.project, probe=lambda h, p: listening, find_listener=lambda p: who, find_containers=find_containers)

    def test_free(self) -> None:
        state = self.classify(3000, listening=False, who=None, running=[])
        self.assertEqual(state.owner, ports.FREE)
        self.assertFalse(state.listening)
        self.assertEqual(self.docker_calls, 0)

    def test_process_from_this_project(self) -> None:
        who = ports.Listener(10, "node", self.project / "client")
        state = self.classify(3000, listening=True, who=who, running=[])
        self.assertEqual(state.owner, ports.PROJECT)
        self.assertIn("node (pid 10)", state.detail)
        self.assertEqual(self.docker_calls, 0)

    def test_process_from_another_project(self) -> None:
        who = ports.Listener(10, "node", self.other)
        state = self.classify(3000, listening=True, who=who, running=[])
        self.assertEqual(state.owner, ports.OTHER)
        self.assertIn("muhsi.in", state.detail)

    def test_docker_container_from_this_project(self) -> None:
        running = [ports.Container("kadha-client-1", frozenset({3000}), self.project)]
        state = self.classify(3000, listening=True, who=ports.Listener(None, "", None), running=running)
        self.assertEqual(state.owner, ports.PROJECT)
        self.assertIn("kadha-client-1", state.detail)

    def test_docker_proxy_is_resolved_through_docker(self) -> None:
        running = [ports.Container("acta-acta-server-1", frozenset({8090}), Path("/home/u/Development/Acta"))]
        who = ports.Listener(5, "docker-proxy", Path("/"))
        state = self.classify(8090, listening=True, who=who, running=running)
        self.assertEqual(state.owner, ports.OTHER)
        self.assertIn("acta-acta-server-1", state.detail)

    def test_unknown_when_nothing_explains_the_port(self) -> None:
        state = self.classify(5432, listening=True, who=ports.Listener(None, "", None), running=[])
        self.assertEqual(state.owner, ports.UNKNOWN)
        state = self.classify(5432, listening=True, who=None, running=None)
        self.assertEqual(state.owner, ports.UNKNOWN)
        self.assertEqual(state.detail, "listening, owner not visible")

    def test_is_within(self) -> None:
        self.assertTrue(ports.is_within(Path("/a/b/c"), Path("/a/b")))
        self.assertTrue(ports.is_within(Path("/a/b"), Path("/a/b")))
        self.assertFalse(ports.is_within(Path("/a/bc"), Path("/a/b")))
        self.assertFalse(ports.is_within(Path("/a"), Path("/a/b")))


if __name__ == "__main__":
    unittest.main()

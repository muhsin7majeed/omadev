"""Who owns a listening port.

A bare port check cannot tell this project's dev server from another
project's server that happens to use the same port, and two web projects on
port 3000 is the normal case, not the exception. So a listening port is
classified before Start trusts it:

  free     nothing listens
  project  the listening process, or the container publishing the port,
           belongs to this project (its working directory is inside it)
  other    it belongs to somewhere else; Start refuses rather than opening
           the browser on the wrong site
  unknown  something listens but its owner is not visible; Start does not
           start a second server, and says so

Process ownership comes from `ss -p` and /proc. Docker-published ports are
owned by a root-run proxy that `ss` cannot attribute for a normal user, so
those are matched through `docker ps` and the compose working-directory
label instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .system import Runner, ToolMissing

FREE = "free"
PROJECT = "project"
OTHER = "other"
UNKNOWN = "unknown"

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"})
COMPOSE_DIR_LABEL = "com.docker.compose.project.working_dir"
DOCKER_PROXY = "docker-proxy"

_PID = re.compile(r"pid=(\d+)")
_PUBLISHED = re.compile(r"(?:^|[\s,])(?:[\d.]+|\[[0-9a-f:]*\]|::)?:?(\d+)->")


@dataclass(frozen=True)
class Listener:
    """The process behind a listening port, as far as we can see it."""

    pid: int | None
    name: str
    cwd: Path | None

    @property
    def description(self) -> str:
        who = self.name or "a process"
        if self.pid is not None:
            who += f" (pid {self.pid})"
        return who


@dataclass(frozen=True)
class Container:
    name: str
    ports: frozenset[int]
    working_dir: Path | None


@dataclass(frozen=True)
class PortState:
    port: int
    owner: str
    detail: str

    @property
    def listening(self) -> bool:
        return self.owner != FREE

    def to_dict(self) -> dict:
        return {"port": self.port, "listening": self.listening, "owner": self.owner, "detail": self.detail}


def is_local_host(host: str) -> bool:
    return host.lower() in LOCAL_HOSTS


def is_within(path: Path, root: Path) -> bool:
    """True if `path` is `root` or lies under it, ignoring symlinks."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def listener(runner: Runner, port: int, *, proc_root: Path = Path("/proc")) -> Listener | None:
    """Who listens on the TCP port, or None if nobody does according to `ss`."""
    result = runner.run(["ss", "-ltnpH", f"sport = :{port}"], timeout=5.0)
    if not result.ok or not result.stdout.strip():
        return None
    match = _PID.search(result.stdout)
    if match is None:
        return Listener(pid=None, name="", cwd=None)
    pid = int(match.group(1))
    return Listener(pid=pid, name=_comm(pid, proc_root), cwd=_cwd(pid, proc_root))


def _comm(pid: int, proc_root: Path) -> str:
    try:
        return (proc_root / str(pid) / "comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _cwd(pid: int, proc_root: Path) -> Path | None:
    try:
        return Path((proc_root / str(pid) / "cwd").readlink())
    except OSError:
        return None


def containers(runner: Runner) -> list[Container] | None:
    """Running containers with their published ports, or None if docker
    is missing or not answering."""
    if not runner.has("docker"):
        return None
    try:
        result = runner.run(["docker", "ps", "--format", "{{json .}}"], timeout=10.0)
    except ToolMissing:
        return None
    if not result.ok:
        return None
    found = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        found.append(Container(
            name=str(item.get("Names", "")),
            ports=frozenset(int(p) for p in _PUBLISHED.findall(str(item.get("Ports", "")))),
            working_dir=_label(str(item.get("Labels", "")), COMPOSE_DIR_LABEL),
        ))
    return found


def _label(labels: str, key: str) -> Path | None:
    for pair in labels.split(","):
        name, _, value = pair.partition("=")
        if name == key and value:
            return Path(value)
    return None


def classify(
    port: int,
    project_path: Path,
    *,
    probe: Callable[[str, int], bool],
    find_listener: Callable[[int], Listener | None],
    find_containers: Callable[[], list[Container] | None],
) -> PortState:
    """Decide whether a local port is free, ours, someone else's, or unclear."""
    if not probe("localhost", port):
        return PortState(port, FREE, "not listening")

    try:
        who = find_listener(port)
    except (ToolMissing, OSError):
        who = None

    if who is not None and who.cwd is not None and who.name != DOCKER_PROXY:
        if is_within(who.cwd, project_path):
            return PortState(port, PROJECT, f"{who.description} from this project")
        return PortState(port, OTHER, f"{who.description} running in {who.cwd}")

    # Either docker's proxy or a process we cannot see into: ask docker.
    running = find_containers()
    if running is not None:
        for container in running:
            if port not in container.ports:
                continue
            if container.working_dir is not None and is_within(container.working_dir, project_path):
                return PortState(port, PROJECT, f"container {container.name} from this project")
            where = f" from {container.working_dir}" if container.working_dir is not None else ""
            return PortState(port, OTHER, f"container {container.name}{where}")

    if who is not None:
        return PortState(port, UNKNOWN, f"{who.description}, owner not determinable")
    return PortState(port, UNKNOWN, "listening, owner not visible")

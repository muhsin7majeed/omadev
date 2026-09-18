"""Command line interface.

Output contract, relied on by the widget: with `--json`, exactly one JSON
object is printed to stdout, always carrying `"ok"`. The exit code is 0 only
when `ok` is true. Without `--json`, output is short human-readable text and
warnings go to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__
from . import config as cfg
from . import steps

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

LOG_BYTES = 256 * 1024
LOG_BACKUPS = 3

Result = tuple[dict[str, Any], int]
ServicesFactory = Callable[[], steps.Services]

log = logging.getLogger("omadev")


class CliError(Exception):
    """A user-facing failure that is not a config problem."""


def _project_summary(project: cfg.Project, config: cfg.Config) -> dict[str, Any]:
    return {
        "name": project.name,
        "path": str(project.path),
        "multiplexer": project.multiplexer,
        "session": project.session_name,
        "url": project.url,
        "browser": config.browser_for(project),
        "commands": [c.name for c in project.commands],
        "apps": [a.name for a in project.apps],
        "mode": project.mode,
    }


def _project_named(config: cfg.Config, name: str) -> cfg.Project:
    project = config.find(name)
    if project is None:
        known = ", ".join(p.name for p in config.projects) or "none configured"
        raise CliError(f"unknown project '{name}' (known: {known})")
    return project


# ------------------------------------------------------------------ commands


def cmd_list(args: argparse.Namespace) -> Result:
    """Configured projects, without touching the system."""
    path = args.file or cfg.projects_file()
    config = cfg.load(path, check_paths=False)
    return {
        "ok": True,
        "file": str(path),
        "exists": path.exists(),
        "projects": [_project_summary(p, config) for p in config.projects],
        "warnings": list(config.warnings),
    }, EXIT_OK


def cmd_validate(args: argparse.Namespace) -> Result:
    """Full validation, including that every project path exists."""
    path = args.file or cfg.projects_file()
    config = cfg.load(path, check_paths=True)
    return {
        "ok": True,
        "file": str(path),
        "exists": path.exists(),
        "count": len(config.projects),
        "warnings": list(config.warnings),
    }, EXIT_OK


def cmd_status(args: argparse.Namespace) -> Result:
    """Read-only snapshot of what is up for one or every project."""
    config = cfg.load(args.file or cfg.projects_file(), check_paths=False)
    projects = [_project_named(config, args.project)] if args.project else list(config.projects)
    services = args.services_factory()
    return {
        "ok": True,
        "projects": [steps.status(p, config, services) for p in projects],
        "warnings": list(config.warnings),
    }, EXIT_OK


def cmd_start(args: argparse.Namespace) -> Result:
    """Bring a project up; every step is check-then-act."""
    config = cfg.load(args.file or cfg.projects_file(), check_paths=True)
    project = _project_named(config, args.project)
    results = steps.start(project, config, args.services_factory(), dry_run=args.dry_run)
    ok = steps.succeeded(results)
    for result in results:
        log.info("start %s %s %s: %s", project.name, result.step, result.status, result.detail)
    payload: dict[str, Any] = {
        "ok": ok,
        "project": project.name,
        "dry_run": args.dry_run,
        "steps": [r.to_dict() for r in results],
        "warnings": list(config.warnings),
    }
    if not ok:
        failed = [r.step for r in results if r.status == steps.FAILED]
        payload["error"] = "failed: " + ", ".join(failed)
    return payload, EXIT_OK if ok else EXIT_FAILURE


# ------------------------------------------------------------- text output


def _print_list(result: dict[str, Any], out: TextIO) -> None:
    if not result["exists"]:
        out.write(f"No projects file yet at {result['file']}\n")
        return
    if not result["projects"]:
        out.write(f"No projects configured in {result['file']}\n")
        return
    width = max(len(p["name"]) for p in result["projects"])
    for project in result["projects"]:
        out.write(f"{project['name']:<{width}}  {project['multiplexer']:<5}  {project['path']}\n")


def _print_validate(result: dict[str, Any], out: TextIO) -> None:
    if not result["exists"]:
        out.write(f"No projects file yet at {result['file']}; nothing to validate\n")
        return
    noun = "project" if result["count"] == 1 else "projects"
    out.write(f"OK: {result['count']} {noun} in {result['file']}\n")


def _print_status(result: dict[str, Any], out: TextIO) -> None:
    for snapshot in result["projects"]:
        workspace = snapshot["workspace"]
        out.write(f"{snapshot['name']}\n")
        out.write(f"  {snapshot['multiplexer']:<9} {'present' if workspace['present'] else 'absent'}"
                  f"{' (' + workspace['id'] + ')' if workspace.get('id') else ''}\n")
        for command in snapshot["commands"]:
            state = "no port" if command["listening"] is None else ("listening" if command["listening"] else "down")
            port = f" :{command['port']}" if command["port"] is not None else ""
            out.write(f"  command   {command['name']}{port} {state}\n")
        if snapshot["url"]:
            out.write(f"  url       {snapshot['url']['value']} {'reachable' if snapshot['url']['reachable'] else 'down'}\n")
        if snapshot.get("editor_open") is not None:
            out.write(f"  editor    {'open' if snapshot['editor_open'] else 'closed'}\n")
        for app in snapshot["apps"]:
            out.write(f"  app       {app['name']} {'open' if app['open'] else 'closed'}\n")


def _print_start(result: dict[str, Any], out: TextIO) -> None:
    heading = "Plan for" if result["dry_run"] else "Started"
    out.write(f"{heading} {result['project']}\n")
    width = max((len(s["step"]) for s in result["steps"]), default=0)
    for step in result["steps"]:
        out.write(f"  {step['status']:<8} {step['step']:<{width}}  {step['detail']}\n")


_TEXT_PRINTERS: dict[str, Callable[[dict[str, Any], TextIO], None]] = {
    "list": _print_list,
    "validate": _print_validate,
    "status": _print_status,
    "start": _print_start,
}


# ------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    common.add_argument("--file", type=Path, metavar="PATH", help="projects file to use instead of the default")

    parser = argparse.ArgumentParser(
        prog="omadev",
        description="Start and stop whole development projects, without duplicating what is already running.",
    )
    parser.add_argument("--version", action="version", version=f"omadev {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    sub = subparsers.add_parser("list", parents=[common], help="show configured projects")
    sub.set_defaults(func=cmd_list)

    sub = subparsers.add_parser("validate", parents=[common], help="check the projects file, including that paths exist")
    sub.set_defaults(func=cmd_validate)

    sub = subparsers.add_parser("status", parents=[common], help="show what is currently up for a project, or all")
    sub.add_argument("project", nargs="?", help="project name; omit for every project")
    sub.set_defaults(func=cmd_status)

    sub = subparsers.add_parser("start", parents=[common], help="bring a project up without duplicating what already runs")
    sub.add_argument("project", help="project name from the projects file")
    sub.add_argument("--dry-run", action="store_true", help="report what would happen without changing anything")
    sub.set_defaults(func=cmd_start)

    return parser


def emit(command: str, result: dict[str, Any], *, as_json: bool, out: TextIO, err: TextIO) -> None:
    if as_json:
        out.write(json.dumps(result, ensure_ascii=False) + "\n")
        return
    if result.get("ok") or "steps" in result:
        _TEXT_PRINTERS[command](result, out)
    if not result.get("ok"):
        err.write(f"omadev: {result.get('error', 'failed')}\n")
    for warning in result.get("warnings", ()):
        err.write(f"warning: {warning}\n")


def configure_logging(err: TextIO) -> None:
    """Append step outcomes to a size-rotated log; never fail the run over it."""
    if log.handlers:
        return
    try:
        directory = cfg.state_dir()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        handler = logging.handlers.RotatingFileHandler(directory / "omadev.log", maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError as exc:
        err.write(f"warning: logging disabled ({exc})\n")
        log.addHandler(logging.NullHandler())
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    services_factory: ServicesFactory = steps.Services.real,
    logging_enabled: bool = True,
) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse has already printed usage or the version; keep its exit code.
        return int(exc.code or 0)
    args.services_factory = services_factory
    if logging_enabled:
        configure_logging(err)

    try:
        result, code = args.func(args)
    except cfg.ConfigError as exc:
        result, code = {"ok": False, "error": str(exc), "where": exc.where}, EXIT_FAILURE
    except CliError as exc:
        result, code = {"ok": False, "error": str(exc)}, EXIT_FAILURE

    emit(args.command, result, as_json=args.json, out=out, err=err)
    return code

"""Command line interface.

Output contract, relied on by the widget: with `--json`, exactly one JSON
object is printed to stdout, always carrying `"ok"`. The exit code is 0 only
when `ok` is true. Without `--json`, output is short human-readable text and
warnings go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__
from . import config as cfg

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

Result = tuple[dict[str, Any], int]


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


_TEXT_PRINTERS: dict[str, Callable[[dict[str, Any], TextIO], None]] = {
    "list": _print_list,
    "validate": _print_validate,
}


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

    return parser


def emit(command: str, result: dict[str, Any], *, as_json: bool, out: TextIO, err: TextIO) -> None:
    if as_json:
        out.write(json.dumps(result, ensure_ascii=False) + "\n")
        return
    if result.get("ok"):
        _TEXT_PRINTERS[command](result, out)
    else:
        err.write(f"omadev: {result.get('error', 'failed')}\n")
    for warning in result.get("warnings", ()):
        err.write(f"warning: {warning}\n")


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout, err: TextIO = sys.stderr) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse has already printed usage or the version; keep its exit code.
        return int(exc.code or 0)

    try:
        result, code = args.func(args)
    except cfg.ConfigError as exc:
        result, code = {"ok": False, "error": str(exc), "where": exc.where}, EXIT_FAILURE

    emit(args.command, result, as_json=args.json, out=out, err=err)
    return code

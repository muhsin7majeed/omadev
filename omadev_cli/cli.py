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
import os
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__
from . import config as cfg
from . import forms, schema, steps

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


def cmd_show(args: argparse.Namespace) -> Result:
    """One project's full configuration, as the edit form needs it."""
    config = cfg.load(args.file or cfg.projects_file(), check_paths=False)
    project = _project_named(config, args.project)
    return {"ok": True, "project": cfg.project_to_dict(project), "warnings": list(config.warnings)}, EXIT_OK


def cmd_schema(args: argparse.Namespace) -> Result:
    """The form description the widget renders, with the file's defaults named."""
    config = cfg.load(args.file or cfg.projects_file(), check_paths=False)
    return {"ok": True, **schema.describe(config.default_browser)}, EXIT_OK


def _decode_project_argument(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(f"project is not valid JSON (line {exc.lineno}, column {exc.colno})") from exc


def cmd_add(args: argparse.Namespace) -> Result:
    """Validate a project given as JSON and append it to the projects file."""
    path = args.file or cfg.projects_file()
    config = cfg.load(path, check_paths=False)
    project, warnings = forms.project_from_form(_decode_project_argument(args.project_json))
    updated = cfg.with_project(config, project)
    cfg.save(updated, path)
    log.info("add %s", project.name)
    return {"ok": True, "project": cfg.project_to_dict(project), "warnings": list(config.warnings) + list(warnings)}, EXIT_OK


def cmd_edit(args: argparse.Namespace) -> Result:
    """Replace one project with a validated JSON version; renaming is allowed."""
    path = args.file or cfg.projects_file()
    config = cfg.load(path, check_paths=False)
    _project_named(config, args.project)
    project, warnings = forms.project_from_form(_decode_project_argument(args.project_json))
    updated = cfg.with_project(config, project, replacing=args.project)
    cfg.save(updated, path)
    log.info("edit %s -> %s", args.project, project.name)
    return {"ok": True, "project": cfg.project_to_dict(project), "warnings": list(config.warnings) + list(warnings)}, EXIT_OK


def cmd_remove(args: argparse.Namespace) -> Result:
    """Delete a project from the file. Nothing running is touched."""
    path = args.file or cfg.projects_file()
    config = cfg.load(path, check_paths=False)
    _project_named(config, args.project)
    cfg.save(cfg.without_project(config, args.project), path)
    log.info("remove %s", args.project)
    return {"ok": True, "removed": args.project, "warnings": list(config.warnings)}, EXIT_OK


def cmd_status(args: argparse.Namespace) -> Result:
    """Read-only snapshot of what is up for one or every project."""
    path = args.file or cfg.projects_file()
    config = cfg.load(path, check_paths=False)
    projects = [_project_named(config, args.project)] if args.project else list(config.projects)
    services = args.services_factory()
    return {
        "ok": True,
        "file": str(path),
        "exists": path.exists(),
        "projects": [steps.status(p, config, services) for p in projects],
        "warnings": list(config.warnings),
    }, EXIT_OK


def _run_steps(args: argparse.Namespace, action: str, runner: Callable[..., list[steps.StepResult]]) -> Result:
    config = cfg.load(args.file or cfg.projects_file(), check_paths=True)
    project = _project_named(config, args.project)
    results = runner(project, config, args.services_factory(), dry_run=args.dry_run)
    ok = steps.succeeded(results)
    for result in results:
        log.info("%s %s %s %s: %s", action, project.name, result.step, result.status, result.detail)
    payload: dict[str, Any] = {
        "ok": ok,
        "action": action,
        "project": project.name,
        "dry_run": args.dry_run,
        "steps": [r.to_dict() for r in results],
        "warnings": list(config.warnings),
    }
    if not ok:
        failed = [r.step for r in results if r.status == steps.FAILED]
        payload["error"] = "failed: " + ", ".join(failed)
    return payload, EXIT_OK if ok else EXIT_FAILURE


def cmd_start(args: argparse.Namespace) -> Result:
    """Bring a project up; every step is check-then-act."""
    return _run_steps(args, "start", steps.start)


def cmd_stop(args: argparse.Namespace) -> Result:
    """Tear down what Start brought up, leaving the workspace in place."""
    return _run_steps(args, "stop", steps.stop)


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
            if command["listening"] is None:
                state = "no port"
            elif command["listening"]:
                state = f"listening, {_owner_phrase(command)}"
            else:
                state = "down"
            port = f" :{command['port']}" if command["port"] is not None else ""
            out.write(f"  command   {command['name']}{port} {state}\n")
        if snapshot["url"]:
            url = snapshot["url"]
            if url["reachable"]:
                state = f"responding, {_owner_phrase(url)}"
            elif url["listening"]:
                state = f"port open but no HTTP answer, {_owner_phrase(url)}"
            else:
                state = "down"
            out.write(f"  url       {url['value']} {state}\n")
        if snapshot.get("editor_open") is not None:
            out.write(f"  editor    {'open' if snapshot['editor_open'] else 'closed'}\n")
        for app in snapshot["apps"]:
            out.write(f"  app       {app['name']} {'open' if app['open'] else 'closed'}\n")


def _owner_phrase(entry: dict[str, Any]) -> str:
    owner = entry.get("owner")
    detail = entry.get("detail") or ""
    if owner == "project":
        return "this project"
    if owner == "other":
        return f"OTHER: {detail}"
    return f"owner unknown ({detail})" if detail else "owner unknown"


def _print_steps(result: dict[str, Any], out: TextIO) -> None:
    if result["dry_run"]:
        heading = f"Plan to {result['action']}"
    else:
        heading = "Started" if result["action"] == "start" else "Stopped"
    out.write(f"{heading} {result['project']}\n")
    width = max((len(s["step"]) for s in result["steps"]), default=0)
    for step in result["steps"]:
        out.write(f"  {step['status']:<8} {step['step']:<{width}}  {step['detail']}\n")


def _print_project(result: dict[str, Any], out: TextIO) -> None:
    out.write(json.dumps(result["project"], indent=2, ensure_ascii=False) + "\n")


def _print_removed(result: dict[str, Any], out: TextIO) -> None:
    out.write(f"Removed project '{result['removed']}'\n")


def _print_schema(result: dict[str, Any], out: TextIO) -> None:
    for section in result["sections"]:
        out.write(f"[{section['title']}]{' (collapsed)' if section.get('collapsed') else ''}\n")
        for field in section["fields"]:
            flag = "" if field.get("optional") else " (required)"
            out.write(f"  {field['key']:<22} {field['kind']:<10} {field['label']}{flag}\n")


_TEXT_PRINTERS: dict[str, Callable[[dict[str, Any], TextIO], None]] = {
    "list": _print_list,
    "validate": _print_validate,
    "status": _print_status,
    "start": _print_steps,
    "stop": _print_steps,
    "show": _print_project,
    "add": _print_project,
    "edit": _print_project,
    "remove": _print_removed,
    "schema": _print_schema,
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

    sub = subparsers.add_parser("stop", parents=[common], help="stop a project's commands and close its windows; the workspace stays")
    sub.add_argument("project", help="project name from the projects file")
    sub.add_argument("--dry-run", action="store_true", help="report what would happen without changing anything")
    sub.set_defaults(func=cmd_stop)

    sub = subparsers.add_parser("show", parents=[common], help="print one project's full configuration")
    sub.add_argument("project", help="project name from the projects file")
    sub.set_defaults(func=cmd_show)

    sub = subparsers.add_parser("add", parents=[common], help="add a project given as one JSON object")
    sub.add_argument("project_json", metavar="JSON", help='e.g. \'{"name": "demo", "path": "~/dev/demo"}\'')
    sub.set_defaults(func=cmd_add)

    sub = subparsers.add_parser("edit", parents=[common], help="replace a project with the given JSON object")
    sub.add_argument("project", help="current project name")
    sub.add_argument("project_json", metavar="JSON", help="the complete new configuration")
    sub.set_defaults(func=cmd_edit)

    sub = subparsers.add_parser("remove", parents=[common], help="delete a project from the projects file")
    sub.add_argument("project", help="project name from the projects file")
    sub.set_defaults(func=cmd_remove)

    sub = subparsers.add_parser("schema", parents=[common], help="describe the project fields the form renders")
    sub.set_defaults(func=cmd_schema)

    return parser


def emit(command: str, result: dict[str, Any], *, as_json: bool, out: TextIO, err: TextIO) -> None:
    if as_json:
        out.write(json.dumps(result, ensure_ascii=False) + "\n")
        return
    if result.get("ok") or "steps" in result:
        _TEXT_PRINTERS[command](result, out)
        # stdout is buffered and stderr is not; keep the terminal order sane.
        out.flush()
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
        path = directory / "omadev.log"
        # The log names project folders and commands; keep it to the owner.
        # Created here with 0600 before the handler opens it, since the
        # handler would otherwise follow the umask. Rotated copies inherit.
        os.close(os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600))
        os.chmod(path, 0o600)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
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

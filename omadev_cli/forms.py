"""Turn what a form submits into a project the validator accepts.

A form hands over strings: an empty text field, a port typed as "3000", a
launch command typed as one line. This module normalises those into the
shapes `config.parse_project` expects, and nothing else: every rule about
what is valid stays in `config.py`, so the form and the file are checked by
the same code.
"""

from __future__ import annotations

import shlex
from typing import Any

from . import config as cfg
from . import schema

_INTEGER_KEYS = {"port", "wait_timeout"}
_ARGV_KEYS = {"launch"}


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _integer(value: Any, where: str) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
        raise cfg.ConfigError(where, "must be a whole number")
    return value


def _boolean(value: Any, where: str) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "on", "yes", "1"):
            return True
        if text in ("false", "off", "no", "0"):
            return False
    raise cfg.ConfigError(where, "must be true or false")


def _argv(value: Any, where: str) -> Any:
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError as exc:
            raise cfg.ConfigError(where, f"could not be split into a command ({exc})") from exc
    return value


def _normalise_fields(data: dict[str, Any], fields: list[dict], where: str) -> dict[str, Any]:
    """Apply the per-kind conversions and drop empty optional values."""
    out: dict[str, Any] = {}
    known = {f["key"]: f for f in fields}
    for key, value in data.items():
        spec = known.get(key)
        here = f"{where}.{key}" if where else key
        if spec is None:
            out[key] = value          # left for the validator to warn about
            continue
        kind = spec["kind"]
        if kind == "boolean":
            if value is None or value == "":
                continue              # unset; the validator applies the default
            out[key] = _boolean(value, here)
            continue
        if _empty(value):
            if spec.get("optional") or kind == "list":
                continue              # unset; the validator applies the default or complains
            out[key] = value
            continue
        if kind == "integer":
            value = _integer(value, here)
        elif kind == "argv":
            value = _argv(value, here)
        elif kind == "object" and isinstance(value, dict):
            value = _normalise_fields(value, spec["fields"], here)
            # A group with nothing but an untouched toggle is an unset group.
            if all(_empty(v) or v is False for v in value.values()):
                continue
        elif kind == "list" and isinstance(value, list):
            item = spec["item"]
            items = []
            for index, entry in enumerate(value):
                if item["kind"] == "object" and isinstance(entry, dict):
                    entry = _normalise_fields(entry, item["fields"], f"{here}[{index}]")
                    if all(_empty(v) for v in entry.values()):
                        continue      # a blank row the user added and never filled
                elif _empty(entry):
                    continue
                items.append(entry)
            if not items:
                continue
            value = items
        out[key] = value
    return out


def normalise_project(data: Any) -> dict[str, Any]:
    """Form input to validator input. Raises ConfigError for unparseable numbers or commands."""
    if not isinstance(data, dict):
        raise cfg.ConfigError("project", "must be a JSON object")
    return _normalise_fields(data, schema.PROJECT_FIELDS, "")


def project_from_form(data: Any, *, check_paths: bool = True) -> tuple[cfg.Project, tuple[str, ...]]:
    """Normalise, then validate exactly as the projects file is validated."""
    return cfg.parse_project(normalise_project(data), check_paths=check_paths)

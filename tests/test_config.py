"""Tests for omadev_cli.config: parsing, validation messages, atomic save."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from omadev_cli import config as cfg


def minimal(path: str, **extra: object) -> dict:
    project = {"name": "demo", "path": path}
    project.update(extra)
    return {"version": 1, "projects": [project]}


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def parse(self, data: object, **kwargs: object) -> cfg.Config:
        return cfg.parse(data, **kwargs)

    def test_full_project_round_trips(self) -> None:
        data = minimal(
            self.dir,
            multiplexer="tmux",
            session="demo-sess",
            commands=[{"name": "web", "run": "npm run dev", "port": 3000}],
            url="http://localhost:3000",
            browser="browser",
            editor=["zed"],
            apps=[{"name": "lazydocker", "launch": ["omarchy-launch-tui", "lazydocker"], "match": "lazydocker"}],
            stop_commands=["docker compose down"],
            mode="parallel",
        )
        config = self.parse(data)
        project = config.projects[0]
        self.assertEqual(project.session_name, "demo-sess")
        self.assertEqual(project.commands[0].port, 3000)
        self.assertEqual(project.url_port, 3000)
        self.assertEqual(config.browser_for(project), "browser")
        self.assertEqual(project.apps[0].launch, ("omarchy-launch-tui", "lazydocker"))
        self.assertEqual(cfg.parse(cfg.to_dict(config)), config)

    def test_defaults(self) -> None:
        project = self.parse(minimal(self.dir)).projects[0]
        self.assertEqual(project.multiplexer, "herdr")
        self.assertEqual(project.session_name, "demo")
        self.assertEqual(project.mode, "sequential")
        self.assertIsNone(project.url_port)
        self.assertEqual(self.parse(minimal(self.dir)).browser_for(project), "webapp")

    def test_url_port_defaults_by_scheme(self) -> None:
        self.assertEqual(cfg.url_port("http://localhost"), 80)
        self.assertEqual(cfg.url_port("https://localhost"), 443)
        self.assertEqual(cfg.url_port("http://127.0.0.1:8080/x"), 8080)

    def test_missing_name_is_an_error(self) -> None:
        with self.assertRaises(cfg.ConfigError) as ctx:
            self.parse({"version": 1, "projects": [{"path": self.dir}]})
        self.assertEqual(ctx.exception.where, "projects[0]")

    def test_wrong_type_names_the_field(self) -> None:
        with self.assertRaises(cfg.ConfigError) as ctx:
            self.parse(minimal(self.dir, commands="npm run dev"))
        self.assertEqual(ctx.exception.where, "project 'demo'.commands")

    def test_bad_port(self) -> None:
        for port in (0, 70000, "3000", True):
            with self.subTest(port=port), self.assertRaises(cfg.ConfigError) as ctx:
                self.parse(minimal(self.dir, commands=[{"name": "web", "run": "x", "port": port}]))
            self.assertEqual(ctx.exception.where, "project 'demo'.commands[0].port")

    def test_bad_url(self) -> None:
        for url in ("localhost:3000", "ftp://x", "http://", "http://localhost:99999"):
            with self.subTest(url=url), self.assertRaises(cfg.ConfigError):
                self.parse(minimal(self.dir, url=url))

    def test_bad_name(self) -> None:
        for name in ("", "../etc", "a" * 65, "semi;colon"):
            with self.subTest(name=name), self.assertRaises(cfg.ConfigError):
                self.parse({"version": 1, "projects": [{"name": name, "path": self.dir}]})

    def test_bad_choices(self) -> None:
        for field, value in (("multiplexer", "zellij"), ("browser", "firefox"), ("mode", "fast")):
            with self.subTest(field=field), self.assertRaises(cfg.ConfigError):
                self.parse(minimal(self.dir, **{field: value}))

    def test_bad_regex_in_app_match(self) -> None:
        with self.assertRaises(cfg.ConfigError):
            self.parse(minimal(self.dir, apps=[{"name": "x", "launch": ["x"], "match": "("}]))

    def test_missing_path_fails_unless_unchecked(self) -> None:
        missing = os.path.join(self.dir, "nope")
        with self.assertRaises(cfg.ConfigError):
            self.parse(minimal(missing))
        self.assertEqual(len(self.parse(minimal(missing), check_paths=False).projects), 1)

    def test_duplicate_names(self) -> None:
        data = {"version": 1, "projects": [{"name": "demo", "path": self.dir}, {"name": "demo", "path": self.dir}]}
        with self.assertRaises(cfg.ConfigError):
            self.parse(data)

    def test_unsupported_version(self) -> None:
        with self.assertRaises(cfg.ConfigError):
            self.parse({"version": 2, "projects": []})

    def test_unknown_fields_warn(self) -> None:
        config = self.parse(minimal(self.dir, colour="red"))
        self.assertEqual(config.warnings, ("projects[0]: unknown field 'colour' ignored",))


class LoadSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "cfg" / "projects.json"

    def test_missing_file_is_empty_config(self) -> None:
        self.assertEqual(cfg.load(self.file), cfg.Config())

    def test_invalid_json_reports_position(self) -> None:
        self.file.parent.mkdir()
        self.file.write_text("{\n  broken\n", encoding="utf-8")
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.load(self.file)
        self.assertIn("line 2", ctx.exception.message)

    def test_save_creates_private_file_and_round_trips(self) -> None:
        config = cfg.parse(minimal(str(self.dir)))
        cfg.save(config, self.file)
        mode = stat.S_IMODE(self.file.stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(stat.S_IMODE(self.file.parent.stat().st_mode), 0o700)
        self.assertEqual(cfg.load(self.file), config)
        self.assertEqual(json.loads(self.file.read_text())["version"], 1)
        leftovers = [p for p in self.file.parent.iterdir() if p.name != "projects.json"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()

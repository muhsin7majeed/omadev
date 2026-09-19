"""Tests for form normalisation, the schema, and the add/edit/remove commands."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from omadev_cli import cli
from omadev_cli import config as cfg
from omadev_cli import forms, schema
from tests.fakes import FakeServices


class SchemaParityTests(unittest.TestCase):
    """The form description and the validator must agree, or the form lies."""

    def keys(self, fields: list[dict]) -> set[str]:
        return {f["key"] for f in fields}

    def test_project_fields_match_validator(self) -> None:
        self.assertEqual(self.keys(schema.PROJECT_FIELDS), set(cfg._PROJECT_FIELDS))
        self.assertEqual(self.keys(schema.COMMAND_FIELDS), set(cfg._COMMAND_FIELDS))
        self.assertEqual(self.keys(schema.APP_FIELDS), set(cfg._APP_FIELDS))
        self.assertEqual(self.keys(schema.EDITOR_FIELDS), set(cfg._EDITOR_FIELDS))

    def test_enum_options_match_constants(self) -> None:
        by_key = {f["key"]: f for f in schema.PROJECT_FIELDS}
        values = lambda key: {o["value"] for o in by_key[key]["options"]} - {""}
        self.assertEqual(values("multiplexer"), set(cfg.MULTIPLEXERS))
        self.assertEqual(values("browser"), set(cfg.BROWSER_MODES))
        self.assertEqual(values("mode"), set(cfg.MODES))
        self.assertEqual(by_key["wait_timeout"]["max"], cfg.MAX_WAIT_TIMEOUT)
        self.assertEqual(by_key["wait_timeout"]["default"], cfg.DEFAULT_WAIT_TIMEOUT)

    def test_every_field_has_the_shape_the_form_needs(self) -> None:
        def check(fields: list[dict], where: str) -> None:
            for field in fields:
                here = f"{where}.{field['key']}"
                self.assertIn("kind", field, here)
                self.assertIn("label", field, here)
                if field["kind"] == "enum":
                    self.assertTrue(field["options"], here)
                if field["kind"] == "integer":
                    self.assertLess(field["min"], field["max"], here)
                if field["kind"] == "object":
                    check(field["fields"], here)
                if field["kind"] == "list":
                    self.assertIn(field["item"]["kind"], ("string", "object"), here)
                    if field["item"]["kind"] == "object":
                        check(field["item"]["fields"], here)

        check(schema.PROJECT_FIELDS, "project")

    def test_editor_presets_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for preset in schema.EDITOR_PRESETS:
                with self.subTest(preset=preset["label"]):
                    project, _ = forms.project_from_form({"name": "x", "path": tmp, "editor": preset["value"]})
                    self.assertIsNotNone(project)

    def test_describe_is_json_serialisable(self) -> None:
        json.dumps(schema.describe())


class NormaliseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_form_strings_become_typed_values(self) -> None:
        submitted = {
            "name": "demo",
            "path": self.dir,
            "session": "",
            "url": "",
            "browser": "",
            "wait_timeout": "45",
            "commands": [
                {"name": "web", "run": "npm run dev", "port": "3000", "cwd": ""},
                {"name": "", "run": "", "port": "", "cwd": ""},
            ],
            "editor": {"launch": "zed {path}", "match": ""},
            "apps": [{"name": "ld", "launch": "omarchy-launch-tui lazydocker", "match": "lazydocker"}],
            "stop_commands": ["docker compose down", ""],
        }
        project, warnings = forms.project_from_form(submitted)
        self.assertEqual(warnings, ())
        self.assertIsNone(project.session)
        self.assertIsNone(project.url)
        self.assertIsNone(project.browser)
        self.assertEqual(project.wait_timeout, 45)
        self.assertEqual(len(project.commands), 1)
        self.assertEqual(project.commands[0].port, 3000)
        self.assertIsNone(project.commands[0].cwd)
        self.assertEqual(project.effective_editor.launch, ("zed", "{path}"))
        self.assertIsNone(project.effective_editor.match)
        self.assertEqual(project.apps[0].launch, ("omarchy-launch-tui", "lazydocker"))
        self.assertEqual(project.stop_commands, ("docker compose down",))

    def test_empty_editor_means_default(self) -> None:
        project, _ = forms.project_from_form({"name": "demo", "path": self.dir, "editor": {"launch": "", "match": ""}})
        self.assertIsNone(project.editor)
        project, _ = forms.project_from_form({"name": "demo", "path": self.dir, "editor": None})
        self.assertIsNone(project.editor)

    def test_bad_number_and_bad_quote_name_the_field(self) -> None:
        with self.assertRaises(cfg.ConfigError) as ctx:
            forms.project_from_form({"name": "demo", "path": self.dir, "wait_timeout": "lots"})
        self.assertEqual(ctx.exception.where, "wait_timeout")
        with self.assertRaises(cfg.ConfigError) as ctx:
            forms.project_from_form({"name": "demo", "path": self.dir, "editor": {"launch": "zed 'unterminated"}})
        self.assertEqual(ctx.exception.where, "editor.launch")
        with self.assertRaises(cfg.ConfigError) as ctx:
            forms.project_from_form({"name": "demo", "path": self.dir, "commands": [{"name": "a", "run": "x", "port": "abc"}]})
        self.assertEqual(ctx.exception.where, "commands[0].port")

    def test_validator_rules_still_apply(self) -> None:
        with self.assertRaises(cfg.ConfigError) as ctx:
            forms.project_from_form({"name": "demo", "path": self.dir, "url": "not a url"})
        self.assertEqual(ctx.exception.where, "project 'demo'.url")
        with self.assertRaises(cfg.ConfigError):
            forms.project_from_form({"name": "", "path": self.dir})
        with self.assertRaises(cfg.ConfigError):
            forms.project_from_form({"name": "demo", "path": ""})
        with self.assertRaises(cfg.ConfigError):
            forms.project_from_form("not an object")

    def test_unknown_field_is_a_warning(self) -> None:
        _, warnings = forms.project_from_form({"name": "demo", "path": self.dir, "colour": "red"})
        self.assertEqual(warnings, ("project: unknown field 'colour' ignored",))

    def test_round_trip_through_show_shape(self) -> None:
        submitted = {"name": "demo", "path": self.dir, "commands": [{"name": "web", "run": "x", "port": 8080, "cwd": "client"}],
                     "editor": {"launch": ["zed", "{path}"], "match": "^dev"}, "apps": [], "mode": "parallel"}
        project, _ = forms.project_from_form(submitted)
        again, _ = forms.project_from_form(cfg.project_to_dict(project))
        self.assertEqual(again, project)


class ConfigEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.a = cfg.parse_project({"name": "a", "path": str(self.dir)})[0]
        self.b = cfg.parse_project({"name": "b", "path": str(self.dir)})[0]
        self.config = cfg.Config(projects=(self.a, self.b))

    def test_add_appends_and_rejects_duplicates(self) -> None:
        c = cfg.parse_project({"name": "c", "path": str(self.dir)})[0]
        self.assertEqual([p.name for p in cfg.with_project(self.config, c).projects], ["a", "b", "c"])
        with self.assertRaises(cfg.ConfigError):
            cfg.with_project(self.config, self.a)

    def test_edit_keeps_position_and_allows_rename(self) -> None:
        renamed = cfg.parse_project({"name": "a2", "path": str(self.dir), "url": "http://localhost:1"})[0]
        updated = cfg.with_project(self.config, renamed, replacing="a")
        self.assertEqual([p.name for p in updated.projects], ["a2", "b"])
        with self.assertRaises(cfg.ConfigError):
            cfg.with_project(self.config, cfg.parse_project({"name": "b", "path": str(self.dir)})[0], replacing="a")
        with self.assertRaises(cfg.ConfigError):
            cfg.with_project(self.config, renamed, replacing="zzz")

    def test_remove(self) -> None:
        self.assertEqual([p.name for p in cfg.without_project(self.config, "a").projects], ["b"])
        with self.assertRaises(cfg.ConfigError):
            cfg.without_project(self.config, "zzz")


class CliEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "projects.json"

    def run_cli(self, *argv: str) -> tuple[int, dict]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main([*argv, "--json", "--file", str(self.file)], out=out, err=err,
                        services_factory=FakeServices().build, logging_enabled=False)
        return code, json.loads(out.getvalue())

    def test_add_show_edit_remove_cycle(self) -> None:
        code, result = self.run_cli("add", json.dumps({"name": "demo", "path": str(self.dir), "commands": [{"name": "web", "run": "x", "port": "3000"}]}))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["project"]["commands"][0]["port"], 3000)
        self.assertTrue(self.file.exists())

        code, result = self.run_cli("show", "demo")
        self.assertEqual(code, 0)
        self.assertEqual(result["project"]["name"], "demo")

        code, result = self.run_cli("edit", "demo", json.dumps({"name": "demo2", "path": str(self.dir), "url": "http://localhost:3000"}))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["project"]["name"], "demo2")
        code, result = self.run_cli("list")
        self.assertEqual([p["name"] for p in result["projects"]], ["demo2"])

        code, result = self.run_cli("remove", "demo2")
        self.assertEqual(code, 0)
        code, result = self.run_cli("list")
        self.assertEqual(result["projects"], [])

    def test_add_reports_validation_errors_with_where(self) -> None:
        code, result = self.run_cli("add", json.dumps({"name": "demo", "path": str(self.dir / "missing")}))
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["where"], "project 'demo'.path")
        self.assertFalse(self.file.exists(), "nothing is written when validation fails")

    def test_add_duplicate_and_bad_json(self) -> None:
        self.run_cli("add", json.dumps({"name": "demo", "path": str(self.dir)}))
        code, result = self.run_cli("add", json.dumps({"name": "demo", "path": str(self.dir)}))
        self.assertEqual(code, 1)
        self.assertIn("already exists", result["error"])
        code, result = self.run_cli("add", "{not json")
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", result["error"])

    def test_edit_unknown_and_remove_unknown(self) -> None:
        code, result = self.run_cli("edit", "nope", json.dumps({"name": "x", "path": str(self.dir)}))
        self.assertEqual(code, 1)
        self.assertIn("unknown project", result["error"])
        code, result = self.run_cli("remove", "nope")
        self.assertEqual(code, 1)

    def test_schema_command(self) -> None:
        code, result = self.run_cli("schema")
        self.assertEqual(code, 0)
        self.assertEqual([f["key"] for f in result["fields"]][:2], ["name", "path"])


if __name__ == "__main__":
    unittest.main()

"""Tests for the CLI output contract."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from omadev_cli import cli
from tests.fakes import FakeServices


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "projects.json"

    def run_cli(self, *argv: str, services: FakeServices | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        factory = (services or FakeServices()).build
        code = cli.main(list(argv), out=out, err=err, services_factory=factory, logging_enabled=False)
        return code, out.getvalue(), err.getvalue()

    def test_start_dry_run_json(self) -> None:
        fake = FakeServices()
        fake.runner.on_json("herdr", "workspace", "list", result={"workspaces": []})
        self.write({"version": 1, "projects": [{"name": "demo", "path": str(self.dir), "url": "http://localhost:3000",
                                                "commands": [{"name": "app", "run": "npm run dev", "port": 3000}]}]})
        code, out, _ = self.run_cli("start", "demo", "--dry-run", "--json", "--file", str(self.file), services=fake)
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual({s["status"] for s in result["steps"]}, {"planned"})
        self.assertEqual(fake.runner.detached, [])

    def test_start_unknown_project(self) -> None:
        self.write({"version": 1, "projects": [{"name": "demo", "path": str(self.dir)}]})
        code, out, _ = self.run_cli("start", "nope", "--json", "--file", str(self.file))
        self.assertEqual(code, 1)
        self.assertIn("unknown project 'nope'", json.loads(out)["error"])

    def test_start_text_output_and_failure_exit(self) -> None:
        fake = FakeServices()
        fake.runner.tools.discard("herdr")
        self.write({"version": 1, "projects": [{"name": "demo", "path": str(self.dir)}]})
        code, out, err = self.run_cli("start", "demo", "--file", str(self.file), services=fake)
        self.assertEqual(code, 1)
        self.assertIn("failed", out)
        self.assertIn("herdr is not installed", out)
        self.assertIn("omadev: failed: workspace", err)

    def test_status_json(self) -> None:
        fake = FakeServices()
        fake.runner.on_json("herdr", "workspace", "list", result={"workspaces": []})
        self.write({"version": 1, "projects": [{"name": "demo", "path": str(self.dir)}]})
        code, out, _ = self.run_cli("status", "--json", "--file", str(self.file), services=fake)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["projects"][0]["workspace"]["present"], False)

    def write(self, data: object) -> None:
        self.file.write_text(json.dumps(data), encoding="utf-8")

    def test_list_json_without_file(self) -> None:
        code, out, err = self.run_cli("list", "--json", "--file", str(self.file))
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertTrue(result["ok"])
        self.assertFalse(result["exists"])
        self.assertEqual(result["projects"], [])
        self.assertEqual(err, "")

    def test_list_json_is_exactly_one_object(self) -> None:
        self.write({"version": 1, "projects": [{"name": "demo", "path": str(self.dir), "url": "http://localhost:3000"}]})
        code, out, _ = self.run_cli("list", "--json", "--file", str(self.file))
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1)
        project = json.loads(out)["projects"][0]
        self.assertEqual(project["name"], "demo")
        self.assertEqual(project["browser"], "webapp")

    def test_list_text(self) -> None:
        self.write({"version": 1, "projects": [{"name": "demo", "path": str(self.dir)}]})
        code, out, _ = self.run_cli("list", "--file", str(self.file))
        self.assertEqual(code, 0)
        self.assertIn("demo", out)
        self.assertIn("herdr", out)

    def test_config_error_is_json_with_ok_false(self) -> None:
        self.write({"version": 1, "projects": [{"name": "demo"}]})
        code, out, err = self.run_cli("list", "--json", "--file", str(self.file))
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertFalse(result["ok"])
        self.assertEqual(result["where"], "projects[0]")
        self.assertEqual(err, "")

    def test_config_error_text_goes_to_stderr(self) -> None:
        self.write({"version": 1, "projects": [{"name": "demo"}]})
        code, out, err = self.run_cli("list", "--file", str(self.file))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("projects[0]", err)

    def test_validate_checks_paths(self) -> None:
        self.write({"version": 1, "projects": [{"name": "demo", "path": str(self.dir / "missing")}]})
        code, _, _ = self.run_cli("list", "--file", str(self.file))
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("validate", "--json", "--file", str(self.file))
        self.assertEqual(code, 1)
        self.assertIn("does not exist", json.loads(out)["error"])

    def test_warnings_reach_stderr_in_text_mode(self) -> None:
        self.write({"version": 1, "projects": [{"name": "demo", "path": str(self.dir), "colour": "red"}]})
        code, _, err = self.run_cli("validate", "--file", str(self.file))
        self.assertEqual(code, 0)
        self.assertIn("unknown field 'colour'", err)

    def test_usage_error_exit_code(self) -> None:
        # argparse writes its usage message straight to sys.stderr; keep it
        # out of the test run's output.
        with contextlib.redirect_stderr(io.StringIO()):
            code, _, _ = self.run_cli("bogus")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

"""End-to-end CLI checks, including first-import read-only behavior."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
CLI = KIT / "agent_docs_kit.py"


class LivingCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="living cli ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src/main.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "README.md").write_text("# Existing documentation\n", encoding="utf-8")
        self.reference = self.base / "evidence.json"

    def run_cli(self, *args: str, cli: Path = CLI) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        return subprocess.run(
            [sys.executable, str(cli), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
            env=env,
        )

    def capture(self) -> subprocess.CompletedProcess[str]:
        result = self.run_cli(
            "snapshot",
            str(self.root),
            "--source",
            "src",
            "--document",
            "README.md",
            "--output",
            str(self.reference),
            "--apply",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def get_status(self) -> subprocess.CompletedProcess[str]:
        return self.run_cli("status", str(self.root), "--snapshot", str(self.reference), "--json")

    def test_version_and_help_advertise_living_commands(self) -> None:
        version = self.run_cli("--version")
        self.assertEqual(version.returncode, 0)
        self.assertIn("0.5.0-rc.1", version.stdout)
        help_result = self.run_cli("--help")
        self.assertIn("snapshot", help_result.stdout)
        self.assertIn("status", help_result.stdout)

    def test_snapshot_requires_apply_and_does_not_write_on_usage_error(self) -> None:
        result = self.run_cli(
            "snapshot",
            str(self.root),
            "--source",
            "src",
            "--document",
            "README.md",
            "--output",
            str(self.reference),
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.reference.exists())

    def test_argument_error_keeps_argparse_contract(self) -> None:
        result = self.run_cli("status", str(self.root), "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--snapshot", result.stderr)

    def test_snapshot_and_unchanged_status_do_not_attest_semantics(self) -> None:
        captured = json.loads(self.capture().stdout)
        self.assertEqual(captured["semantic_review"], "not_attested")
        before = self.reference.read_bytes()
        result = self.get_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["state"], "unchanged_since_snapshot")
        self.assertEqual(report["semantic_review"], "not_attested")
        self.assertEqual(before, self.reference.read_bytes())

    def test_existing_snapshot_is_not_overwritten(self) -> None:
        self.capture()
        before = self.reference.read_bytes()
        result = self.run_cli(
            "snapshot",
            str(self.root),
            "--source",
            "src",
            "--document",
            "README.md",
            "--output",
            str(self.reference),
            "--apply",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(before, self.reference.read_bytes())

    def test_dirty_source_changes_are_machine_readable(self) -> None:
        self.capture()
        (self.root / "src/main.py").write_text("VALUE = 2\n", encoding="utf-8")
        (self.root / "src/new.py").write_text("NEW = True\n", encoding="utf-8")
        result = self.get_status()
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["state"], "review_required")
        self.assertIn("src/main.py", report["sources"]["modified"])
        self.assertIn("src/new.py", report["sources"]["added"])

    def test_missing_document_requires_review(self) -> None:
        self.capture()
        (self.root / "README.md").unlink()
        result = self.get_status()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("README.md", json.loads(result.stdout)["documents"]["deleted"])

    def test_invalid_snapshot_is_json_error_not_clean(self) -> None:
        self.reference.write_text("{invalid", encoding="utf-8")
        result = self.get_status()
        self.assertEqual(result.returncode, 3)
        report = json.loads(result.stdout)
        self.assertEqual(report["state"], "error")
        self.assertEqual(report["semantic_review"], "not_attested")
        self.assertTrue(report["error"])

    def test_missing_snapshot_is_json_error(self) -> None:
        result = self.get_status()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["state"], "error")

    def test_unknown_schema_is_json_error(self) -> None:
        self.capture()
        payload = json.loads(self.reference.read_text())
        payload["schema_version"] = 999
        self.reference.write_text(json.dumps(payload), encoding="utf-8")
        result = self.get_status()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["state"], "error")

    def test_first_status_import_does_not_write_bytecode_into_inspected_kit(self) -> None:
        copied = self.base / "kit"
        shutil.copytree(KIT, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copied_cli = copied / "agent_docs_kit.py"
        result = self.run_cli(
            "snapshot",
            str(copied),
            "--source",
            ".",
            "--document",
            "README.md",
            "--output",
            str(self.reference),
            "--apply",
            cli=copied_cli,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((copied / "__pycache__").exists())

        def complete_inventory() -> dict[str, str]:
            return {
                p.relative_to(copied).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                if p.is_file()
                else "directory"
                for p in copied.rglob("*")
            }

        before = complete_inventory()
        result = self.run_cli(
            "status",
            str(copied),
            "--snapshot",
            str(self.reference),
            "--json",
            cli=copied_cli,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "unchanged_since_snapshot")
        self.assertEqual(complete_inventory(), before)
        self.assertFalse((copied / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()

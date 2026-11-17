"""Regressions found by independent review, exercised through the public API."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import agent_docs_kit as kit


class ProductionRegressions(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="kit production ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        for relative in kit.DOCUMENTS:
            p = self.root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# Document\n", encoding="utf-8")

    def test_paragraph_continuation_cannot_hide_a_broken_link(self) -> None:
        (self.root / "AGENTS.md").write_text(
            "# Entry\n\nSee evidence\n    [missing](missing.md)\n", encoding="utf-8"
        )
        self.assertTrue(any("missing.md" in item for item in kit.check(self.root)))

    def test_list_continuation_cannot_hide_a_broken_link(self) -> None:
        (self.root / "AGENTS.md").write_text("- Evidence\n    [missing](missing.md)\n", encoding="utf-8")
        self.assertTrue(any("missing.md" in item for item in kit.check(self.root)))

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_relative_path_executable_survives_output_cwd_change(self) -> None:
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        executable = bin_dir / "graphify"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json, pathlib, sys\n"
            "if '--version' in sys.argv:\n"
            " print('graphify 0.9.26')\n"
            "else:\n"
            " p=pathlib.Path('graphify-out/graph.json')\n"
            " p.parent.mkdir()\n"
            " p.write_text(json.dumps({'nodes':[{'id':'one'}], 'edges':[]}))\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        before = Path.cwd()
        try:
            os.chdir(self.base)
            with patch.dict(os.environ, {"PATH": "./bin"}):
                artifact = kit.graph(self.root, self.base / "graph")
            self.assertEqual(json.loads(artifact.read_text())["nodes"], [{"id": "one"}])
        finally:
            os.chdir(before)

    def test_version_timeout_is_a_cli_error_without_output(self) -> None:
        output = self.base / "graph"
        with (
            patch.object(kit.shutil, "which", return_value="/graphify"),
            patch.object(kit.subprocess, "run", side_effect=subprocess.TimeoutExpired(["graphify"], 10)),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = kit.main(["graph", str(self.root), "--output", str(output), "--apply"])
        self.assertEqual(code, 3)
        self.assertFalse(output.exists())

    def test_extraction_timeout_preserves_failure_and_partial_log(self) -> None:
        output = self.base / "graph"
        responses = [
            subprocess.CompletedProcess(["graphify", "--version"], 0, "graphify 0.9.26\n", ""),
            subprocess.TimeoutExpired(
                ["graphify", "extract"], 0.1, output=b"partial output", stderr=b"warning"
            ),
        ]
        with (
            patch.object(kit.shutil, "which", return_value="/graphify"),
            patch.object(kit.subprocess, "run", side_effect=responses),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = kit.main(["graph", str(self.root), "--output", str(output), "--apply"])
        self.assertEqual(code, 3)
        record = json.loads((output / "run.json").read_text())
        self.assertEqual(record["status"], "failed")
        self.assertIn("partial output", (output / "graphify.log").read_text())

    def test_invalid_timeout_is_rejected_before_process_or_write(self) -> None:
        with patch.object(kit.subprocess, "run") as runner:
            for timeout in (0, -1, float("nan"), float("inf")):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    kit.graph(self.root, self.base / "graph", timeout=timeout)
        runner.assert_not_called()
        self.assertFalse((self.base / "graph").exists())


if __name__ == "__main__":
    unittest.main()

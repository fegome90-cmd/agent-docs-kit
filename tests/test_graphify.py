"""Public Graphify integration contract; no external executable is needed."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import agent_docs_kit as kit


@dataclass
class FakeCompletedProcess:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class GraphifyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="graph contract ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "source with spaces"
        self.root.mkdir()
        (self.root / "example.py").write_bytes(b"def hello():\n    return 42\n")
        (self.root / "AGENTS.md").write_bytes(b"Owner instructions\r\n")
        self.output = self.base / "new parent" / "graph result"
        self.executable = "/external tools/graphify"
        self.version = FakeCompletedProcess(stdout="graphify 0.9.26\n")
        self.result = FakeCompletedProcess(stdout="extract stdout\n", stderr="extract stderr\n")
        self.graph_bytes: bytes | None = b'{"nodes": [{"id": "example"}], "edges": []}'
        self.calls: list[list[str]] = []
        which = patch("agent_docs_kit.shutil.which", return_value=self.executable)
        self.which = which.start()
        self.addCleanup(which.stop)
        runner = patch("agent_docs_kit.subprocess.run", side_effect=self.run_process)
        self.runner = runner.start()
        self.addCleanup(runner.stop)

    def run_process(
        self, argv: list[str], *, capture_output: bool, text: bool,
        check: bool, env: dict[str, str], cwd: Path | None = None, timeout: float,
    ) -> FakeCompletedProcess:
        self.calls.append(argv)
        self.assertTrue(capture_output)
        self.assertTrue(text)
        self.assertFalse(check)
        self.assertGreater(timeout, 0)
        self.assertEqual(env["GRAPHIFY_OUT"], "graphify-out")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(
            {key for key in env if key.startswith("GRAPHIFY_")}, {"GRAPHIFY_OUT"}
        )
        if argv == [self.executable, "--version"]:
            self.assertFalse(self.output.exists())
            return self.version
        self.assertEqual(cwd, self.output)
        self.assertTrue(self.output.is_dir())
        if self.graph_bytes is not None:
            artifact = self.output / "graphify-out" / "graph.json"
            artifact.parent.mkdir()
            artifact.write_bytes(self.graph_bytes)
        return self.result

    def cli(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                status = kit.main(list(arguments))
            except SystemExit as exc:
                if not isinstance(exc.code, int):
                    self.fail(f"Expected integer exit status, got {exc.code!r}")
                status = exc.code
        return status, stdout.getvalue(), stderr.getvalue()

    def graph_cli(self, *arguments: str) -> tuple[int, str, str]:
        return self.cli(
            "graph", str(self.root), "--output", str(self.output), "--apply", *arguments
        )

    def snapshot(self) -> dict[str, bytes | str | None]:
        return {
            str(path.relative_to(self.base)): (
                str(path.readlink()) if path.is_symlink()
                else None if path.is_dir() else path.read_bytes()
            )
            for path in self.base.rglob("*")
        }

    def assert_record(self, status: str) -> None:
        metadata = json.loads((self.output / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["status"], status)
        self.assertEqual(metadata["root"], str(self.root))
        self.assertIn("0.9.26", metadata["version"])
        self.assertEqual(metadata["command"], self.calls[-1])
        log = (self.output / "graphify.log").read_text(encoding="utf-8")
        self.assertIn(self.result.stdout, log)
        self.assertIn(self.result.stderr, log)

    def test_graph_api_returns_artifact_and_exact_safe_argv(self) -> None:
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        excludes = ("tests/**", "name with spaces; touch unwanted")
        artifact = kit.graph(self.root, self.output, excludes)
        self.assertEqual(artifact, self.output / "graphify-out" / "graph.json")
        self.assertEqual(artifact.read_bytes(), self.graph_bytes)
        self.which.assert_called_once_with("graphify")
        self.assertEqual(self.calls, [
            [self.executable, "--version"],
            [self.executable, "extract", str(self.root), "--code-only", "--no-cluster",
             "--max-workers", "1", "--out", str(self.output),
             "--exclude", excludes[0], "--exclude", excludes[1]],
        ])
        self.assert_record("complete")
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_cli_success_reports_graph_path_and_repeated_excludes(self) -> None:
        status, stdout, stderr = self.graph_cli("--exclude", "*.md", "--exclude", "build/**")
        self.assertEqual(status, 0)
        self.assertIn(str(self.output / "graphify-out" / "graph.json"), stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(self.calls[-1][-4:], ["--exclude", "*.md", "--exclude", "build/**"])
        self.assert_record("complete")

    def test_environment_is_clean_copy_without_mutating_process(self) -> None:
        with patch.dict(os.environ, {
            "GRAPHIFY_OUT": "/unwanted", "GRAPHIFY_API_KEY": "secret",
            "GRAPHIFY_LLM": "enabled", "PYTHONDONTWRITEBYTECODE": "0",
            "KIT_SENTINEL": "preserved",
        }):
            before = dict(os.environ)
            kit.graph(self.root, self.output)
            self.assertEqual(dict(os.environ), before)
            for call in self.runner.call_args_list:
                environment = call.kwargs["env"]
                self.assertIsNot(environment, os.environ)
                self.assertEqual(environment["KIT_SENTINEL"], "preserved")
                self.assertNotIn("GRAPHIFY_API_KEY", environment)

    def test_cli_requires_output_and_apply_and_rejects_arbitrary_flags(self) -> None:
        for arguments in (
            ("graph", str(self.root)),
            ("graph", str(self.root), "--apply"),
            ("graph", str(self.root), "--output", str(self.output)),
            ("graph", str(self.root), "--output", str(self.output), "--apply", "--llm"),
            ("graph", str(self.root), "--output", str(self.output), "--apply", "--watch"),
        ):
            with self.subTest(arguments=arguments):
                before = self.snapshot()
                self.assertEqual(self.cli(*arguments)[0], 2)
                self.assertEqual(self.snapshot(), before)
        self.runner.assert_not_called()
        self.which.assert_not_called()

    def test_invalid_root_and_nonexternal_output_never_write_or_run(self) -> None:
        file = self.base / "file"
        file.write_bytes(b"preserve")
        cases = (
            (self.base / "missing", self.output), (file, self.output),
            (self.root, self.root), (self.root, self.root / "nested"),
            (self.root, self.base),
        )
        for source, output in cases:
            with self.subTest(source=source, output=output):
                before = self.snapshot()
                status, _, _ = self.cli(
                    "graph", str(source), "--output", str(output), "--apply"
                )
                self.assertEqual(status, 3)
                self.assertEqual(self.snapshot(), before)
        self.runner.assert_not_called()

    def test_case_alias_output_cannot_write_inside_root(self) -> None:
        alias = self.root.with_name(self.root.name.upper())
        if not alias.exists() or not alias.samefile(self.root):
            self.skipTest("Filesystem does not provide a case alias")
        for output in (alias, alias / "nested"):
            with self.subTest(output=output):
                before = self.snapshot()
                self.assertEqual(self.cli(
                    "graph", str(self.root), "--output", str(output), "--apply"
                )[0], 3)
                self.assertEqual(self.snapshot(), before)
        self.runner.assert_not_called()

    def test_symlink_roots_outputs_and_output_parents_are_rejected(self) -> None:
        alias = self.base / "linked root"
        alias.symlink_to(self.root, target_is_directory=True)
        outside = self.base / "outside"
        outside.mkdir()
        link = self.base / "linked output"
        link.symlink_to(outside, target_is_directory=True)
        broken = self.base / "broken output"
        broken.symlink_to(self.base / "missing")
        for source, output in (
            (alias, self.output), (alias / ".." / self.root.name, self.output),
            (self.root, link), (self.root, link / "nested"), (self.root, broken),
        ):
            with self.subTest(source=source, output=output):
                before = self.snapshot()
                self.assertEqual(self.cli(
                    "graph", str(source), "--output", str(output), "--apply"
                )[0], 3)
                self.assertEqual(self.snapshot(), before)
        self.runner.assert_not_called()

    def test_existing_output_file_or_directory_is_conflict(self) -> None:
        for kind in ("file", "directory"):
            with self.subTest(kind=kind):
                self.output.parent.mkdir(parents=True, exist_ok=True)
                if kind == "file":
                    self.output.write_bytes(b"keep bytes\r\n")
                else:
                    self.output.mkdir()
                    (self.output / "sentinel").write_bytes(b"keep")
                before = self.snapshot()
                with self.assertRaises(FileExistsError):
                    kit.graph(self.root, self.output)
                self.assertEqual(self.graph_cli()[0], 1)
                self.assertEqual(self.snapshot(), before)
                if kind == "file":
                    self.output.unlink()
        self.runner.assert_not_called()

    def test_missing_executable_or_unsupported_version_does_not_create_parents(self) -> None:
        self.which.return_value = None
        before = self.snapshot()
        self.assertEqual(self.graph_cli()[0], 3)
        self.assertEqual(self.snapshot(), before)
        self.runner.assert_not_called()
        self.which.return_value = self.executable
        for version in (
            FakeCompletedProcess(stdout="graphify 0.9.25\n"),
            FakeCompletedProcess(stdout="graphify 0.9.260\n"),
            FakeCompletedProcess(1, "graphify 0.9.26\n", "version failed"),
        ):
            with self.subTest(version=version):
                self.version = version
                self.assertEqual(self.graph_cli()[0], 3)
                self.assertEqual(self.snapshot(), before)
        self.assertTrue(all(argv == [self.executable, "--version"] for argv in self.calls))

    def test_output_permission_error_is_io_failure_without_source_changes(self) -> None:
        before = self.snapshot()
        with patch.object(Path, "mkdir", side_effect=PermissionError("permission denied")):
            status, _, stderr = self.graph_cli()
        self.assertEqual(status, 3)
        self.assertIn("permission denied", stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.calls, [[self.executable, "--version"]])

    def test_nonzero_tool_exit_keeps_failed_record_and_output_logs(self) -> None:
        self.result = FakeCompletedProcess(7, "partial stdout", "extraction failed stderr")
        self.graph_bytes = None
        status, _, stderr = self.graph_cli()
        self.assertEqual(status, 3)
        self.assertTrue(stderr)
        self.assert_record("failed")
        self.assertTrue(self.output.is_dir())

    def test_missing_or_malformed_graph_keeps_failed_record_and_logs(self) -> None:
        for index, payload in enumerate((
            None, b"not json", b"[]", b"{}", b'{"nodes": [], "edges": []}',
            b'{"nodes": {}, "edges": []}', b'{"nodes": [{}], "edges": {}}',
            b'{"nodes": [{}]}',
        )):
            with self.subTest(payload=payload):
                self.output = self.base / f"invalid graph {index}"
                self.graph_bytes = payload
                status, _, stderr = self.graph_cli()
                self.assertEqual(status, 3)
                self.assertTrue(stderr)
                self.assert_record("failed")


if __name__ == "__main__":
    unittest.main()

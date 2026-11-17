"""Behavioral tests for the portable, standard-library documentation kit."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import IO, Any
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import agent_docs_kit as kit


class KitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="agent docs kit ")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "repository with spaces"
        self.root.mkdir()

    def cli(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                status = kit.main(list(arguments))
            except SystemExit as exc:
                if not isinstance(exc.code, int):
                    self.fail(f"Expected integer exit status, got {exc.code!r}")
                status = exc.code
        self.assertIsInstance(status, int)
        return status, stdout.getvalue(), stderr.getvalue()

    def snapshot(self, root: Path) -> dict[str, bytes | str | None]:
        return {
            str(path.relative_to(root)): (
                str(path.readlink()) if path.is_symlink()
                else None if path.is_dir() else path.read_bytes()
            )
            for path in root.rglob("*")
        }

    def complete_documents(self) -> None:
        for relative in kit.DOCUMENTS:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"# Authored document\n\nSupported by local evidence.\n")

    def test_inspect_is_read_only_even_with_authority_in_another_path(self) -> None:
        authority = self.root / "design.md"
        authority.write_bytes(b"Existing authority\n")
        before = self.snapshot(self.base)
        self.assertEqual(kit.inspect(self.root), ())
        status, stdout, _ = self.cli("inspect", str(self.root))
        self.assertEqual(status, 0)
        self.assertIn("autoridades", stdout)
        self.assertEqual(self.snapshot(self.base), before)

    def test_cli_requires_explicit_write_and_authority_review(self) -> None:
        for flags in ((), ("--authorities-reviewed",), ("--apply",)):
            with self.subTest(flags=flags):
                status, _, _ = self.cli("prepare", str(self.root), *flags)
                self.assertEqual(status, 2)
                self.assertEqual(self.snapshot(self.root), {})
        output = self.base / "proposal"
        status, _, _ = self.cli("prepare", str(self.root), "--output", str(output))
        self.assertEqual(status, 2)
        self.assertFalse(output.exists())

    def test_prepare_writes_exact_templates_and_drafts_fail_check(self) -> None:
        status, _, _ = self.cli(
            "prepare", str(self.root), "--apply", "--authorities-reviewed"
        )
        self.assertEqual(status, 0)
        self.assertEqual(
            {p for p in self.root.rglob("*") if p.is_file()},
            {self.root / relative for relative in kit.DOCUMENTS},
        )
        for relative in kit.DOCUMENTS:
            data = (self.root / relative).read_bytes()
            self.assertEqual(data, (KIT / "templates" / relative).read_bytes())
            self.assertNotIn(b"\r", data)
            self.assertIn(kit.DRAFT_MARKER, data.decode("utf-8"))
        self.assertEqual(self.cli("check", str(self.root))[0], 1)

    def test_any_occupied_destination_blocks_whole_prepare(self) -> None:
        for relative in kit.DOCUMENTS:
            with (
                self.subTest(relative=relative),
                tempfile.TemporaryDirectory(dir=self.base) as temporary,
            ):
                root = Path(temporary).resolve()
                occupied = root / relative
                occupied.parent.mkdir(parents=True, exist_ok=True)
                occupied.write_bytes(b"Owner-authored bytes\r\n")
                before = self.snapshot(root)
                self.assertTrue(kit.inspect(root))
                with self.assertRaises(FileExistsError):
                    kit.prepare(root)
                self.assertEqual(self.snapshot(root), before)

    def test_repeat_preserves_all_existing_bytes(self) -> None:
        kit.prepare(self.root)
        before = self.snapshot(self.root)
        self.assertEqual(self.cli("inspect", str(self.root))[0], 1)
        self.assertEqual(self.cli(
            "prepare", str(self.root), "--apply", "--authorities-reviewed"
        )[0], 1)
        self.assertEqual(self.snapshot(self.root), before)

    def test_external_proposal_preserves_source_and_blocks_repeat(self) -> None:
        (self.root / "AGENTS.md").write_bytes(b"Existing instructions\n")
        before = self.snapshot(self.root)
        output = self.base / "new proposal" / "drafts"
        status, _, _ = self.cli(
            "prepare", str(self.root), "--output", str(output), "--apply"
        )
        self.assertEqual(status, 0)
        self.assertEqual(self.snapshot(self.root), before)
        proposal = self.snapshot(output)
        self.assertEqual(self.cli(
            "prepare", str(self.root), "--output", str(output), "--apply"
        )[0], 1)
        self.assertEqual(self.snapshot(output), proposal)
        self.assertEqual(self.snapshot(self.root), before)

    def test_output_must_be_external_and_not_contain_source(self) -> None:
        for output in (self.root, self.root / "proposal", self.base):
            with self.subTest(output=output):
                before = self.snapshot(self.base)
                with self.assertRaises(ValueError):
                    kit.prepare(self.root, output)
                self.assertEqual(self.snapshot(self.base), before)

    def test_case_alias_output_cannot_activate_inside_source(self) -> None:
        alias = self.root.with_name(self.root.name.upper())
        if not alias.exists() or not alias.samefile(self.root):
            self.skipTest("Filesystem does not provide a case alias")
        before = self.snapshot(self.base)
        for output in (alias, alias / "proposal"):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    kit.prepare(self.root, output)
                status, _, _ = self.cli(
                    "prepare", str(self.root), "--output", str(output), "--apply"
                )
                self.assertEqual(status, 3)
                self.assertEqual(self.snapshot(self.base), before)

    def test_existing_external_directory_retains_unrelated_files(self) -> None:
        output = self.base / "proposal"
        output.mkdir()
        sentinel = output / "notes.txt"
        sentinel.write_bytes(b"Keep this\n")
        kit.prepare(self.root, output)
        self.assertEqual(sentinel.read_bytes(), b"Keep this\n")
        self.assertEqual(self.snapshot(self.root), {})

    def test_non_regular_destinations_and_parent_file_block_writes(self) -> None:
        target = self.root / "AGENTS.md"
        target.mkdir()
        before = self.snapshot(self.root)
        with self.assertRaises(FileExistsError):
            kit.prepare(self.root)
        self.assertEqual(self.snapshot(self.root), before)
        target.rmdir()
        (self.root / "docs").write_bytes(b"Not a directory")
        before = self.snapshot(self.root)
        with self.assertRaises(FileExistsError):
            kit.prepare(self.root)
        self.assertEqual(self.snapshot(self.root), before)

    def test_root_and_output_must_be_directories(self) -> None:
        missing = self.base / "missing"
        file = self.base / "file"
        file.write_bytes(b"keep")
        for bad_root in (missing, file):
            with self.subTest(root=bad_root):
                self.assertEqual(self.cli("inspect", str(bad_root))[0], 3)
                self.assertEqual(self.cli("check", str(bad_root))[0], 3)
        with self.assertRaises(ValueError):
            kit.prepare(self.root, file)
        self.assertEqual(file.read_bytes(), b"keep")

    def test_symlink_root_and_lexical_symlink_before_dotdot_are_rejected(self) -> None:
        link = self.base / "linked repository"
        link.symlink_to(self.root, target_is_directory=True)
        for root in (link, link / ".." / self.root.name):
            with self.subTest(root=root):
                for command in ("inspect", "check"):
                    self.assertEqual(self.cli(command, str(root))[0], 3)
                with self.assertRaises(ValueError):
                    kit.prepare(root)
        self.assertEqual(self.snapshot(self.root), {})

    def test_symlink_targets_broken_targets_and_parents_are_conflicts(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "instructions.md"
        sentinel.write_bytes(b"Do not change\n")
        target = self.root / "AGENTS.md"
        for destination in (sentinel, outside / "missing"):
            with self.subTest(destination=destination):
                target.symlink_to(destination)
                with self.assertRaises(FileExistsError):
                    kit.prepare(self.root)
                self.assertTrue(target.is_symlink())
                self.assertEqual(sentinel.read_bytes(), b"Do not change\n")
                target.unlink()
        (self.root / "docs").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            kit.prepare(self.root)
        self.assertEqual({p.name for p in outside.iterdir()}, {"instructions.md"})
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_symlink_external_output_parent_is_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        link = self.base / "proposal link"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            kit.prepare(self.root, link / "drafts")
        self.assertEqual(self.snapshot(outside), {})

    def test_failure_after_first_file_rolls_back_new_files_and_directories(self) -> None:
        output = self.base / "new" / "proposal"
        original_open = Path.open
        creations = 0

        def fail_second_creation(
            path: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> IO[Any]:
            nonlocal creations
            if mode == "xb":
                creations += 1
                if creations == 2:
                    raise OSError("simulated write failure")
            return original_open(path, mode, buffering, encoding, errors, newline)

        with patch.object(Path, "open", fail_second_creation):
            status, _, stderr = self.cli(
                "prepare", str(self.root), "--output", str(output), "--apply"
            )
        self.assertEqual(status, 3)
        self.assertIn("simulated write failure", stderr)
        self.assertEqual(creations, 2)
        self.assertFalse((self.base / "new").exists())
        self.assertEqual(self.snapshot(self.root), {})

    def test_rollback_keeps_preexisting_directories_and_files(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "notes.txt").write_bytes(b"Keep\n")
        before = self.snapshot(self.root)
        original_open = Path.open
        creations = 0

        def fail_second_creation(
            path: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> IO[Any]:
            nonlocal creations
            if mode == "xb":
                creations += 1
                if creations == 2:
                    raise OSError("simulated write failure")
            return original_open(path, mode, buffering, encoding, errors, newline)

        with patch.object(Path, "open", fail_second_creation), self.assertRaises(OSError):
            kit.prepare(self.root)
        self.assertEqual(creations, 2)
        self.assertEqual(self.snapshot(self.root), before)

    def test_complete_documents_pass_read_only_check(self) -> None:
        self.complete_documents()
        before = self.snapshot(self.root)
        self.assertEqual(kit.check(self.root), ())
        status, stdout, _ = self.cli("check", str(self.root))
        self.assertEqual(status, 0)
        self.assertIn("estructural", stdout)
        self.assertIn("anclas", stdout)
        self.assertEqual(self.snapshot(self.root), before)

    def test_check_propagates_read_permission_error_and_cli_reports_io_failure(self) -> None:
        self.complete_documents()
        before = self.snapshot(self.root)
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                kit.check(self.root)
            status, _, stderr = self.cli("check", str(self.root))
        self.assertEqual(status, 3)
        self.assertTrue(stderr)
        self.assertEqual(self.snapshot(self.root), before)

    def test_check_reports_absent_empty_encoding_newline_and_draft_problems(self) -> None:
        self.assertEqual(len(kit.check(self.root)), 5)
        self.complete_documents()
        cases = (
            (b"", "vacío"),
            (b"\xff", "leer"),
            (b"# Content\r\n", "LF"),
            (kit.DRAFT_MARKER.encode(), "borrador"),
            (b"# Content\n{{OPEN_QUESTION}}\n", "borrador"),
        )
        target = self.root / "AGENTS.md"
        for data, expected in cases:
            with self.subTest(data=data):
                target.write_bytes(data)
                before = self.snapshot(self.root)
                self.assertTrue(any(expected in finding for finding in kit.check(self.root)))
                self.assertEqual(self.snapshot(self.root), before)

    def test_check_rejects_symlink_document_without_reading_target(self) -> None:
        self.complete_documents()
        outside = self.base / "outside.md"
        outside.write_bytes(b"External bytes\n")
        target = self.root / "AGENTS.md"
        target.unlink()
        target.symlink_to(outside)
        findings = kit.check(self.root)
        self.assertEqual(len(findings), 1)
        self.assertIn("AGENTS.md", findings[0])
        self.assertEqual(outside.read_bytes(), b"External bytes\n")

    def test_check_relative_links_spaces_percent_encoding_and_fragments(self) -> None:
        self.complete_documents()
        (self.root / "a file.md").write_bytes(b"# Local\n")
        (self.root / "AGENTS.md").write_text(
            "# Links\n[context](docs/agent/CONTEXT.md#unknown-anchor)\n"
            "[space](<a file.md>)\n[encoded](a%20file.md)\n"
            "[fragment](#unverified)\n[web](https://example.invalid/no-network)\n"
            "[email](mailto:someone@example.invalid)\n", encoding="utf-8"
        )
        (self.root / "docs/agent/CONTEXT.md").write_text(
            "# Context\n[entry](../../AGENTS.md)\n", encoding="utf-8"
        )
        self.assertEqual(kit.check(self.root), ())

    def test_check_reports_missing_absolute_and_escaping_links(self) -> None:
        self.complete_documents()
        outside = self.base / "outside.md"
        outside.write_bytes(b"External\n")
        cases = (
            ("absent.md", "ausente"),
            (str(outside), "absoluto"),
            ("../outside.md", "fuera"),
            ("%2e%2e/outside.md", "fuera"),
        )
        for link, expected in cases:
            with self.subTest(link=link):
                (self.root / "AGENTS.md").write_text(
                    f"# Links\n[local](<{link}>)\n", encoding="utf-8"
                )
                findings = kit.check(self.root)
                self.assertEqual(len(findings), 1)
                self.assertIn(expected, findings[0])

    def test_check_rejects_symlink_link_targets_and_parent_components(self) -> None:
        self.complete_documents()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "file.md").write_bytes(b"External\n")
        (self.root / "link.md").symlink_to(outside / "file.md")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        for link in ("link.md", "linked/file.md", "linked/../AGENTS.md"):
            with self.subTest(link=link):
                (self.root / "AGENTS.md").write_text(
                    f"# Links\n[local]({link})\n", encoding="utf-8"
                )
                self.assertEqual(len(kit.check(self.root)), 1)

    def test_code_examples_and_manual_link_forms_are_not_checked(self) -> None:
        self.complete_documents()
        (self.root / "AGENTS.md").write_text(
            "# Examples\n`[inline](absent.md)`\n"
            "````markdown\n[example](absent.md)\n```\n"
            "[still code](absent.md)\n````\n"
            "~~~markdown\n[example](absent.md)\n~~~\n"
            "[reference][manual]\n[manual]: absent.md\n", encoding="utf-8"
        )
        self.assertEqual(kit.check(self.root), ())

    def test_missing_parent_dotdot_cannot_hide_symlink_roots_or_links(self) -> None:
        self.complete_documents()
        outside = self.base / "outside.md"
        outside.write_bytes(b"Outside\n")
        (self.root / "linked.md").symlink_to(outside)
        (self.root / "AGENTS.md").write_text(
            "# Links\n[external](missing/../linked.md)\n", encoding="utf-8"
        )
        before = self.snapshot(self.base)
        self.assertEqual(len(kit.check(self.root)), 1)
        self.assertEqual(self.cli("check", str(self.root))[0], 1)
        alias = self.base / "linked-root"
        alias.symlink_to(self.root, target_is_directory=True)
        disguised = self.base / "missing" / ".." / "linked-root"
        output = self.base / "proposal"
        for command in ("inspect", "check"):
            self.assertEqual(self.cli(command, str(disguised))[0], 3)
        self.assertEqual(self.cli(
            "prepare", str(disguised), "--output", str(output), "--apply"
        )[0], 3)
        self.assertFalse(output.exists())
        self.assertEqual(self.snapshot(self.base), before | {"linked-root": str(self.root)})

    def test_check_requires_regular_file_link_targets(self) -> None:
        self.complete_documents()
        entry = self.root / "AGENTS.md"
        before = entry.read_bytes()
        (self.root / "AGENTS.md").write_text(
            "# Links\n[directory](docs/agent)\n", encoding="utf-8"
        )
        self.assertEqual(len(kit.check(self.root)), 1)
        self.assertEqual(self.cli("check", str(self.root))[0], 1)
        entry.write_bytes(before)
        self.assertEqual(kit.check(self.root), ())

    def test_cli_help_and_argument_errors(self) -> None:
        self.assertEqual(self.cli("--help")[0], 0)
        self.assertEqual(self.cli()[0], 2)
        self.assertEqual(self.cli("unknown", str(self.root))[0], 2)
        self.assertEqual(self.cli("inspect")[0], 2)


if __name__ == "__main__":
    unittest.main()

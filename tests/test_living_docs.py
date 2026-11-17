"""Behavioral acceptance tests for byte snapshots, without semantic approval."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import living_docs as living


class LivingDocsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="living docs ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repository with spaces"
        self.root.mkdir()
        (self.root / "src/empty").mkdir(parents=True)
        (self.root / "src/main.py").write_bytes(b"value = 1\n")
        (self.root / "src/remove.py").write_bytes(b"retired = False\n")
        (self.root / "README.md").write_bytes(b"# Draft\n<!-- agent-docs-kit:draft -->\n")
        self.output = self.base / "snapshot.json"

    def capture(self, **kwargs: object) -> dict:
        arguments = dict(root=self.root, sources=["src"], documents=["README.md"], output=self.output)
        arguments.update(kwargs)
        return living.snapshot(**arguments)

    def report(self, **kwargs: object) -> dict:
        arguments = dict(root=self.root, snapshot_path=self.output)
        arguments.update(kwargs)
        return living.status(**arguments)

    def write_manifest(self, payload: object) -> None:
        self.output.write_text(json.dumps(payload), encoding="utf-8")

    def contents(self) -> dict[str, bytes]:
        return {
            p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()
        }

    def test_snapshot_records_only_relative_evidence_and_preserves_source_bytes(self) -> None:
        binary = b"\x00\xffprivate byte payload\x00" * 90000
        (self.root / "src/binary.dat").write_bytes(binary)
        before = self.contents()
        with patch.object(living.shutil, "which", return_value=None):
            manifest = self.capture()
        self.assertEqual(manifest, json.loads(self.output.read_text(encoding="utf-8")))
        self.assertEqual(manifest["schema"], "agent-docs-kit/living-docs")
        self.assertIs(type(manifest["schema_version"]), int)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["kit_version"], "0.5.0-rc.1")
        self.assertEqual(manifest["semantic_review"], "not_attested")
        self.assertEqual(manifest["coverage"], "declared_sources_and_documents")
        self.assertNotIn("coverage_note", manifest)
        self.assertEqual(manifest["hash_algorithm"], "sha256")
        self.assertIsNone(manifest["git_head"])
        self.assertTrue(manifest["created_at"].endswith("Z"))
        self.assertEqual(manifest["source_scopes"], [{"path": "src", "kind": "directory"}])
        self.assertEqual(manifest["source_directories"], ["src", "src/empty"])
        files = {item["path"]: item for item in manifest["sources"]}
        self.assertEqual(
            files["src/binary.dat"],
            {
                "path": "src/binary.dat",
                "size": len(binary),
                "sha256": hashlib.sha256(binary).hexdigest(),
            },
        )
        serialized = self.output.read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("private byte payload", serialized)
        self.assertNotIn("agent-docs-kit:draft", serialized)
        self.assertEqual(self.contents(), before)

    def test_unchanged_and_draft_documents_never_attest_semantic_review(self) -> None:
        self.capture()
        before = self.contents()
        result = self.report()
        self.assertEqual(result["state"], "unchanged_since_snapshot")
        self.assertEqual(result["semantic_review"], "not_attested")
        for group in ("sources", "documents"):
            self.assertEqual(result[group], {"added": [], "modified": [], "deleted": []})
        self.assertEqual(result["scope_changes"], [])
        self.assertEqual(result["directory_changes"], {"added": [], "deleted": []})
        self.assertIn("coverage", result)
        self.assertIsInstance(result["coverage_note"], str)
        self.assertEqual(self.contents(), before)

    def test_added_modified_deleted_and_renamed_sources_without_a_commit(self) -> None:
        self.capture()
        (self.root / "src/main.py").write_bytes(b"value = 2\n")
        (self.root / "src/remove.py").rename(self.root / "src/renamed.py")
        (self.root / "src/new.py").write_bytes(b"new = True\n")
        result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(
            result["sources"],
            {
                "added": ["src/new.py", "src/renamed.py"],
                "modified": ["src/main.py"],
                "deleted": ["src/remove.py"],
            },
        )
        self.assertEqual(result["documents"]["modified"], [])

    def test_out_of_scope_changes_do_not_expand_declared_coverage(self) -> None:
        self.capture()
        (self.root / "unobserved.py").write_bytes(b"outside scope\n")
        self.assertEqual(self.report()["state"], "unchanged_since_snapshot")

    def test_modified_and_deleted_documents_require_review(self) -> None:
        (self.root / "notes.md").write_bytes(b"# Other document\n")
        self.capture(documents=["README.md", "notes.md"])
        (self.root / "README.md").write_bytes(b"# Authored document\n")
        (self.root / "notes.md").unlink()
        result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(
            result["documents"],
            {
                "added": [],
                "modified": ["README.md"],
                "deleted": ["notes.md"],
            },
        )

    def test_deleted_file_and_empty_directory_scopes_require_review(self) -> None:
        self.capture(sources=["src/main.py", "src/empty"])
        (self.root / "src/main.py").unlink()
        (self.root / "src/empty").rmdir()
        result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(result["sources"]["deleted"], ["src/main.py"])
        self.assertEqual(
            result["scope_changes"],
            [
                {"path": "src/empty", "before": "directory", "after": "missing"},
                {"path": "src/main.py", "before": "file", "after": "missing"},
            ],
        )
        self.assertEqual(result["directory_changes"]["deleted"], ["src/empty"])

    def test_deleted_directory_scope_reports_every_previously_observed_file(self) -> None:
        self.capture()
        shutil.rmtree(self.root / "src")
        result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(result["sources"]["deleted"], ["src/main.py", "src/remove.py"])
        self.assertEqual(
            result["scope_changes"],
            [
                {"path": "src", "before": "directory", "after": "missing"},
            ],
        )

    def test_scope_kind_changes_cannot_produce_false_green(self) -> None:
        self.capture(sources=["src/main.py", "src/empty"])
        (self.root / "src/main.py").unlink()
        (self.root / "src/main.py").mkdir()
        (self.root / "src/empty").rmdir()
        (self.root / "src/empty").write_bytes(b"now a file")
        result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(
            result["scope_changes"],
            [
                {"path": "src/empty", "before": "directory", "after": "file"},
                {"path": "src/main.py", "before": "file", "after": "directory"},
            ],
        )
        self.assertEqual(result["sources"]["added"], ["src/empty"])
        self.assertEqual(result["sources"]["deleted"], ["src/main.py"])

    def test_empty_subdirectory_changes_are_observed(self) -> None:
        self.capture()
        (self.root / "src/empty").rmdir()
        (self.root / "src/new empty").mkdir()
        result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(
            result["directory_changes"],
            {
                "added": ["src/new empty"],
                "deleted": ["src/empty"],
            },
        )

    def test_file_scope_becoming_directory_does_not_authorize_reading_new_children(self) -> None:
        self.capture(sources=["src/main.py"])
        selected = self.root / "src/main.py"
        selected.unlink()
        selected.mkdir()
        child = selected / "private.py"
        child.write_bytes(b"outside the original file selection")
        original_open = os.open

        def forbidden(path: object, *args: object, **kwargs: object) -> int:
            if Path(path) == child:
                raise AssertionError("A file scope did not authorize this child read")
            return original_open(path, *args, **kwargs)

        with patch.object(living.os, "open", forbidden):
            result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(
            result["sources"],
            {
                "added": [],
                "modified": [],
                "deleted": ["src/main.py"],
            },
        )
        self.assertEqual(
            result["scope_changes"],
            [
                {"path": "src/main.py", "before": "file", "after": "directory"},
            ],
        )

    def test_an_original_parent_directory_scope_still_covers_new_children(self) -> None:
        manifest = self.capture(sources=["src", "src/main.py"])
        manifest["source_scopes"].reverse()
        self.write_manifest(manifest)
        selected = self.root / "src/main.py"
        selected.unlink()
        selected.mkdir()
        (selected / "new.py").write_bytes(b"covered by src")
        result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(result["sources"]["added"], ["src/main.py/new.py"])
        self.assertEqual(result["sources"]["deleted"], ["src/main.py"])

    def test_snapshot_remains_usable_after_root_copy_and_move(self) -> None:
        self.capture()
        copied = self.base / "copied"
        shutil.copytree(self.root, copied)
        self.assertEqual(self.report(root=copied)["state"], "unchanged_since_snapshot")
        moved = self.base / "moved"
        copied.rename(moved)
        shutil.rmtree(self.root)
        self.assertEqual(self.report(root=moved)["state"], "unchanged_since_snapshot")

    def test_excluded_names_are_recorded_and_not_traversed(self) -> None:
        names = [".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__"]
        for name in names:
            (self.root / "src" / name).mkdir()
            (self.root / "src" / name / "ignored").write_bytes(b"ignored")
        manifest = self.capture()
        self.assertEqual(manifest["exclusions"], names)
        for name in names:
            (self.root / "src" / name / "ignored").write_bytes(b"changed")
        self.assertEqual(self.report()["state"], "unchanged_since_snapshot")

    def test_excluded_explicit_scopes_and_documents_are_errors(self) -> None:
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules/doc.md").write_bytes(b"ignored")
        for parameters in ({"sources": ["node_modules"]}, {"documents": ["node_modules/doc.md"]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.capture(**parameters)
        self.assertFalse(self.output.exists())

    def test_root_scope_and_overlapping_scopes_are_deduplicated(self) -> None:
        manifest = self.capture(sources=[".", "src", "src/main.py"])
        paths = [item["path"] for item in manifest["sources"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertIn("README.md", paths)
        self.assertIn(".", manifest["source_directories"])
        self.assertEqual(self.report()["state"], "unchanged_since_snapshot")

    def test_invalid_and_duplicate_normalized_paths_fail_before_writing(self) -> None:
        invalid = [
            "",
            "../outside",
            "src/../README.md",
            "/tmp/source",
            "C:\\source",
            "C:source",
            "src\\main.py",
            "src/\x00bad",
        ]
        for value in invalid:
            with self.subTest(path=value), self.assertRaises(ValueError):
                self.capture(sources=[value])
        for parameters in (
            {"sources": []},
            {"documents": []},
            {"sources": ["src", "./src"]},
            {"documents": ["README.md", "./README.md"]},
            {"sources": "src"},
            {"documents": ["src"]},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.capture(**parameters)
        self.assertFalse(self.output.exists())

    def test_missing_inputs_do_not_create_a_partial_snapshot(self) -> None:
        for parameters in ({"sources": ["missing"]}, {"documents": ["missing.md"]}):
            with self.subTest(parameters=parameters), self.assertRaises((OSError, ValueError)):
                self.capture(**parameters)
        self.assertFalse(self.output.exists())

    def test_snapshot_is_exclusive_and_creates_needed_output_parents(self) -> None:
        self.output = self.base / "new/evidence/baseline.json"
        self.capture()
        baseline = self.output.read_bytes()
        before = self.contents()
        with self.assertRaises(FileExistsError):
            self.capture()
        self.assertEqual(self.output.read_bytes(), baseline)
        self.assertEqual(self.contents(), before)

    def test_output_cannot_observe_itself_as_source_or_document(self) -> None:
        for output in (self.root / "src/evidence.json", self.root / "src/main.py", self.root / "README.md"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.capture(output=output)
        self.assertFalse((self.root / "src/evidence.json").exists())

    def test_status_rejects_snapshot_copied_into_its_own_source_scope(self) -> None:
        self.capture()
        copied = self.root / "src/evidence.json"
        shutil.copyfile(self.output, copied)
        with self.assertRaises(ValueError):
            self.report(snapshot_path=copied)

    def test_status_rejects_a_hardlink_to_the_snapshot_inside_a_directory_scope(self) -> None:
        self.capture()
        alias = self.root / "src/snapshot-alias.json"
        try:
            os.link(self.output, alias)
        except OSError as exc:
            self.skipTest(f"Filesystem does not support this hardlink fixture: {exc}")
        with self.assertRaises(ValueError):
            self.report()

    def test_symlink_sources_documents_and_descendants_are_rejected(self) -> None:
        outside = self.base / "outside.py"
        outside.write_bytes(b"must not follow")
        link = self.root / "link.py"
        link.symlink_to(outside)
        for parameters in ({"sources": ["link.py"]}, {"documents": ["link.py"]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.capture(**parameters)
        nested = self.root / "src/link.py"
        nested.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.capture()
        nested.unlink()
        nested.symlink_to(self.base / "missing")
        with self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(outside.read_bytes(), b"must not follow")
        self.assertFalse(self.output.exists())

    def test_symlink_roots_outputs_and_dotdot_components_are_rejected(self) -> None:
        link = self.base / "linked"
        link.symlink_to(self.root, target_is_directory=True)
        for root in (link, link / ".." / self.root.name):
            with self.subTest(root=root), self.assertRaises(ValueError):
                self.capture(root=root)
        for output in (link / "evidence.json", link / ".." / "evidence.json"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.capture(output=output)
        self.assertFalse(self.output.exists())

    def test_status_rejects_symlinks_in_manifest_path_or_current_inputs(self) -> None:
        self.capture()
        linked = self.base / "linked.json"
        linked.symlink_to(self.output)
        with self.assertRaises(ValueError):
            self.report(snapshot_path=linked)
        (self.root / "src/main.py").unlink()
        (self.root / "src/main.py").symlink_to(self.root / "README.md")
        with self.assertRaises(ValueError):
            self.report()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Named pipe fixture requires POSIX")
    def test_nonregular_sources_and_documents_are_errors_without_opening_them(self) -> None:
        pipe = self.root / "src/pipe"
        os.mkfifo(pipe)
        for parameters in ({}, {"sources": ["src/pipe"]}, {"documents": ["src/pipe"]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.capture(**parameters)
        self.assertFalse(self.output.exists())

    def test_document_replaced_by_directory_is_an_error(self) -> None:
        self.capture()
        (self.root / "README.md").unlink()
        (self.root / "README.md").mkdir()
        with self.assertRaises(ValueError):
            self.report()

    def test_corrupt_unknown_and_duplicate_key_json_are_errors(self) -> None:
        self.capture()
        for raw in (
            "{broken",
            "[]",
            "null",
            '{"schema": "a", "schema": "b"}',
            '{"number": NaN}',
            '"not a manifest"',
        ):
            with self.subTest(raw=raw):
                self.output.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.report()

    def test_manifest_schema_types_hashes_and_semantic_claim_are_strict(self) -> None:
        baseline = self.capture()
        mutations = [
            lambda p: p.update(schema="unknown"),
            lambda p: p.update(schema_version=2),
            lambda p: p.update(schema_version=True),
            lambda p: p.update(kit_version="unknown"),
            lambda p: p.update(semantic_review="approved"),
            lambda p: p.update(hash_algorithm="md5"),
            lambda p: p.update(coverage="the entire repository is approved"),
            lambda p: p.update(created_at="yesterday"),
            lambda p: p.update(git_head="not-a-commit"),
            lambda p: p.update(unknown=True),
            lambda p: p.pop("documents"),
            lambda p: p.update(exclusions=[]),
            lambda p: p.update(source_scopes=[]),
            lambda p: p.update(documents=[]),
            lambda p: p["sources"][0].update(size=True),
            lambda p: p["sources"][0].update(size=-1),
            lambda p: p["sources"][0].update(size=1.0),
            lambda p: p["sources"][0].update(sha256="a" * 63),
            lambda p: p["sources"][0].update(extra="field"),
            lambda p: p["source_scopes"][0].update(kind="unknown"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(baseline)
                mutation(payload)
                self.write_manifest(payload)
                with self.assertRaises(ValueError):
                    self.report()

    def test_schema_compatibility_does_not_depend_on_the_kit_release(self) -> None:
        baseline = self.capture()
        for version in ("0.2.1", "9.1.0-rc.2+local", "1.0.0"):
            with self.subTest(version=version):
                payload = copy.deepcopy(baseline)
                payload["kit_version"] = version
                self.write_manifest(payload)
                self.assertEqual(self.report()["state"], "unchanged_since_snapshot")

    def test_manifest_cannot_invent_coverage_or_duplicate_paths(self) -> None:
        baseline = self.capture()
        mutations = [
            lambda p: p["sources"][0].update(path="README.md"),
            lambda p: p["sources"][0].update(path="../outside"),
            lambda p: p["sources"][0].update(path="src/../README.md"),
            lambda p: p["sources"][0].update(path="src/.git/config"),
            lambda p: p["sources"].append(copy.deepcopy(p["sources"][0])),
            lambda p: p["documents"].append(copy.deepcopy(p["documents"][0])),
            lambda p: p["documents"][0].update(path="./README.md"),
            lambda p: p["documents"][0].update(path="."),
            lambda p: p["source_scopes"].append(copy.deepcopy(p["source_scopes"][0])),
            lambda p: p["source_directories"].append("elsewhere"),
            lambda p: p["source_directories"].append("src"),
            lambda p: p["source_directories"].remove("src"),
            lambda p: p["source_directories"].append("src/main.py"),
            lambda p: p["source_scopes"][0].update(kind="file"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(baseline)
                mutation(payload)
                self.write_manifest(payload)
                with self.assertRaises(ValueError):
                    self.report()

    def test_document_inside_directory_scope_requires_matching_source_evidence(self) -> None:
        (self.root / "src/README.md").write_bytes(b"# Observed source and document\n")
        payload = self.capture(documents=["src/README.md"])
        payload["sources"] = [item for item in payload["sources"] if item["path"] != "src/README.md"]
        self.write_manifest(payload)
        with self.assertRaises(ValueError):
            self.report()

    def test_permission_and_disappearance_during_read_are_errors(self) -> None:
        self.capture()
        original_open = os.open
        target = self.root / "src/main.py"

        def denied(path: object, *args: object, **kwargs: object) -> int:
            if Path(path) == target:
                raise PermissionError("simulated denied read")
            return original_open(path, *args, **kwargs)

        with patch.object(living.os, "open", denied), self.assertRaises(PermissionError):
            self.report()

        def disappeared(path: object, *args: object, **kwargs: object) -> int:
            if Path(path) == target:
                target.unlink()
            return original_open(path, *args, **kwargs)

        with patch.object(living.os, "open", disappeared), self.assertRaises(FileNotFoundError):
            self.report()

    def test_directory_enumeration_failure_is_not_treated_as_empty(self) -> None:
        self.capture()
        original_scandir = os.scandir

        def denied(path: object) -> object:
            if Path(path) == self.root / "src":
                raise PermissionError("simulated traversal denial")
            return original_scandir(path)

        with patch.object(living.os, "scandir", denied), self.assertRaises(PermissionError):
            self.report()

    def test_write_failure_cleans_new_output_parents(self) -> None:
        output = self.base / "new/evidence/snapshot.json"
        original_open = Path.open

        def denied(path: Path, *args: object, **kwargs: object) -> object:
            if path == output:
                raise PermissionError("simulated output denial")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", denied), self.assertRaises(PermissionError):
            self.capture(output=output)
        self.assertFalse((self.base / "new").exists())

    def test_absent_or_timed_out_git_is_optional_context(self) -> None:
        with patch.object(living.shutil, "which", return_value=None):
            manifest = self.capture()
            result = self.report()
        self.assertIsNone(manifest["git_head"])
        self.assertEqual(result["git"], {"snapshot_head": None, "current_head": None})
        self.output.unlink()
        with (
            patch.object(living.shutil, "which", return_value="/usr/bin/git"),
            patch.object(living.subprocess, "run", side_effect=subprocess.TimeoutExpired(["git"], 3)),
        ):
            self.assertIsNone(self.capture()["git_head"])

    @unittest.skipUnless(shutil.which("git"), "Git integration fixture requires Git")
    def test_real_git_head_is_context_and_uncommitted_bytes_drive_status(self) -> None:
        executable = shutil.which("git")

        def git(*args: str) -> None:
            subprocess.run(
                [executable, *args],
                cwd=self.root,
                check=True,
                capture_output=True,
                env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            )

        git("init")
        git("add", ".")
        git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "fixture",
            "--no-gpg-sign",
        )
        baseline = self.capture()
        self.assertRegex(baseline["git_head"], r"^[0-9a-f]{40,64}$")
        git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "context only",
            "--no-gpg-sign",
        )
        result = self.report()
        self.assertEqual(result["state"], "unchanged_since_snapshot")
        self.assertNotEqual(result["git"]["snapshot_head"], result["git"]["current_head"])
        (self.root / "src/main.py").write_bytes(b"value = 3\n")
        self.assertEqual(self.report()["state"], "review_required")

    def test_git_executable_inside_the_inspected_repository_is_never_run(self) -> None:
        executable = self.root / "git"
        marker = self.base / "executed"
        executable.write_text(
            f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8"
        )
        executable.chmod(0o700)
        with patch.object(living.shutil, "which", return_value=str(executable)):
            self.assertIsNone(self.capture()["git_head"])
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()

"""Real Git fixtures: gate the index while preserving every inspected byte."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import git_gate
import living_docs


@unittest.skipUnless(shutil.which("git"), "Git is required for the index gate")
class GitGateTests(unittest.TestCase):
    SNAPSHOT = "docs/living/snapshots/base.json"
    HISTORY = "docs/living/history"
    SESSION = "9eef7346-14db-482d-81a3-5c687f5493a2"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="git gate ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repository with spaces"
        self.root.mkdir()
        self.git("init", "-q")
        self.write("src/main.py", b"VALUE = 1\n")
        self.write("README.md", b"# Documentation\n")
        self.capture()
        self.git("add", "--", "src", "README.md", self.SNAPSHOT)
        self.commit("fixture baseline")

    def git(self, *arguments: str, input: bytes | None = None,
            extra_env: dict[str, str] | None = None) -> bytes:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                           GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
        if extra_env:
            environment.update(extra_env)
        result = subprocess.run(
            [shutil.which("git"), "--no-pager", "--no-optional-locks",
             "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
             "-c", "user.name=Index Fixture", "-c", "user.email=index-fixture@example.invalid",
             "-C", str(self.root), *arguments],
            input=input, capture_output=True, check=False, timeout=20, env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return result.stdout

    def write(self, path: str, data: bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def capture(self, *, sources: list[str] | None = None) -> dict:
        output = self.root / self.SNAPSHOT
        if output.exists():
            output.unlink()
        return living_docs.snapshot(self.root, sources or ["src"], ["README.md"], output)

    def check(self, **arguments: object) -> dict:
        values = dict(root=self.root, snapshot_path=self.SNAPSHOT)
        values.update(arguments)
        return git_gate.check_index(**values)

    def commit(self, message: str) -> None:
        # The fixture alone writes commits, always with hooks disabled.
        self.git("commit", "-q", "--no-verify", "-m", message)

    def segment(self, count: int = 1, *, closed: bool = False) -> bytes:
        events = []
        for sequence in range(1, count + 1):
            event = {
                "schema_version": 1, "event_id": str(UUID(int=sequence)),
                "idempotency_key": f"fixture-{sequence}", "session_id": self.SESSION,
                "sequence": sequence, "occurred_at": "2026-11-13T12:00:00Z",
                "kind": "session.closed" if closed and sequence == count else "work.logged",
                "source": "test", "payload": {"summary": f"event {sequence}"},
            }
            events.append(json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        return b"".join(events)

    def history_file(self) -> str:
        return f"{self.HISTORY}/{self.SESSION}.jsonl"

    def track_history(self, data: bytes, *, commit: bool = True) -> str:
        path = self.history_file()
        self.write(path, data)
        self.git("add", "--", path)
        if commit:
            self.commit("fixture history")
        return path

    def inventory(self) -> dict:
        inventory = {}
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root).as_posix()
            info = path.lstat()
            data = os.readlink(path) if path.is_symlink() else None if path.is_dir() else path.read_bytes()
            inventory[relative] = (info.st_mode, info.st_size, info.st_mtime_ns, data)
        return inventory

    def assert_read_only(self, callback) -> object:
        before_status = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        before = self.inventory()
        result = callback()
        self.assertEqual(self.inventory(), before)
        self.assertEqual(self.git("status", "--porcelain=v1", "-z", "--untracked-files=all"), before_status)
        return result

    def test_clean_index_preserves_repository_and_never_attests_semantics(self) -> None:
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "clean")
        self.assertEqual(report["semantic_review"], "not_attested")
        self.assertEqual(report["history"]["checked_segments"], 0)
        self.assertEqual(report["history"]["state"], "append_only")
        self.assertIsNone(report["facts"])
        self.assertFalse((self.root / self.HISTORY).exists())

    def test_worktree_changes_are_not_substituted_for_index_bytes(self) -> None:
        self.write("src/main.py", b"VALUE = 2\n")
        self.write("README.md", b"# Unstaged documentation\n")
        self.write("src/untracked.py", b"UNTRACKED = True\n")
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "clean")

    def test_partially_staged_snapshot_cannot_produce_false_clean(self) -> None:
        self.write("src/main.py", b"VALUE = 2\n")
        self.capture()
        self.git("add", "--", self.SNAPSHOT)
        # The worktree matches the snapshot; the proposed index still has VALUE=1.
        self.assertEqual(living_docs.status(self.root, self.root / self.SNAPSHOT)["state"],
                         "unchanged_since_snapshot")
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["sources"]["modified"], ["src/main.py"])

    def test_staged_source_changes_require_review_even_if_worktree_was_restored(self) -> None:
        self.write("src/main.py", b"VALUE = 2\n")
        self.git("add", "--", "src/main.py")
        self.write("src/main.py", b"VALUE = 1\n")
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["sources"]["modified"], ["src/main.py"])

    def test_alternate_index_is_honored_without_mutating_either_index(self) -> None:
        alternate = self.root / ".git" / "fixture-alternate-index"
        environment = {"GIT_INDEX_FILE": str(alternate)}
        self.git("read-tree", "HEAD", extra_env=environment)
        self.write("src/main.py", b"VALUE = 2\n")
        self.git("add", "--", "src/main.py", extra_env=environment)
        self.assertEqual(self.check()["state"], "clean")
        with patch.dict(os.environ, environment):
            report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["git"]["index"], "GIT_INDEX_FILE")

    def test_relative_alternate_index_is_resolved_from_invoking_directory(self) -> None:
        alternate = self.root / ".git" / "fixture-alternate-index"
        environment = {"GIT_INDEX_FILE": str(alternate)}
        self.git("read-tree", "HEAD", extra_env=environment)
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {"GIT_INDEX_FILE": ".git/fixture-alternate-index"}):
                self.assertEqual(self.check()["state"], "clean")
        finally:
            os.chdir(previous)

    def test_snapshot_must_exist_in_index_even_when_present_in_worktree(self) -> None:
        self.git("rm", "--cached", "--", self.SNAPSHOT)
        self.assertTrue((self.root / self.SNAPSHOT).exists())
        with self.assertRaisesRegex(ValueError, "snapshot.*índice"):
            self.check()

    def test_invalid_staged_snapshot_is_not_replaced_with_valid_worktree_copy(self) -> None:
        original = (self.root / self.SNAPSHOT).read_bytes()
        self.write(self.SNAPSHOT, b"{invalid")
        self.git("add", "--", self.SNAPSHOT)
        self.write(self.SNAPSHOT, original)
        with self.assertRaises(ValueError):
            self.check()

    def test_snapshot_unknown_schema_and_duplicate_keys_are_rejected(self) -> None:
        original = (self.root / self.SNAPSHOT).read_bytes()
        invalid = json.loads(original)
        invalid["schema_version"] = 999
        for data in (json.dumps(invalid).encode(), original.replace(b'"schema_version": 1',
                                                                   b'"schema_version": 1, "schema_version": 1')):
            with self.subTest(data=data[:60]):
                self.write(self.SNAPSHOT, data)
                self.git("add", "--", self.SNAPSHOT)
                with self.assertRaises(ValueError):
                    self.check()

    def test_missing_staged_document_requires_review(self) -> None:
        self.git("rm", "--cached", "--", "README.md")
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["documents"]["deleted"], ["README.md"])

    def test_sources_added_deleted_and_renamed_are_compared_from_index(self) -> None:
        self.git("mv", "--", "src/main.py", "src/renamed.py")
        self.write("src/new.py", b"NEW = True\n")
        self.git("add", "--", "src/new.py")
        self.assertEqual(self.check()["sources"], {
            "added": ["src/new.py", "src/renamed.py"], "modified": [], "deleted": ["src/main.py"],
        })

    def test_empty_directories_cannot_be_claimed_present_in_git(self) -> None:
        (self.root / "src/empty").mkdir()
        self.capture()
        self.git("add", "--", self.SNAPSHOT)
        report = self.check()
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["directory_changes"]["deleted"], ["src/empty"])
        self.assertEqual(report["directory_model"], git_gate.DIRECTORY_MODEL)

    def test_file_scope_becoming_directory_does_not_expand_coverage(self) -> None:
        self.capture(sources=["src/main.py"])
        self.git("add", "--", self.SNAPSHOT)
        (self.root / "src/main.py").unlink()
        self.write("src/main.py/child.txt", b"not selected by the file scope\n")
        self.git("add", "-A", "--", "src")
        report = self.check()
        self.assertEqual(report["sources"], {"added": [], "modified": [], "deleted": ["src/main.py"]})
        self.assertEqual(report["scope_changes"], [{"path": "src/main.py", "before": "file", "after": "directory"}])

    def test_excluded_index_paths_and_out_of_scope_changes_do_not_expand_coverage(self) -> None:
        self.write("src/node_modules/ignored", b"excluded\n")
        self.write("unobserved.py", b"outside\n")
        self.git("add", "--", "src/node_modules/ignored", "unobserved.py")
        self.assertEqual(self.check()["state"], "clean")

    def test_regular_executable_mode_is_supported(self) -> None:
        self.git("update-index", "--chmod=+x", "src/main.py")
        self.assertEqual(self.check()["state"], "clean")

    def test_symlink_is_rejected_even_outside_snapshot_coverage(self) -> None:
        try:
            os.symlink("README.md", self.root / "unobserved-link")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        self.git("add", "--", "unobserved-link")
        with self.assertRaisesRegex(ValueError, "archivos regulares"):
            self.check()

    def test_gitlink_is_rejected(self) -> None:
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{head},vendor/submodule")
        with self.assertRaisesRegex(ValueError, "archivos regulares"):
            self.check()

    def test_unmerged_entries_are_rejected(self) -> None:
        first = self.git("hash-object", "-w", "--stdin", input=b"first\n").decode().strip()
        second = self.git("hash-object", "-w", "--stdin", input=b"second\n").decode().strip()
        self.git("update-index", "--force-remove", "src/main.py")
        records = f"100644 {first} 1\tsrc/main.py\n100644 {first} 2\tsrc/main.py\n100644 {second} 3\tsrc/main.py\n"
        self.git("update-index", "--index-info", input=records.encode())
        with self.assertRaisesRegex(ValueError, "conflicto"):
            self.check()

    def test_intent_to_add_entries_cannot_produce_false_clean(self) -> None:
        self.write("src/intended.py", b"")
        self.capture()
        self.git("add", "--", self.SNAPSHOT)
        self.git("add", "-N", "--", "src/intended.py")
        with self.assertRaisesRegex(ValueError, "intent-to-add"):
            self.check()

    def test_intent_to_add_replacing_a_tracked_path_is_also_rejected(self) -> None:
        self.write("src/main.py", b"")
        self.capture()
        self.git("add", "--", self.SNAPSHOT)
        self.git("rm", "--cached", "--", "src/main.py")
        self.git("add", "-N", "--", "src/main.py")
        # Both --name-only variants list this path: visible says modified,
        # invisible says deleted. Comparing raw metadata must still reject it.
        with self.assertRaisesRegex(ValueError, "intent-to-add"):
            self.check()

    def test_history_new_valid_segment_and_append_are_allowed(self) -> None:
        path = self.track_history(self.segment(), commit=False)
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "clean")
        self.assertEqual(report["history"]["new_segments"], [path])
        self.commit("fixture first history row")
        self.write(path, self.segment(2))
        self.git("add", "--", path)
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "clean")
        self.assertEqual(report["history"]["appended_segments"], [path])

    def test_empty_history_segment_is_valid_without_events(self) -> None:
        path = self.track_history(b"", commit=False)
        report = self.check()
        self.assertEqual(report["state"], "clean")
        self.assertEqual(report["history"]["new_segments"], [path])

    def test_history_deletion_is_rejected(self) -> None:
        path = self.track_history(self.segment())
        self.git("rm", "--cached", "--", path)
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["history"]["violations"][0]["reason"], "deleted")

    def test_history_rename_is_rejected_as_loss_of_committed_segment(self) -> None:
        path = self.track_history(self.segment())
        target = f"{self.HISTORY}/7e32a319-5af1-4f9d-bd8f-950cb72a426b.jsonl"
        self.git("mv", "--", path, target)
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertIn({"path": path, "reason": "deleted", "detail": "Un segmento de HEAD no puede borrarse ni renombrarse."},
                      report["history"]["violations"])

    def test_history_valid_json_prefix_rewrite_is_rejected(self) -> None:
        path = self.track_history(self.segment())
        self.write(path, self.segment().replace(b'"summary":"event 1"', b'"summary":"edited"'))
        self.git("add", "--", path)
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["history"]["violations"][0]["reason"], "prefix_changed")

    def test_closed_history_must_preserve_exact_bytes(self) -> None:
        path = self.track_history(self.segment(closed=True))
        self.assertEqual(self.check()["state"], "clean")
        self.write(path, self.segment(closed=True) + self.segment(2).splitlines(keepends=True)[1])
        self.git("add", "--", path)
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["history"]["violations"][0]["reason"], "closed_segment_changed")

    def test_staged_history_is_validated_without_using_worktree_copy(self) -> None:
        path = self.track_history(self.segment().rstrip(b"\n"), commit=False)
        self.write(path, self.segment())
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["history"]["violations"][0]["reason"], "invalid_segment")

    def test_unstaged_history_corruption_does_not_change_proposed_tree(self) -> None:
        path = self.track_history(self.segment())
        self.write(path, b"corrupted only in the worktree")
        self.assertEqual(self.assert_read_only(self.check)["state"], "clean")

    def test_nested_history_and_invalid_segment_names_are_rejected(self) -> None:
        for path in (f"{self.HISTORY}/nested/{self.SESSION}.jsonl", f"{self.HISTORY}/not-a-uuid.jsonl"):
            with self.subTest(path=path):
                self.write(path, b"")
                self.git("add", "--", path)
                self.assertEqual(self.check()["state"], "review_required")
                self.git("rm", "--cached", "--", path)

    def test_history_lock_sidecar_is_not_treated_as_an_event(self) -> None:
        self.write(self.history_file() + ".lock", b"operational locking metadata")
        self.git("add", "--", self.history_file() + ".lock")
        report = self.check()
        self.assertEqual(report["state"], "clean")
        self.assertEqual(report["history"]["checked_segments"], 0)

    def test_event_ids_must_be_unique_across_staged_sessions(self) -> None:
        self.track_history(self.segment())
        other_session = "7e32a319-5af1-4f9d-bd8f-950cb72a426b"
        other_path = f"{self.HISTORY}/{other_session}.jsonl"
        self.write(other_path, self.segment().replace(self.SESSION.encode(), other_session.encode()))
        self.git("add", "--", other_path)
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["history"]["violations"][0]["reason"], "duplicate_event_id")

    def facts_fixture(self) -> str:
        path = "docs/living/facts.json"
        manifest = {"schema_version": 1, "facts": [{
            "id": "release", "canonical": {"file": "VERSION", "pattern": r"release=(\d+)"},
            "copies": [{"file": "notes.md", "pattern": r"release=(\d+)"}],
        }]}
        self.write(path, json.dumps(manifest).encode())
        self.write("VERSION", b"release=1\n")
        self.write("notes.md", b"release=1\n")
        self.git("add", "--", path, "VERSION", "notes.md")
        return path

    def test_facts_use_index_content_and_ignore_unstaged_changes(self) -> None:
        facts_path = self.facts_fixture()
        self.write("VERSION", b"release=2\n")
        self.write("notes.md", b"release=9\n")
        report = self.assert_read_only(lambda: self.check(facts_path=facts_path))
        self.assertEqual(report["state"], "clean")
        self.assertEqual(report["facts"]["state"], "in_sync")
        self.assertEqual(report["facts"]["facts"][0]["canonical"]["value"], "1")

    def test_staged_fact_mismatch_requires_review_even_when_worktree_matches(self) -> None:
        facts_path = self.facts_fixture()
        self.write("notes.md", b"release=9\n")
        self.git("add", "--", "notes.md")
        self.write("notes.md", b"release=1\n")
        report = self.assert_read_only(lambda: self.check(facts_path=facts_path))
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["facts"]["facts"][0]["copies"][0]["reason"], "mismatch")

    def test_declared_facts_manifest_must_be_staged(self) -> None:
        facts_path = self.facts_fixture()
        self.git("rm", "--cached", "--", facts_path)
        with self.assertRaisesRegex(ValueError, "manifiesto.*índice"):
            self.check(facts_path=facts_path)

    def test_invalid_staged_facts_manifest_is_not_replaced_from_worktree(self) -> None:
        facts_path = self.facts_fixture()
        valid = (self.root / facts_path).read_bytes()
        self.write(facts_path, b"{invalid")
        self.git("add", "--", facts_path)
        self.write(facts_path, valid)
        with self.assertRaises(ValueError):
            self.check(facts_path=facts_path)

    def test_fact_reader_rejects_non_utf8_and_oversized_index_blobs(self) -> None:
        facts_path = self.facts_fixture()
        for content in (b"\xff", b"a" * (1024 * 1024 + 1)):
            with self.subTest(size=len(content)):
                self.write("notes.md", content)
                self.git("add", "--", "notes.md")
                with self.assertRaises(ValueError):
                    self.check(facts_path=facts_path)

    def test_git_configuration_cannot_execute_project_helpers(self) -> None:
        helper = self.root / "project-helper.sh"
        marker = self.root / "helper-was-executed"
        self.write("project-helper.sh", b"#!/bin/sh\ntouch helper-was-executed\nexit 0\n")
        helper.chmod(0o755)
        self.write(".gitattributes", b"*.py diff=project\n")
        self.git("add", "--", ".gitattributes")
        self.git("config", "core.fsmonitor", str(helper))
        self.git("config", "diff.external", str(helper))
        self.git("config", "diff.project.textconv", str(helper))
        self.assertEqual(self.assert_read_only(self.check)["state"], "clean")
        self.assertFalse(marker.exists())

    def test_unborn_repository_can_check_its_first_staged_tree(self) -> None:
        self.git("update-ref", "-d", "HEAD")
        report = self.assert_read_only(self.check)
        self.assertEqual(report["state"], "clean")
        self.assertIsNone(report["git"]["current_head"])

    def test_non_repository_and_subdirectory_roots_are_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        for root in (outside, self.root / "src"):
            with self.subTest(root=root), self.assertRaises(ValueError):
                self.check(root=root)


if __name__ == "__main__":
    unittest.main()

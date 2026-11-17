"""Public-boundary and test-isolation regressions from fresh EGM round two."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_support import git, inventory, make_repo, run_cli


class RoundTwoRegressions(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(self)
        self.root = self.repo["root"]

    def test_status_caps_new_directory_source_on_first_read(self):
        import doc_evidence
        import living_docs

        (self.root / "src/new.bin").write_bytes(b"x" * 128)
        observed = []
        original = living_docs._fingerprint

        def spy(path, relative, **kwargs):
            if relative == "src/new.bin":
                observed.append(kwargs.get("maximum_bytes"))
                self.assertEqual(kwargs.get("maximum_bytes"), 32, "New descendant reached unbounded reader")
            return original(path, relative, **kwargs)

        with (
            patch.object(doc_evidence, "MAX_SOURCE_BYTES", 32),
            patch.object(living_docs, "_fingerprint", side_effect=spy),
        ):
            with self.assertRaisesRegex(ValueError, "size|limit"):
                living_docs.status(self.root, self.root / "snapshot.json", knowledge_map="links.json")
        self.assertEqual(observed, [32])
        self.assertEqual(
            living_docs.status(self.root, self.root / "snapshot.json")["state"], "review_required"
        )

    def test_status_caps_snapshot_on_first_read(self):
        import doc_evidence
        import living_docs

        observed = []
        original = living_docs._fingerprint

        def spy(path, relative, **kwargs):
            if Path(path) == self.root / "snapshot.json":
                observed.append(kwargs.get("maximum_bytes"))
                self.assertEqual(kwargs.get("maximum_bytes"), 32, "Snapshot read without its 8 MiB policy")
            return original(path, relative, **kwargs)

        with (
            patch.object(doc_evidence, "MAX_EVIDENCE", 32),
            patch.object(living_docs, "_fingerprint", side_effect=spy),
        ):
            with self.assertRaisesRegex(ValueError, "size|limit"):
                living_docs.status(self.root, self.root / "snapshot.json", knowledge_map="links.json")
        # A pre-read stat rejection may avoid the fingerprint altogether.
        self.assertTrue(not observed or observed == [32])
        self.assertEqual(
            living_docs.status(self.root, self.root / "snapshot.json")["state"], "unchanged_since_snapshot"
        )

    def test_status_caps_document_on_every_preliminary_read(self):
        import doc_revisions
        import living_docs

        observed = []
        original = living_docs._fingerprint

        def spy(path, relative, **kwargs):
            if relative == "PROJECT.md":
                observed.append(kwargs.get("maximum_bytes"))
                self.assertEqual(
                    kwargs.get("maximum_bytes"), 32, "Document first read used only generic source limit"
                )
            return original(path, relative, **kwargs)

        with (
            patch.object(doc_revisions, "MAX_DOCUMENT", 32),
            patch.object(living_docs, "_fingerprint", side_effect=spy),
        ):
            with self.assertRaisesRegex(ValueError, "size|limit"):
                living_docs.status(self.root, self.root / "snapshot.json", knowledge_map="links.json")
        self.assertTrue(not observed or observed == [32])

    def test_index_history_root_file_is_operational_error_in_both_commands(self):
        path = self.root / "docs/living/history"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not a directory\n")
        self.assertEqual(git(self.root, "add", "docs/living/history").returncode, 0)
        before = inventory(self.root)
        for op, extra in [("check", ["--snapshot", "snapshot.json"]), ("read", ["--document", "project"])]:
            result = run_cli(
                self.root,
                "knowledge",
                op,
                str(self.root),
                "--map",
                "links.json",
                "--view",
                "index",
                "--json",
                *extra,
            )
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["state"], "error")
        self.assertEqual(inventory(self.root), before)

    def test_fixture_helpers_strip_ambient_git_redirects(self):
        # Everything called external here is an owned disposable sentinel.
        sentinel = make_repo(self)
        sr = sentinel["root"]
        before = inventory(sr)
        redirected = {
            "GIT_DIR": str(sr / ".git"),
            "GIT_WORK_TREE": str(sr),
            "GIT_INDEX_FILE": str(sr / ".git/index"),
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, redirected):
            local = Path(tmp).resolve()
            initialized = git(local, "init", "-q")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertTrue((local / ".git").exists(), "git -C fixture was redirected by ambient environment")
            self.assertEqual(git(local, "config", "user.name", "Synthetic isolated user").returncode, 0)
            result = run_cli(
                self.root,
                "knowledge",
                "check",
                str(self.root),
                "--map",
                "links.json",
                "--snapshot",
                "snapshot.json",
                "--json",
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(inventory(sr), before)

    def test_fixture_helpers_keep_only_explicit_alternate_index_override(self):
        sentinel = make_repo(self)
        sr = sentinel["root"]
        before = inventory(sr)
        alternate = self.root / "owned-alt-index"
        with patch.dict(os.environ, {"GIT_INDEX_FILE": str(sr / ".git/index")}):
            result = git(self.root, "read-tree", "HEAD", env={"GIT_INDEX_FILE": str(alternate)})
            self.assertEqual(result.returncode, 0, result.stderr)
            checked = run_cli(
                self.root,
                "knowledge",
                "check",
                str(self.root),
                "--map",
                "links.json",
                "--snapshot",
                "snapshot.json",
                "--view",
                "index",
                "--json",
                env={"GIT_INDEX_FILE": str(alternate)},
            )
            self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        self.assertTrue(alternate.exists())
        self.assertEqual(inventory(sr), before)

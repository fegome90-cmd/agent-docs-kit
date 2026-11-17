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

from knowledge_support import KIT, git, inventory, make_repo, require_module


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.m = require_module(self, "doc_evidence")
        self.repo = make_repo(self)
        self.root = self.repo["root"]

    def capture(self, kind="worktree"):
        view = self.m.open_view(self.root, kind)
        return self.m.capture_evidence(
            view,
            self.repo["links"],
            "snapshot.json",
            "links.json",
            {"project": view.read("PROJECT.md", maximum=1048576)},
            {"project": []},
        )

    def test_fingerprint_ignores_time_not_content(self):
        evidence = self.capture()
        altered = copy.deepcopy(evidence)
        altered["observed_at"] = "2030-01-01T00:00:00Z"
        self.assertEqual(self.m.evidence_id(altered), evidence["evidence_id"])
        altered["files"][0]["sha256"] = "0" * 64
        self.assertNotEqual(self.m.evidence_id(altered), evidence["evidence_id"])

    def test_view_is_part_of_evidence_identity(self):
        a, b = self.capture(), self.capture("index")
        self.assertEqual(a["files"], b["files"])
        self.assertNotEqual(a["evidence_id"], b["evidence_id"])

    def test_index_reads_blob_not_worktree(self):
        before = (self.root / "src/catalog.py").read_bytes()
        (self.root / "src/catalog.py").write_bytes(b"changed local\n")
        view = self.m.open_view(self.root, "index")
        self.assertEqual(view.read("src/catalog.py", maximum=1000), before)
        self.assertEqual(view.fingerprint("src/catalog.py")["sha256"], hashlib.sha256(before).hexdigest())
        self.assertIsNone(view.read("absent", maximum=1000))
        (self.root / "absent").write_bytes(b"not staged")
        self.assertIsNone(view.read("absent", maximum=1000))
        from git_gate import _Git, _read_index

        runner = _Git(self.root)
        raw, entries = _read_index(runner)
        reused = self.m.from_index(runner, raw, entries, runner.head())
        self.assertEqual(reused.read("src/catalog.py", maximum=1000), before)

    def test_alternate_index_and_root_paths(self):
        alternate = self.root.parent / (self.root.name + "-alternate-index")
        self.addCleanup(lambda: alternate.unlink(missing_ok=True))
        shutil.copyfile(self.root / ".git/index", alternate)
        (self.root / "src/catalog.py").write_bytes(b"alternate\n")
        self.assertEqual(
            git(self.root, "add", "src/catalog.py", env={"GIT_INDEX_FILE": str(alternate)}).returncode, 0
        )
        with patch.dict(os.environ, {"GIT_INDEX_FILE": str(alternate)}):
            view = self.m.open_view(self.root, "index")
            self.assertEqual(view.read("src/catalog.py", maximum=1000), b"alternate\n")
        self.assertNotEqual(
            self.m.open_view(self.root, "index").read("src/catalog.py", maximum=1000), b"alternate\n"
        )
        prior = Path.cwd()
        try:
            os.chdir(self.root.parent)
            self.assertEqual(
                self.m.open_view(self.root, "worktree").read("links.json", maximum=1048576),
                (self.root / "links.json").read_bytes(),
            )
        finally:
            os.chdir(prior)

    def test_missing_is_not_permission_error(self):
        view = self.m.open_view(self.root, "worktree")
        self.assertEqual(
            view.fingerprint("absent"), {"path": "absent", "present": False, "size": None, "sha256": None}
        )
        with patch("living_docs._fingerprint", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                view.fingerprint("src/catalog.py")

    def test_read_is_stable_and_side_effect_free(self):
        before = inventory(self.root)
        view = self.m.open_view(self.root, "worktree")
        view.fingerprint("src/catalog.py")
        view.assert_unchanged()
        self.assertEqual(inventory(self.root), before)
        (self.root / "src/catalog.py").write_bytes(b"race\n")
        with self.assertRaises((ValueError, OSError)):
            view.assert_unchanged()
        index = self.m.open_view(self.root, "index")
        git(self.root, "add", "src/catalog.py")
        with self.assertRaises((ValueError, OSError)):
            index.assert_unchanged()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            for file in KIT.glob("*.py"):
                shutil.copyfile(file, target / file.name)
            script = 'import sys; sys.dont_write_bytecode=True; import doc_evidence; assert "portalocker" not in sys.modules'
            env = dict(os.environ)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=target, env=env, capture_output=True, timeout=30
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((target / "__pycache__").exists())

    def test_raw_hash_distinguishes_string_and_indent(self):
        hashes = []
        for raw in [
            b'x="a b"\n',
            b'x="a  b"\n',
            b"if x:\n y=1\n",
            b"if x:\n  y=1\n",
            b'"""a\n b"""\n',
            b'"""a\n  b"""\n',
        ]:
            (self.root / "src/catalog.py").write_bytes(raw)
            hashes.append(self.m.open_view(self.root, "worktree").fingerprint("src/catalog.py")["sha256"])
        self.assertEqual(len(set(hashes)), len(hashes))

    def test_limits_and_unsafe_paths_fail(self):
        view = self.m.open_view(self.root, "worktree")
        for p in ["../escape", "/absolute", "src//catalog.py", ".git/index", "src/./catalog.py"]:
            with self.assertRaises(ValueError):
                view.read(p, maximum=100)
        with self.assertRaises(ValueError):
            view.read("PROJECT.md", maximum=1)
        (self.root / "sym").symlink_to(self.root / "PROJECT.md")
        with self.assertRaises(ValueError):
            view.read("sym", maximum=1048576)
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            self.m.open_view(Path(temp), "worktree")
        with self.assertRaises(ValueError):
            self.m.open_view(self.root, "other")

    def test_strict_envelope_and_revalidation(self):
        envelope = self.capture()
        for mutate in [
            lambda x: x.update(extra=1),
            lambda x: x["files"][0].update(extra=1),
            lambda x: x["inventory"].update(extra=1),
            lambda x: x["map"].update(sha256="short"),
            lambda x: x.update(schema_version=True),
            lambda x: x.update(files=x["files"] * 2),
            lambda x: x["base_documents"][0].update(extra=1),
        ]:
            bad = copy.deepcopy(envelope)
            mutate(bad)
            with self.assertRaises(ValueError):
                self.m.evidence_id(bad)
        self.assertEqual(
            self.m.revalidate(self.m.open_view(self.root, "worktree"), envelope, phase="proposal")["state"],
            "matches_review",
        )
        (self.root / "src/catalog.py").write_bytes(b"new\n")
        self.assertEqual(
            self.m.revalidate(self.m.open_view(self.root, "worktree"), envelope, phase="proposal")["state"],
            "stale",
        )
        with self.assertRaises(ValueError):
            self.m.revalidate(self.m.open_view(self.root, "worktree"), envelope, phase="anything")

    def test_inventory_retains_unmapped_without_reading_content(self):
        (self.root / "src/new.bin").write_bytes(b"\xff" * 200)
        view = self.m.open_view(self.root, "worktree")
        captured = self.m.capture_evidence(
            view,
            self.repo["links"],
            "snapshot.json",
            "links.json",
            {"project": view.read("PROJECT.md", maximum=1048576)},
            {"project": []},
        )
        self.assertIn("src/new.bin", captured["inventory"]["not_evaluated"])
        self.assertNotIn("src/new.bin", {f["path"] for f in captured["files"]})

    def test_capture_preserves_explicit_parent_heads(self):
        view = self.m.open_view(self.root, "worktree")
        heads = {"project": ["12345678-1234-4234-8234-123456789abc"]}
        result = self.m.capture_evidence(
            view,
            self.repo["links"],
            "snapshot.json",
            "links.json",
            {"project": view.read("PROJECT.md", maximum=1048576)},
            heads,
        )
        self.assertEqual(result["heads"], heads)

    def test_resolved_accepted_context_and_baseline_integrity(self):
        raw = (self.root / "PROJECT.md").read_bytes().replace(b"Catalog\n", b"Accepted catalog\n")
        heads = {"project": ["12345678-1234-4234-8234-123456789abc"]}
        resolved = {
            "base_documents": {"project": raw},
            "heads": heads,
            "proposal_ids": {"project": "a" * 64},
            "selection": {"project": "accepted"},
        }
        envelope = self.m.capture_evidence(
            self.m.open_view(self.root, "worktree"),
            self.repo["links"],
            "snapshot.json",
            "links.json",
            resolved["base_documents"],
            heads,
            proposal_ids=resolved["proposal_ids"],
        )
        self.assertEqual(envelope["base_documents"][0]["proposal_id"], "a" * 64)
        self.assertEqual(
            self.m.revalidate(
                self.m.open_view(self.root, "worktree"), envelope, phase="proposal", resolved=resolved
            )["state"],
            "matches_review",
        )
        with self.assertRaises(ValueError):
            self.m.revalidate(self.m.open_view(self.root, "worktree"), envelope, phase="proposal")
        (self.root / "snapshot.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            self.m.revalidate(
                self.m.open_view(self.root, "worktree"), envelope, phase="proposal", resolved=resolved
            )

    def test_new_snapshot_refresh_both_views_retains_debt(self):
        for kind in ("worktree", "index"):
            with self.subTest(kind=kind):
                envelope = self.capture(kind)
                old = (self.root / "snapshot.json").read_bytes()
                selected = {
                    "document_id": "project",
                    "path": "PROJECT.md",
                    "sha256": hashlib.sha256((self.root / "PROJECT.md").read_bytes()).hexdigest(),
                }
                baseline = json.loads(old)
                baseline["created_at"] = "2030-01-01T00:00:00Z"
                (self.root / "new-snapshot.json").write_text(json.dumps(baseline))
                if kind == "index":
                    git(self.root, "add", "new-snapshot.json")
                self.assertEqual(
                    self.m.revalidate(
                        self.m.open_view(self.root, kind),
                        envelope,
                        phase="selected",
                        selected=selected,
                        snapshot_path="new-snapshot.json",
                    )["state"],
                    "matches_review",
                )
                (self.root / "src/catalog.py").write_bytes(b"changed\n")
                if kind == "index":
                    git(self.root, "add", "src/catalog.py")
                from living_docs import snapshot

                snapshot(
                    self.root,
                    ["src"],
                    ["PROJECT.md"],
                    self.root / (kind + "-later-snapshot.json"),
                    discover_roots=["src"],
                )
                if kind == "index":
                    git(self.root, "add", kind + "-later-snapshot.json")
                self.assertEqual(
                    self.m.revalidate(
                        self.m.open_view(self.root, kind),
                        envelope,
                        phase="selected",
                        selected=selected,
                        snapshot_path=kind + "-later-snapshot.json",
                    )["state"],
                    "stale",
                )
                self.assertEqual((self.root / "snapshot.json").read_bytes(), old)
                git(self.root, "checkout", "--", "src/catalog.py")
                git(self.root, "reset", "-q", "HEAD", "--", "src/catalog.py")
                git(self.root, "checkout", "--", "src/catalog.py")

    def test_growing_read_aborts_before_retaining_overshoot(self):
        import living_docs

        real = os.fdopen

        class Growing:
            def __init__(self, stream):
                self.stream = stream
                self.reads = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                self.reads += 1
                if self.reads > 3:
                    raise AssertionError("read loop exceeded bounded growth")
                return b"x" * 8

        chunks = []
        with patch("living_docs.os.fdopen", side_effect=lambda *a: Growing(real(*a))):
            with self.assertRaisesRegex(ValueError, "limit"):
                living_docs._fingerprint(
                    self.root / "PROJECT.md", "PROJECT.md", chunks=chunks, maximum_bytes=10
                )
        self.assertEqual(chunks, [b"x" * 8])

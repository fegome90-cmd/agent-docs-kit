"""Discovery contract through real CLI processes and isolated Git repositories."""

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


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="docs discovery ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repo with spaces"
        (self.root / "src").mkdir(parents=True)
        self.write("src/base.py", "VALUE = 1\n")
        self.write("README.md", "# Example\n")
        self.write(".gitignore", "src/ignored*\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS="0",
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.git("init", "-q")
        self.git("add", "--", "src/base.py", "README.md", ".gitignore")
        self.git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=" + os.devnull,
            "commit",
            "-qm",
            "baseline",
        )
        self.ref = self.root / "evidence.json"

    def write(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
        return p

    def git(self, *args, env=None):
        p = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(self.root), *args],
            env=env or self.env,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(p.returncode, 0, p.stderr.decode(errors="replace"))
        return p.stdout

    def cli(self, *args, expected=0, env=None):
        p = subprocess.run(
            [sys.executable, "-B", str(CLI), *map(str, args)],
            env=env or self.env,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(p.returncode, expected, p.stdout + p.stderr)
        return json.loads(p.stdout) if p.stdout.strip().startswith("{") else p.stdout

    def capture(self, source="src/base.py", roots=("src",), ref=None, expected=0):
        args = [
            "snapshot",
            self.root,
            "--source",
            source,
            "--document",
            "README.md",
            "--output",
            ref or self.ref,
            "--apply",
        ]
        for root in roots:
            args += ["--discover-root", root]
        return self.cli(*args, expected=expected)

    def status(self, expected=0, env=None):
        return self.cli("status", self.root, "--snapshot", self.ref, "--json", expected=expected, env=env)

    def gate(self, expected=0, env=None):
        return self.cli(
            "index-check", self.root, "--snapshot", "evidence.json", "--json", expected=expected, env=env
        )

    def test_v2_policy_is_persisted_and_consumed_without_status_flag(self):
        s = self.capture()
        self.assertEqual(s["schema_version"], 2)
        self.assertEqual(s["discovery"], {"mode": "git", "roots": ["src"]})
        r = self.status()
        self.assertEqual(r["coverage_check"]["state"], "complete")
        self.assertEqual(r["coverage_check"]["view"], "worktree")
        self.assertEqual(r["semantic_review"], "not_attested")

    def test_new_untracked_file_is_uncovered(self):
        self.capture()
        self.write("src/new.md", "# New\n")
        r = self.status(1)
        self.assertEqual(r["state"], "review_required")
        self.assertEqual(r["coverage_check"]["uncovered"], [{"path": "src/new.md", "git": "untracked"}])
        self.assertEqual(r["sources"]["added"], [])

    def test_existing_uncovered_file_cannot_be_hidden_by_new_snapshot(self):
        self.write("src/already.md", "# Existing but not selected\n")
        self.capture()
        self.assertEqual(self.status(1)["coverage_check"]["uncovered"][0]["path"], "src/already.md")

    def test_declared_directory_new_file_is_added_not_uncovered(self):
        self.capture(source="src")
        self.write("src/new.md", "# New\n")
        r = self.status(1)
        self.assertEqual(r["sources"]["added"], ["src/new.md"])
        self.assertEqual(r["coverage_check"]["uncovered"], [])

    def test_ignored_untracked_is_not_discovered(self):
        self.capture()
        self.write("src/ignored.bin", b"\xff\x00")
        self.assertEqual(self.status()["coverage_check"]["uncovered"], [])

    def test_tracked_ignored_file_is_discovered(self):
        self.capture()
        self.write("src/ignored.py", "X = 2\n")
        self.git("add", "-f", "--", "src/ignored.py")
        self.assertEqual(
            self.status(1)["coverage_check"]["uncovered"], [{"path": "src/ignored.py", "git": "tracked"}]
        )

    def test_new_binary_is_reported_as_path_not_contents(self):
        self.capture()
        self.write("src/asset.bin", b"\xff\xfe\x00payload")
        r = self.status(1)
        self.assertEqual(r["coverage_check"]["uncovered"], [{"path": "src/asset.bin", "git": "untracked"}])
        self.assertNotIn("payload", json.dumps(r))

    def test_nested_roots_and_outside_files(self):
        (self.root / "src/nested").mkdir()
        self.capture(roots=("src/nested",))
        self.write("src/outside.md", "outside\n")
        self.status()
        self.write("src/nested/new.md", "inside\n")
        self.status(1)

    def test_multiple_roots(self):
        (self.root / "tests").mkdir()
        self.capture(roots=("tests", "src"))
        self.write("tests/new.txt", "test\n")
        r = self.status(1)
        self.assertEqual(r["coverage_check"]["roots"], ["src", "tests"])
        self.assertEqual(r["coverage_check"]["uncovered"][0]["path"], "tests/new.txt")

    def test_weird_filenames_are_nul_delimited_and_deterministic(self):
        self.capture()
        names = ["src/space file.md", "src/ñandú.md", "src/line\nbreak.md", "src/tab\tname.md", "src/[a].md"]
        for n in names:
            self.write(n, "content\n")
        r = self.status(1)
        self.assertEqual([x["path"] for x in r["coverage_check"]["uncovered"]], sorted(names))

    def test_sources_modified_and_deleted_keep_original_detection(self):
        self.capture()
        self.write("src/base.py", "VALUE = 2\n")
        self.assertEqual(self.status(1)["sources"]["modified"], ["src/base.py"])
        (self.root / "src/base.py").unlink()
        self.assertEqual(self.status(1)["sources"]["deleted"], ["src/base.py"])

    def test_document_inside_root_counts_as_covered(self):
        self.write("src/doc.md", "# Doc\n")
        self.cli(
            "snapshot",
            self.root,
            "--source",
            "src/base.py",
            "--document",
            "src/doc.md",
            "--discover-root",
            "src",
            "--output",
            self.ref,
            "--apply",
        )
        self.status()

    def test_v1_contract_has_no_discovery_field(self):
        p = self.capture(roots=())
        self.assertEqual(p["schema_version"], 1)
        self.assertNotIn("discovery", p)
        self.write("src/new.md", "new\n")
        self.assertNotIn("coverage_check", self.status())

    def test_v1_still_works_without_git(self):
        shutil.rmtree(self.root / ".git")
        self.capture(roots=())
        self.status()

    def test_git_required_for_v2_snapshot(self):
        shutil.rmtree(self.root / ".git")
        self.capture(expected=3)
        self.assertFalse(self.ref.exists())

    def test_git_missing_cannot_return_clean(self):
        self.capture()
        r = self.status(3, env={**self.env, "PATH": str(self.base / "no-executables")})
        self.assertEqual(r["state"], "error")

    def test_invalid_discovery_roots_create_nothing(self):
        (self.root / "src/nested").mkdir()
        for roots in [
            ("../src",),
            ("/tmp",),
            ("missing",),
            ("src/base.py",),
            ("src", "./src"),
            ("src", "src/nested"),
        ]:
            with self.subTest(roots=roots):
                self.capture(roots=roots, expected=3)
                self.assertFalse(self.ref.exists())

    def test_deleted_discovery_root_is_error(self):
        (self.root / "new-root").mkdir()
        self.capture(roots=("new-root",))
        (self.root / "new-root").rmdir()
        self.status(3)

    def test_symlink_root_rejected_without_writing(self):
        (self.root / "linked").symlink_to(self.root / "src", target_is_directory=True)
        self.capture(roots=("linked",), expected=3)
        self.assertFalse(self.ref.exists())

    def test_discovered_symlink_is_error_not_followed(self):
        self.capture()
        external = self.base / "private"
        external.write_text("not to read\n")
        self.write("src/safe.md", "safe\n")
        (self.root / "src/link").symlink_to(external)
        self.status(3)
        self.assertEqual(external.read_text(), "not to read\n")

    def test_output_under_discovery_root_is_rejected(self):
        self.capture(ref=self.root / "src/evidence.json", expected=3)
        self.assertFalse((self.root / "src/evidence.json").exists())

    def test_v2_invalid_schema_cannot_return_clean(self):
        original = self.capture()
        invalid = [
            {"mode": "bad", "roots": ["src"]},
            {"mode": "git", "roots": []},
            {"mode": "git", "roots": ["src", "src"]},
            {"mode": "git", "roots": ["src"], "extra": 1},
            {"mode": "git", "roots": "src"},
            {"mode": "git", "roots": ["../src"]},
        ]
        for policy in invalid:
            with self.subTest(policy=policy):
                payload = {**original, "discovery": policy}
                self.ref.write_text(json.dumps(payload))
                self.status(3)

    def test_legacy_schema_cannot_smuggle_discovery(self):
        original = self.capture()
        original["schema_version"] = 1
        self.ref.write_text(json.dumps(original))
        self.status(3)

    def test_index_ignores_untracked_worktree_files(self):
        self.capture()
        self.git("add", "--", "evidence.json")
        self.write("src/untracked.md", "new\n")
        self.status(1)
        r = self.gate()
        self.assertEqual(r["coverage_check"]["view"], "index")
        self.assertEqual(r["coverage_check"]["uncovered"], [])

    def test_index_staged_uncovered_even_if_worktree_file_deleted(self):
        self.capture()
        self.write("src/staged.md", "staged\n")
        self.git("add", "--", "src/staged.md", "evidence.json")
        (self.root / "src/staged.md").unlink()
        r = self.gate(1)
        self.assertEqual(r["coverage_check"]["uncovered"], [{"path": "src/staged.md", "git": "tracked"}])

    def test_index_uses_staged_policy_not_worktree_policy(self):
        self.capture()
        self.git("add", "--", "evidence.json")
        self.ref.write_text("{invalid")
        self.gate()

    def test_alternate_index_preserves_primary_bytes(self):
        self.capture()
        primary = self.root / ".git/index"
        before = primary.read_bytes()
        alt = self.base / "alternate-index"
        alt.write_bytes(before)
        env = {**self.env, "GIT_INDEX_FILE": str(alt)}
        self.write("src/new.md", "new\n")
        self.git("add", "--", "src/new.md", "evidence.json", env=env)
        self.gate(1, env=env)
        self.assertEqual(primary.read_bytes(), before)

    def test_directory_coverage_does_not_duplicate_staged_addition(self):
        self.capture(source="src")
        self.write("src/new.md", "new\n")
        self.git("add", "--", "src/new.md", "evidence.json")
        r = self.gate(1)
        self.assertEqual(r["sources"]["added"], ["src/new.md"])
        self.assertEqual(r["coverage_check"]["uncovered"], [])

    def test_new_snapshot_resolves_coverage_without_rewriting_old(self):
        self.capture()
        before = self.ref.read_bytes()
        self.write("src/new.md", "new\n")
        self.status(1)
        newer = self.root / "newer.json"
        self.capture(source="src", ref=newer)
        self.cli("status", self.root, "--snapshot", newer, "--json")
        self.assertEqual(self.ref.read_bytes(), before)

    def test_status_is_read_only_including_index(self):
        self.capture()
        self.write("src/new.md", "new\n")

        def inventory():
            return {
                str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*")
                if p.is_file()
            }

        before = inventory()
        self.status(1)
        self.assertEqual(inventory(), before)

    def test_new_file_outside_roots_has_no_coverage_claim(self):
        self.capture()
        self.write("outside/new.py", "outside\n")
        self.assertEqual(self.status()["coverage_check"]["roots"], ["src"])

    def test_case_alias_discovery_root_rejected(self):
        if not (self.root / "SRC").is_dir():
            self.skipTest("Filesystem is case-sensitive")
        self.capture(roots=("SRC",), expected=3)
        self.assertFalse(self.ref.exists())

    def test_discovery_never_fingerprints_uncovered_contents(self):
        sys.path.insert(0, str(KIT))
        from unittest.mock import patch

        import living_docs

        self.capture()
        self.write("src/new.bin", b"\x00\xffsecret-body")
        calls = []
        original = living_docs._fingerprint

        def observed(path, relative, **kwargs):
            calls.append(str(path))
            return original(path, relative, **kwargs)

        with patch.object(living_docs, "_fingerprint", side_effect=observed):
            r = living_docs.status(self.root, self.ref)
        self.assertEqual(r["coverage_check"]["state"], "uncovered")
        self.assertNotIn(str(self.root / "src/new.bin"), calls)

    def test_inventory_race_fails_without_publishing_snapshot(self):
        sys.path.insert(0, str(KIT))
        from unittest.mock import patch

        import discovery
        import living_docs

        before = (b"index", "head", {"src/base.py": "tracked"})
        after = (b"index", "head", {"src/base.py": "tracked", "src/new.md": "untracked"})
        with patch.object(discovery, "worktree_inventory", side_effect=[before, after]):
            with self.assertRaisesRegex(OSError, "inventario Git"):
                living_docs.snapshot(
                    self.root, ["src/base.py"], ["README.md"], self.ref, discover_roots=["src"]
                )
        self.assertFalse(self.ref.exists())

    def test_inventory_race_during_status_fails(self):
        sys.path.insert(0, str(KIT))
        from unittest.mock import patch

        import discovery
        import living_docs

        self.capture()
        before = (b"index", "head", {"src/base.py": "tracked"})
        after = (b"index", "head", {"src/base.py": "tracked", "src/new.md": "untracked"})
        with patch.object(discovery, "worktree_inventory", side_effect=[before, after]):
            with self.assertRaisesRegex(OSError, "inventario Git"):
                living_docs.status(self.root, self.ref)

    def test_unlisted_fifo_does_not_expand_git_inventory(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        self.capture()
        os.mkfifo(self.root / "src/pipe")
        self.assertNotIn(b"src/pipe", self.git("ls-files", "--others", "--exclude-standard", "-z"))
        self.assertEqual(self.status()["coverage_check"]["eligible_count"], 1)

    def test_fifo_is_rejected_if_git_reports_it(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        sys.path.insert(0, str(KIT))
        from unittest.mock import patch

        import discovery
        import git_gate

        os.mkfifo(self.root / "src/pipe")
        original = git_gate._Git.run

        def reported(git, *args, **kwargs):
            if args[:2] == ("ls-files", "--others"):
                return b"src/pipe\x00"
            return original(git, *args, **kwargs)

        with patch.object(git_gate._Git, "run", reported):
            with self.assertRaisesRegex(ValueError, "no regular"):
                discovery.worktree_inventory(self.root, {"mode": "git", "roots": ["src"]})

    def test_repository_local_ignore_rules_and_negation(self):
        self.capture()
        self.write("src/.gitignore", "*.temp\n!keep.temp\n")
        self.write("src/skip.temp", "ignored\n")
        self.write("src/keep.temp", "included\n")
        paths = [p["path"] for p in self.status(1)["coverage_check"]["uncovered"]]
        self.assertEqual(paths, ["src/.gitignore", "src/keep.temp"])

    def test_index_rejects_self_discovery_even_if_manifest_is_valid(self):
        original = self.capture()
        original["discovery"]["roots"] = ["."]
        self.ref.write_text(json.dumps(original))
        self.git("add", "--", "evidence.json")
        self.gate(3)

    def test_existing_snapshot_bytes_preserved_on_second_capture(self):
        self.capture()
        before = self.ref.read_bytes()
        self.capture(expected=1)
        self.assertEqual(self.ref.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

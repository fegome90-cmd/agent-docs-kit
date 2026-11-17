"""Real CLI, files, multiprocess writers, Git commits/hooks and optional Graphify.

No subprocess doubles are used here. Every repository is a new temporary fixture.
Set KIT_E2E_GRAPHIFY=1 to require the separately installed Graphify 0.9.26.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


class KitEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kit e2e documentación ")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "project with spaces"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src/config.py").write_text('VERSION = "1.0"\n', encoding="utf-8")
        (self.root / "README.md").write_text("# Proyecto\nVersion: 1.0\n", encoding="utf-8")
        self.deployed = self.root / "tools/agent-docs-kit"
        shutil.copytree(KIT, self.deployed, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.cli = self.deployed / "agent_docs_kit.py"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "GRAPHIFY_", "AGENT_DOCS_KIT_"))
        }
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        self.git("init", "-q")
        self.git("config", "user.name", "E2E fixture")
        self.git("config", "user.email", "e2e@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.root / ".gitignore").write_text("*.jsonl.lock\n__pycache__/\n", encoding="utf-8")
        self.session = str(uuid.uuid4())
        self.history = self.root / "docs/living/history"
        self.log = self.history / f"{self.session}.jsonl"
        self.snapshot_rel = "docs/living/snapshots/base.json"
        self.snapshot = self.root / self.snapshot_rel
        self.facts_rel = "docs/living/facts.json"

    def run_cli(self, *args: str, expected: int = 0, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-B", str(self.cli), *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def git(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args], env=self.env, text=True, capture_output=True, timeout=30
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def inventory(self) -> dict[str, str]:
        return {
            p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def capture(self, relative: str | None = None) -> None:
        self.run_cli(
            "snapshot",
            str(self.root),
            "--source",
            "src",
            "--document",
            "README.md",
            "--output",
            str(self.root / (relative or self.snapshot_rel)),
            "--apply",
        )

    def append(self, key: str, summary: str = "Nota conservada", kind: str = "note") -> dict:
        return json.loads(
            self.run_cli(
                "append",
                str(self.root),
                "--session",
                self.session,
                "--key",
                key,
                "--kind",
                kind,
                "--summary",
                summary,
                "--apply",
            ).stdout
        )

    def make_facts(self) -> None:
        path = self.root / self.facts_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "facts": [
                        {
                            "id": "version",
                            "canonical": {"file": "src/config.py", "pattern": 'VERSION = "([^"]+)"'},
                            "copies": [{"file": "README.md", "pattern": r"Version: (\S+)"}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def baseline_commit(self) -> None:
        self.capture()
        self.append("opening")
        self.make_facts()
        self.git("add", ".")
        self.git("commit", "-qm", "Initial fixture")

    def gate(self, expected: int = 0) -> dict:
        return json.loads(
            self.run_cli(
                "index-check",
                str(self.root),
                "--snapshot",
                self.snapshot_rel,
                "--facts",
                self.facts_rel,
                "--json",
                expected=expected,
            ).stdout
        )

    def test_bootstrap_inspect_prepare_author_check_and_exclusive_output(self) -> None:
        self.run_cli("inspect", str(self.root))
        proposal = self.base / "proposal"
        self.run_cli("prepare", str(self.root), "--output", str(proposal), "--apply")
        self.run_cli("check", str(proposal), expected=1)
        for rel in (
            "AGENTS.md",
            "docs/agent/CONTEXT.md",
            "docs/agent/ARCHITECTURE.md",
            "docs/agent/CODEMAP.md",
            "docs/agent/RUNTIME.md",
        ):
            (proposal / rel).write_text(
                "# Documento redactado\nContenido específico del fixture.\n", encoding="utf-8"
            )
        self.run_cli("check", str(proposal))
        before = {p: p.read_bytes() for p in proposal.rglob("*.md")}
        self.run_cli("prepare", str(self.root), "--output", str(proposal), "--apply", expected=1)
        self.assertEqual(before, {p: p.read_bytes() for p in proposal.rglob("*.md")})

    def test_snapshot_status_and_immutable_baseline(self) -> None:
        self.capture()
        before = self.snapshot.read_bytes()
        self.run_cli("status", str(self.root), "--snapshot", str(self.snapshot), "--json")
        (self.root / "src/new.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = json.loads(
            self.run_cli(
                "status", str(self.root), "--snapshot", str(self.snapshot), "--json", expected=1
            ).stdout
        )
        self.assertIn("src/new.py", report["sources"]["added"])
        self.run_cli(
            "snapshot",
            str(self.root),
            "--source",
            "src",
            "--document",
            "README.md",
            "--output",
            str(self.snapshot),
            "--apply",
            expected=1,
        )
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_append_retry_close_verify_render_and_exclusive_export(self) -> None:
        first = self.append("one", "Primera nota: información íntegra")
        prefix = self.log.read_bytes()
        retry = self.append("one", "Primera nota: información íntegra")
        self.assertEqual(first, retry)
        self.assertEqual(prefix, self.log.read_bytes())
        self.append("two", "Corrección posterior; la primera sigue presente")
        self.assertTrue(self.log.read_bytes().startswith(prefix))
        self.append("close", "Sesión terminada", "session.closed")
        before = self.inventory()
        verification = json.loads(self.run_cli("verify", str(self.root), "--json").stdout)
        self.assertEqual(verification["event_count"], 3)
        rendered = self.run_cli("render", str(self.root)).stdout
        self.assertIn("Primera nota", rendered)
        self.assertIn("Corrección posterior", rendered)
        self.assertEqual(before, self.inventory())
        self.run_cli(
            "append",
            str(self.root),
            "--session",
            self.session,
            "--key",
            "late",
            "--summary",
            "Too late",
            "--apply",
            expected=3,
        )
        export = self.base / "readable.md"
        self.run_cli("render", str(self.root), "--output", str(export), "--apply")
        saved = export.read_bytes()
        self.run_cli("render", str(self.root), "--output", str(export), "--apply", expected=1)
        self.assertEqual(export.read_bytes(), saved)

    def test_parallel_real_cli_writers_preserve_every_event(self) -> None:
        count = 16
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(self.cli),
                    "append",
                    str(self.root),
                    "--session",
                    self.session,
                    "--key",
                    f"writer-{i}",
                    "--summary",
                    f"Evento {i}",
                    "--timeout",
                    "15",
                    "--apply",
                ],
                cwd=self.root,
                env=self.env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for i in range(count)
        ]
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=25)
                self.assertEqual(process.returncode, 0, stdout + stderr)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(entries), count)
        self.assertEqual(
            {event["idempotency_key"] for event in entries}, {f"writer-{i}" for i in range(count)}
        )
        self.assertEqual(len({event["event_id"] for event in entries}), count)
        self.assertEqual([event["sequence"] for event in entries], list(range(1, count + 1)))
        self.run_cli("verify", str(self.root), "--json")

    def test_corrupt_tail_and_key_conflict_keep_original_bytes(self) -> None:
        self.append("one")
        before = self.log.read_bytes()
        self.run_cli(
            "append",
            str(self.root),
            "--session",
            self.session,
            "--key",
            "one",
            "--summary",
            "Distinto",
            "--apply",
            expected=3,
        )
        self.assertEqual(before, self.log.read_bytes())
        with self.log.open("ab") as stream:
            stream.write(b'{"partial":')
        damaged = self.log.read_bytes()
        self.run_cli("verify", str(self.root), "--json", expected=3)
        self.run_cli(
            "append",
            str(self.root),
            "--session",
            self.session,
            "--key",
            "two",
            "--summary",
            "No reparar automáticamente",
            "--apply",
            expected=3,
        )
        self.assertEqual(damaged, self.log.read_bytes())

    def test_facts_detect_drift_and_never_edit_documents(self) -> None:
        self.make_facts()
        self.run_cli("facts", str(self.root), "--manifest", self.facts_rel, "--json")
        (self.root / "README.md").write_text("Version: 0.9\n", encoding="utf-8")
        before = self.inventory()
        report = json.loads(
            self.run_cli("facts", str(self.root), "--manifest", self.facts_rel, "--json", expected=1).stdout
        )
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(before, self.inventory())

    def test_index_compares_staged_bytes_in_both_directions(self) -> None:
        self.baseline_commit()
        before = self.inventory()
        self.gate()
        self.assertEqual(before, self.inventory())
        source = self.root / "src/config.py"
        source.write_text('VERSION = "2.0"\n', encoding="utf-8")
        self.gate()  # Dirty working tree does not alter the proposed commit.
        self.git("add", "src/config.py")
        source.write_text('VERSION = "1.0"\n', encoding="utf-8")
        report = self.gate(expected=1)  # A clean-looking worktree cannot hide the staged change.
        self.assertIn("src/config.py", report["sources"]["modified"])

    def test_index_blocks_history_rewrite_deletion_and_rename(self) -> None:
        self.baseline_commit()
        original = self.log.read_bytes()
        self.log.write_bytes(original.replace(b"Nota conservada", b"Nota alterada"))
        self.git("add", str(self.log.relative_to(self.root)))
        self.assertEqual(self.gate(expected=1)["history"]["state"], "review_required")
        self.log.write_bytes(original)
        self.git("add", str(self.log.relative_to(self.root)))
        self.gate()
        self.git("rm", str(self.log.relative_to(self.root)))
        self.assertEqual(self.gate(expected=1)["history"]["state"], "review_required")
        (self.history / f"{uuid.uuid4()}.jsonl").write_bytes(original)
        self.git("add", "docs/living/history")
        self.gate(expected=1)

    def test_actual_native_hook_rejects_drift_and_accepts_new_snapshot(self) -> None:
        self.baseline_commit()
        hook = self.root / ".git/hooks/pre-commit"
        shutil.copyfile(self.deployed / "hooks/pre-commit.example", hook)
        hook.chmod(0o755)
        self.env["AGENT_DOCS_KIT_PYTHON"] = sys.executable
        self.env["AGENT_DOCS_KIT_FACTS"] = self.facts_rel
        (self.root / "src/config.py").write_text('VERSION = "2.0"\n', encoding="utf-8")
        self.git("add", "src/config.py")
        head = self.git("rev-parse", "HEAD").stdout
        # Git commit itself may refresh the index's TREE/stat cache even when
        # a hook rejects it. Compare the actual staged entries, not that cache.
        index = self.git("ls-files", "--stage", "-z").stdout
        self.git("commit", "-qm", "Must be rejected", expected=1)
        self.assertEqual(head, self.git("rev-parse", "HEAD").stdout)
        self.assertEqual(index, self.git("ls-files", "--stage", "-z").stdout)
        (self.root / "README.md").write_text("# Proyecto\nVersion: 2.0\n", encoding="utf-8")
        second = "docs/living/snapshots/second.json"
        self.capture(second)
        self.env["AGENT_DOCS_KIT_SNAPSHOT"] = second
        self.append("review", "Revisión registrada; no implica aceptación semántica automática")
        self.git("add", ".")
        self.git("commit", "-qm", "Reviewed fixture")
        self.assertNotEqual(head, self.git("rev-parse", "HEAD").stdout)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_separate_sessions_merge_without_rewriting_prior_logs(self) -> None:
        self.baseline_commit()
        base_branch = self.git("branch", "--show-current").stdout.strip()
        initial = self.log.read_bytes()
        self.git("checkout", "-qb", "session-left")
        self.session = str(uuid.uuid4())
        self.append("left")
        self.git("add", "docs/living/history")
        self.git("commit", "-qm", "Left session")
        self.git("checkout", "-q", base_branch)
        self.session = str(uuid.uuid4())
        self.append("right")
        self.git("add", "docs/living/history")
        self.git("commit", "-qm", "Right session")
        self.git("merge", "--no-edit", "session-left")
        report = json.loads(self.run_cli("verify", str(self.root), "--json").stdout)
        self.assertEqual(report["event_count"], 3)
        self.assertEqual(self.log.read_bytes(), initial)

    def test_index_and_verify_both_reject_event_ids_reused_across_sessions(self) -> None:
        self.baseline_commit()
        first_id = json.loads(self.log.read_text().splitlines()[0])["event_id"]
        self.session = str(uuid.uuid4())
        event = self.append("other-session")
        event["event_id"] = first_id
        second_log = self.history / f"{self.session}.jsonl"
        second_log.write_text(json.dumps(event) + "\n", encoding="utf-8")
        self.git("add", "docs/living/history")
        self.run_cli("verify", str(self.root), "--json", expected=3)
        report = self.gate(expected=1)
        self.assertTrue(report["history"]["violations"])

    def test_index_rejects_intent_to_add_even_when_file_is_empty(self) -> None:
        self.baseline_commit()
        (self.root / "src/empty.py").touch()
        self.snapshot_rel = "docs/living/snapshots/with-empty.json"
        self.capture()
        self.git("add", self.snapshot_rel)
        self.git("add", "-N", "src/empty.py")
        report = self.gate(expected=3)
        self.assertEqual(report["state"], "error")
        self.assertIn("intent-to-add", report["error"])

    def test_facts_cannot_certify_a_single_occurrence_as_its_own_copy(self) -> None:
        self.make_facts()
        config = {
            "schema_version": 1,
            "facts": [
                {
                    "id": "version",
                    "canonical": {"file": "README.md", "pattern": r"Version: (1\.0)"},
                    "copies": [{"file": "README.md", "pattern": r"Version: (\S+)"}],
                }
            ],
        }
        (self.root / self.facts_rel).write_text(json.dumps(config), encoding="utf-8")
        before = self.inventory()
        result = self.run_cli("facts", str(self.root), "--manifest", self.facts_rel, "--json", expected=3)
        self.assertIn("misma ocurrencia", result.stdout)
        self.assertEqual(before, self.inventory())

    @unittest.skipUnless(
        os.environ.get("KIT_E2E_GRAPHIFY") == "1", "Set KIT_E2E_GRAPHIFY=1 for installed Graphify"
    )
    def test_actual_graphify_code_only_and_append_run_events(self) -> None:
        self.assertIsNotNone(
            shutil.which("graphify", path=self.env["PATH"]), "Graphify was explicitly required"
        )
        (self.root / "src/math.py").write_text(
            "def double(value):\n    return value * 2\n\ndef main():\n    return double(3)\n",
            encoding="utf-8",
        )
        output = self.base / "new graph"
        before = self.inventory()
        self.run_cli(
            "graph", str(self.root / "src"), "--output", str(output), "--apply", "--timeout", "45", timeout=60
        )
        self.assertEqual(before, self.inventory())
        graph = json.loads((output / "graphify-out/graph.json").read_text())
        self.assertTrue(graph["nodes"])
        events = [json.loads(line) for line in (output / "run.jsonl").read_text().splitlines()]
        self.assertEqual([e["event"] for e in events], ["started", "finished"])
        self.assertEqual(events[-1]["status"], "complete")
        self.run_cli("graph", str(self.root / "src"), "--output", str(output), "--apply", expected=1)


if __name__ == "__main__":
    unittest.main()

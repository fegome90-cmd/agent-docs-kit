"""Portable process-level journeys; distribution and host pilot run separately."""

import hashlib
import json
import unittest
import uuid

from knowledge_support import git, make_repo, run_cli


class KnowledgeE2E(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(self)
        self.root = self.repo["root"]
        self.original = (self.root / "PROJECT.md").read_bytes()

    def cli(self, *args, code=0):
        p = run_cli(self.root, *args)
        self.assertEqual(p.returncode, code, p.stderr.decode() + p.stdout.decode()[:800])
        return json.loads(p.stdout)

    def check(self, snapshot="snapshot.json", code=1):
        return self.cli(
            "knowledge",
            "check",
            str(self.root),
            "--map",
            "links.json",
            "--snapshot",
            snapshot,
            "--json",
            code=code,
        )

    def propose(self, body, mode="bootstrap", reviewed=None):
        report = self.check(code=1 if mode == "bootstrap" else self.check_code)
        key = str(uuid.uuid4())
        candidate = "candidate-" + key + ".md"
        request = "request-" + key + ".json"
        bundle = "docs/living/revisions/" + key
        (self.root / candidate).write_bytes(body)
        data = {
            "schema": "agent-docs-kit/proposal-request",
            "schema_version": 1,
            "request_id": key,
            "project_id": self.repo["links"]["project_id"],
            "document_id": "project",
            "mode": mode,
            "candidate_path": candidate,
            "expected_parents": report["evidence"]["heads"]["project"],
            "reviewed_sections": reviewed
            or sorted(s["id"] for s in self.repo["links"]["documents"][0]["sections"]),
            "reason": "Explicit synthetic agent review",
            "evidence": report["evidence"],
        }
        (self.root / request).write_text(json.dumps(data))
        manifest = self.cli(
            "knowledge",
            "propose",
            str(self.root),
            "--request",
            request,
            "--output",
            bundle,
            "--apply",
            "--json",
        )
        review = "review-" + key + ".json"
        (self.root / review).write_text(
            json.dumps(
                {
                    "schema": "agent-docs-kit/review",
                    "schema_version": 1,
                    "proposal_id": manifest["proposal_id"],
                    "decision": "accept",
                    "reviewer": {"kind": "agent", "id": "e2e-reviewer"},
                    "reason": "Reviewed the complete requested spans against sources",
                    "review_reference": "fixture://e2e/" + key,
                    "constraint_authorizations": [],
                }
            )
        )
        return bundle, review

    def accept(self, proposal, code=0):
        return self.cli(
            "knowledge",
            "accept",
            str(self.root),
            "--proposal",
            proposal[0],
            "--review",
            proposal[1],
            "--session",
            str(uuid.uuid4()),
            "--key",
            "review",
            "--confirm-reviewed",
            "--apply",
            "--json",
            code=code,
        )

    def read(self, code=0):
        return self.cli(
            "knowledge",
            "read",
            str(self.root),
            "--map",
            "links.json",
            "--document",
            "project",
            "--json",
            code=code,
        )

    def test_e2e_bootstrap_change_review_read(self):
        adopted = self.accept(self.propose(self.original))
        version = run_cli(self.root, "--version")
        self.assertIn(b"0.5.0-rc.1", version.stdout)
        self.assertEqual(self.read()["body"], self.original.decode())
        (self.root / "src/catalog.py").write_bytes(b'catalog = "new"\n')
        self.assertEqual(self.check()["review_queue"], ["project.catalog", "project.summary"])
        self.check_code = 1
        body = self.original.replace(b"\nCatalog\n", b"\nNew catalogue\n").replace(
            b"\nPlan\n", b"\nReview plan\n"
        )
        stale = self.propose(body, "revise", ["project.catalog", "project.summary"])
        (self.root / "src/catalog.py").write_bytes(b'catalog = "newer"\n')
        before = list((self.root / "docs/living/history").glob("*.jsonl"))
        self.accept(stale, code=1)
        self.assertEqual(list((self.root / "docs/living/history").glob("*.jsonl")), before)
        fresh = self.propose(body, "revise", ["project.catalog", "project.summary"])
        accepted = self.accept(fresh)
        result = self.read()
        self.assertEqual(result["body"], body.decode())
        self.assertEqual(result["lineage"]["parents"], [adopted["event"]["event_id"]])
        self.assertEqual(result["lineage"]["event_id"], accepted["event"]["event_id"])
        self.assertEqual((self.root / "PROJECT.md").read_bytes(), self.original)
        self.assertEqual(len(list((self.root / "docs/living/history").glob("*.jsonl"))), 2)

    def test_e2e_independent_git_branches_conflict(self):
        self.accept(self.propose(self.original))
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "adopt")
        base = git(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        self.check_code = 0
        for branch, old, new in [("a", b"\nCatalog\n", b"\nBranch A\n"), ("b", b"\nPlan\n", b"\nBranch B\n")]:
            self.assertEqual(git(self.root, "checkout", "-qb", branch, base).returncode, 0)
            self.accept(self.propose(self.original.replace(old, new), "revise"))
            git(self.root, "add", ".")
            self.assertEqual(git(self.root, "commit", "-qm", "branch " + branch).returncode, 0)
        merged = git(self.root, "merge", "--no-edit", "a")
        self.assertEqual(merged.returncode, 0, merged.stderr)
        result = self.read(code=1)
        self.assertEqual(result["selection"], "conflict")
        self.assertNotIn("body", result)
        self.assertEqual(len(result["heads"]), 2)
        self.check_code = 1
        body = self.original.replace(b"\nCatalog\n", b"\nBranch A\n").replace(b"\nPlan\n", b"\nBranch B\n")
        self.accept(self.propose(body, "resolve"))
        self.assertEqual(self.read()["body"], body.decode())
        self.assertEqual(len(list((self.root / "docs/living/history").glob("*.jsonl"))), 4)
        self.assertEqual((self.root / "PROJECT.md").read_bytes(), self.original)

    def test_e2e_snapshot_refresh_not_semantic_approval(self):
        self.accept(self.propose(self.original))
        old = (self.root / "snapshot.json").read_bytes()
        (self.root / "src/catalog.py").write_bytes(b'catalog = "pending"\n')
        self.cli(
            "snapshot",
            str(self.root),
            "--source",
            "src",
            "--document",
            "PROJECT.md",
            "--discover-root",
            "src",
            "--output",
            str(self.root / "refresh.json"),
            "--apply",
        )
        report = self.check("refresh.json")
        self.assertEqual(report["review_queue"], ["project.catalog", "project.summary"])
        self.assertEqual(self.read(code=1)["state"], "stale")
        self.assertEqual(
            hashlib.sha256((self.root / "snapshot.json").read_bytes()).digest(), hashlib.sha256(old).digest()
        )

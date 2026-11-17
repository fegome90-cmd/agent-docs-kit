"""Executable regressions from controller and fresh Goldfish findings."""

import copy
import hashlib
import json
import unittest

from knowledge_support import adopt_document, make_repo, proposal_request

from doc_evidence import capture_evidence, evidence_id, open_view, revalidate
from doc_impact import compute_impact
from doc_revisions import load_acceptances, resolved_context
from living_docs import snapshot


class GateBRegressions(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(self)
        self.root = self.repo["root"]
        self.links = self.repo["links"]

    def test_content_equal_reacceptance_does_not_add_impact(self):
        self.links["documents"].append(
            {
                "id": "other",
                "origin": "OTHER.md",
                "sections": [
                    {
                        "id": "other.a",
                        "selector": {"kind": "document"},
                        "claim_kind": "observed",
                        "watches": ["src/runtime.py"],
                        "depends_on": [],
                    }
                ],
            }
        )
        self.links["documents"][0]["sections"][1]["depends_on"].append("other.a")
        (self.root / "links.json").write_text(json.dumps(self.links))
        (self.root / "OTHER.md").write_bytes(b"# Other\nA\n")
        bodies = {
            "project": (self.root / "PROJECT.md").read_bytes(),
            "other": (self.root / "OTHER.md").read_bytes(),
        }
        heads = {
            "project": ["12345678-1234-4234-8234-123456789abc"],
            "other": ["22345678-1234-4234-8234-123456789abc"],
        }
        old = capture_evidence(
            open_view(self.root, "worktree"),
            self.links,
            "snapshot.json",
            "links.json",
            bodies,
            heads,
            proposal_ids={"project": "a" * 64, "other": "b" * 64},
        )
        new = copy.deepcopy(old)
        for d in new["base_documents"]:
            d["proposal_id"] = "c" * 64
        new["heads"] = {
            "project": ["32345678-1234-4234-8234-123456789abc"],
            "other": ["42345678-1234-4234-8234-123456789abc"],
        }
        new["evidence_id"] = evidence_id(new)
        self.assertEqual(compute_impact(self.links, {"project": old, "other": old}, new)["review_queue"], [])
        for d in new["base_documents"]:
            if d["document_id"] == "other":
                d["sha256"] = hashlib.sha256(b"changed content").hexdigest()
        new["evidence_id"] = evidence_id(new)
        changed = compute_impact(self.links, {"project": old, "other": old}, new)
        self.assertIn("project.summary", changed["review_queue"])
        self.assertIn("other.a", changed["review_queue"])

    def test_new_baseline_is_not_substituted_for_proposal_analysis(self):
        raw = (self.root / "snapshot.json").read_bytes()
        env = capture_evidence(
            open_view(self.root, "worktree"),
            self.links,
            "snapshot.json",
            "links.json",
            {"project": (self.root / "PROJECT.md").read_bytes()},
            {"project": []},
        )
        snapshot(self.root, ["src"], ["PROJECT.md"], self.root / "later.json", discover_roots=["src"])
        result = revalidate(
            open_view(self.root, "worktree"), env, phase="proposal", snapshot_path="later.json"
        )
        self.assertEqual(result["state"], "stale")
        self.assertIn("baseline_changed", result["reasons"])
        selected = {
            "document_id": "project",
            "path": "PROJECT.md",
            "sha256": hashlib.sha256((self.root / "PROJECT.md").read_bytes()).hexdigest(),
        }
        self.assertEqual(
            revalidate(
                open_view(self.root, "worktree"),
                env,
                phase="selected",
                selected=selected,
                snapshot_path="later.json",
            )["state"],
            "matches_review",
        )
        self.assertEqual((self.root / "snapshot.json").read_bytes(), raw)

    def test_ignored_live_history_remains_authoritative_but_not_staged(self):
        (self.root / ".gitignore").write_text("docs/living/history/*.jsonl\n")
        index = (self.root / ".git/index").read_bytes()
        adopted = adopt_document(self.repo)
        self.assertEqual(adopted["result"]["state"], "recorded")
        self.assertEqual(adopted["result"]["exit_code"], 0)
        self.assertEqual(len(load_acceptances(open_view(self.root, "worktree"), "docs/living/history")), 1)
        context, _ = resolved_context(open_view(self.root, "worktree"), self.links)
        self.assertEqual(context["selection"]["project"], "accepted")
        self.assertEqual(load_acceptances(open_view(self.root, "index"), "docs/living/history"), [])
        with self.assertRaises(ValueError):
            proposal_request(self.repo, mode="bootstrap")
        self.assertEqual(len(load_acceptances(open_view(self.root, "worktree"), "docs/living/history")), 1)
        self.assertEqual((self.root / ".git/index").read_bytes(), index)

    def test_ignored_corrupt_explicit_history_is_not_silently_skipped(self):
        (self.root / ".gitignore").write_text("docs/living/history/*.jsonl\n")
        directory = self.root / "docs/living/history"
        directory.mkdir(parents=True)
        segment = directory / "12345678-1234-4234-8234-123456789abc.jsonl"
        segment.write_bytes(b"{broken")
        with self.assertRaises(ValueError):
            load_acceptances(open_view(self.root, "worktree"), "docs/living/history")
        self.assertEqual(segment.read_bytes(), b"{broken")

    def test_new_explicit_history_segment_invalidates_observed_view(self):
        (self.root / ".gitignore").write_text("docs/living/history/*.jsonl\n")
        directory = self.root / "docs/living/history"
        directory.mkdir(parents=True)
        view = open_view(self.root, "worktree")
        self.assertEqual(load_acceptances(view, "docs/living/history"), [])
        (directory / "12345678-1234-4234-8234-123456789abc.jsonl").write_bytes(b"")
        with self.assertRaises((ValueError, OSError)):
            view.assert_unchanged()

    def test_markdown_output_keeps_lineage_and_coverage_details(self):
        from doc_current import read_current, render_current

        adopted = adopt_document(self.repo)
        result = read_current(self.root, "links.json", document_id="project")
        # Render the public result shape with visible unresolved coverage metadata.
        result["coverage"]["state"] = "partial"
        result["coverage"]["not_evaluated"] = ["src/unmapped.py"]
        result["coverage"]["unmapped_sections"] = {"project": ["## Unmanaged"]}
        text = render_current(result)
        self.assertIn(adopted["result"]["event"]["event_id"], text)
        self.assertIn(adopted["manifest"]["proposal_id"], text)
        self.assertIn(adopted["bundle"] + "/document.md", text)
        self.assertIn("src/unmapped.py", text)
        self.assertIn("## Unmanaged", text)


if __name__ == "__main__":
    unittest.main()

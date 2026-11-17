"""Regression proof for PC-001/PC-002; real CLI plus preserved document bytes."""

import contextlib
import io
import json
import unittest
import uuid
from unittest.mock import patch

from knowledge_support import adopt_document, inventory, make_repo, proposal_request, review_record, run_cli


class FinalReauditTests(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(self)
        self.root = self.repo["root"]

    def accept_cli(self, proposal, review=None):
        review = review or review_record(self.repo, proposal)
        return run_cli(
            self.root,
            "knowledge",
            "accept",
            str(self.root),
            "--proposal",
            proposal["bundle"],
            "--review",
            review,
            "--session",
            str(uuid.uuid4()),
            "--key",
            "review",
            "--confirm-reviewed",
            "--apply",
            "--json",
        )

    def test_stale_accept_is_review_required_without_publishing(self):
        adopt_document(self.repo)
        proposal = proposal_request(self.repo, mode="no_impact")
        review = review_record(self.repo, proposal)
        before = inventory(self.root / "docs/living")
        (self.root / "src/catalog.py").write_bytes(b"changed after review\n")
        result = self.accept_cli(proposal, review)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["reason_code"], "stale_evidence")
        self.assertEqual(report["semantic_review"], "not_attested")
        self.assertEqual(inventory(self.root / "docs/living"), before)

    def test_stale_propose_uses_same_review_required_contract(self):
        proposal = proposal_request(self.repo)
        (self.root / "src/catalog.py").write_bytes(b"new source\n")
        result = run_cli(
            self.root,
            "knowledge",
            "propose",
            str(self.root),
            "--request",
            proposal["request_path"],
            "--output",
            "new-bundle",
            "--apply",
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["reason_code"], "stale_evidence")
        self.assertFalse((self.root / "new-bundle").exists())

    def test_parent_change_is_review_required_not_corruption(self):
        adopt_document(self.repo)
        first = proposal_request(self.repo, mode="no_impact")
        second = proposal_request(self.repo, mode="no_impact")
        self.assertEqual(self.accept_cli(first).returncode, 0)
        result = self.accept_cli(second)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["reason_code"], "parent_selection_changed")

    def test_corrupt_artifact_schema_and_missing_input_still_exit_three(self):
        proposal = proposal_request(self.repo)
        review = review_record(self.repo, proposal)
        missing = self.accept_cli(proposal, "missing-review.json")
        self.assertEqual(missing.returncode, 3)
        value = json.loads((self.root / review).read_text())
        value["schema_version"] = 999
        (self.root / review).write_text(json.dumps(value))
        malformed = self.accept_cli(proposal, review)
        self.assertEqual(malformed.returncode, 3)
        (self.root / review).write_text(json.dumps(dict(value, schema_version=1)))
        with (self.root / proposal["bundle"] / "document.md").open("ab") as f:
            f.write(b"corrupt")
        corrupted = self.accept_cli(proposal, review)
        self.assertEqual(corrupted.returncode, 3)
        self.assertEqual(json.loads(corrupted.stdout)["state"], "error")

    def test_generic_valueerror_and_io_never_masquerade_as_review(self):
        import agent_docs_kit

        for error in (ValueError("Proposal evidence expired"), PermissionError("denied")):
            with (
                self.subTest(error=type(error).__name__),
                patch("doc_revisions.create_proposal", side_effect=error),
                contextlib.redirect_stdout(io.StringIO()) as out,
            ):
                code = agent_docs_kit.main(
                    [
                        "knowledge",
                        "propose",
                        str(self.root),
                        "--request",
                        "unused.json",
                        "--output",
                        "unused",
                        "--apply",
                        "--json",
                    ]
                )
            self.assertEqual(code, 3)
            self.assertEqual(json.loads(out.getvalue())["state"], "error")
        self.assertFalse((self.root / "unused").exists())

    def test_document_and_section_markdown_disclose_origin_link_base(self):
        from doc_current import read_current, render_current

        origin = "docs/living/PROJECT.md"
        source = self.root / "PROJECT.md"
        original = source.read_bytes().replace(b"\nCatalog\n", b"\nCatalog [code](../../src/catalog.py)\n")
        target = self.root / origin
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
        self.repo["links"]["documents"][0]["origin"] = origin
        (self.root / "links.json").write_text(json.dumps(self.repo["links"]))
        adopted = adopt_document(self.repo)
        for selector in ({"document_id": "project"}, {"section_id": "project.summary"}):
            result = read_current(self.root, "links.json", **selector)
            text = render_current(result)
            self.assertIn(origin, text)
            self.assertIn("relative_link_base", text)
            self.assertIn("docs/living", text)
            self.assertIn("[code](../../src/catalog.py)", text)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual((self.root / adopted["bundle"] / "document.md").read_bytes(), original)

    def test_inventory_only_reasons_are_visible_not_semantic_evidence(self):
        from doc_current import read_current, render_current

        (self.root / "src/aux.bin").write_bytes(b"opaque")
        disposition = {"path": "src/aux.bin", "reason": "Inventory only; no explanatory claim was reviewed."}
        self.repo["links"]["inventory_only"] = [disposition]
        (self.root / "links.json").write_text(json.dumps(self.repo["links"]))
        adopt_document(self.repo)
        result = read_current(self.root, "links.json", document_id="project")
        self.assertIn("inventory_only", result["coverage"])
        self.assertEqual(result["coverage"]["inventory_only"], [disposition])
        self.assertIn("not semantic", result["coverage"]["assurance_note"].lower())
        rendered = render_current(result)
        self.assertIn(disposition["path"], rendered)
        self.assertIn(disposition["reason"], rendered)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["semantic_review"], "not_attested")

    def test_partial_coverage_keeps_local_body_but_nonzero_global_exit(self):
        from doc_current import read_current, render_current

        with (self.root / "PROJECT.md").open("ab") as f:
            f.write(b"\n## Unmapped\nThis section has not been reviewed.\n")
        adopt_document(self.repo)
        for selector in ({}, {"document_id": "project"}, {"section_id": "project.catalog"}):
            result = read_current(self.root, "links.json", **selector)
            self.assertEqual(result["coverage"]["state"], "partial")
            self.assertEqual(result["exit_code"], 1)
            self.assertIn("## Unmapped", render_current(result))
            if selector.get("document_id"):
                self.assertEqual(result["freshness"]["state"], "matches_review")
                self.assertIn("body", result)
        result = run_cli(
            self.root,
            "knowledge",
            "read",
            str(self.root),
            "--map",
            "links.json",
            "--document",
            "project",
            "--json",
        )
        self.assertEqual(result.returncode, 1)

    def test_inventory_metadata_still_obeys_output_budget(self):
        from doc_current import read_current

        (self.root / "src/aux.bin").write_bytes(b"opaque")
        self.repo["links"]["inventory_only"] = [{"path": "src/aux.bin", "reason": "x" * 33000}]
        (self.root / "links.json").write_text(json.dumps(self.repo["links"]))
        adopt_document(self.repo)
        with self.assertRaisesRegex(ValueError, "budget"):
            read_current(self.root, "links.json", document_id="project")

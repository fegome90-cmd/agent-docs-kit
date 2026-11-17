import copy
import json
import os
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from knowledge_support import (
    KIT,
    adopt_document,
    git,
    inventory,
    make_repo,
    proposal_request,
    require_module,
    review_record,
    run_cli,
)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.m = require_module(self, "doc_revisions")
        self.assertTrue(hasattr(self.m, "accept_proposal"), "Missing capability: accept_proposal")
        self.repo = make_repo(self)
        self.root = self.repo["root"]
        self.session = str(uuid.uuid4())

    def accept(self, p, review=None, key=None, session=None):
        return self.m.accept_proposal(
            self.root,
            p["bundle"],
            review or review_record(self.repo, p),
            session or self.session,
            key or str(uuid.uuid4()),
            confirm_reviewed=True,
        )

    def events(self):
        from doc_evidence import open_view

        return self.m.load_acceptances(open_view(self.root, "worktree"), "docs/living/history")

    def test_accept_requires_review_and_flags(self):
        p = proposal_request(self.repo)
        for flags in ([], ["--apply"], ["--confirm-reviewed"]):
            result = run_cli(
                self.root,
                "knowledge",
                "accept",
                str(self.root),
                "--proposal",
                p["bundle"],
                "--review",
                "missing.json",
                "--session",
                self.session,
                "--key",
                "one",
                *flags,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
        with self.assertRaises(ValueError):
            self.m.accept_proposal(
                self.root, p["bundle"], "absent", self.session, "one", confirm_reviewed=False
            )
        with self.assertRaises(ValueError):
            self.accept(p, "absent")
        self.assertEqual(self.events(), [])

    def test_accept_stale_sources_or_parent(self):
        p = proposal_request(self.repo)
        old = (self.root / "src/catalog.py").read_bytes()
        (self.root / "src/catalog.py").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.accept(p)
        self.assertEqual(self.events(), [])
        (self.root / "src/catalog.py").write_bytes(old)
        other = proposal_request(self.repo)
        self.accept(other)
        with self.assertRaises(ValueError):
            self.accept(p)
        self.assertEqual(len(self.events()), 1)

    def test_constraint_change_requires_specific_authorization(self):
        adopted = adopt_document(self.repo)
        body = adopted["body"].replace(b"\nRule\n", b"\nNew rule\n")
        p = proposal_request(self.repo, mode="revise", body=body)
        for auth in (
            [],
            [{"section_id": "project.catalog", "reference": "fixture://human", "reason": "approved"}],
        ):
            with self.assertRaises(ValueError):
                self.accept(p, review_record(self.repo, p, authorizations=auth))
        review = review_record(
            self.repo,
            p,
            authorizations=[
                {
                    "section_id": "project.runtime",
                    "reference": "fixture://human",
                    "reason": "explicit instruction",
                }
            ],
        )
        self.assertEqual(self.accept(p, review)["state"], "recorded")
        self.assertEqual(len(self.events()), 2)

    def test_accept_retry_after_source_changed(self):
        p = proposal_request(self.repo)
        review = review_record(self.repo, p)
        first = self.accept(p, review, key="retry")
        before = (self.root / "docs/living/history" / f"{self.session}.jsonl").read_bytes()
        (self.root / "src/catalog.py").write_bytes(b"after ack")
        second = self.accept(p, review, key="retry")
        self.assertEqual(second["event"], first["event"])
        self.assertEqual(second["freshness"]["state"], "stale")
        self.assertEqual((self.root / "docs/living/history" / f"{self.session}.jsonl").read_bytes(), before)
        bad = json.loads((self.root / review).read_bytes())
        bad["reason"] = "changed"
        (self.root / review).write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            self.accept(p, review, key="retry")

    def test_two_sessions_accepting_same_parent(self):
        p = proposal_request(self.repo)
        q = proposal_request(self.repo)
        processes = []
        for proposal in (p, q):
            review = review_record(self.repo, proposal)
            command = [
                sys.executable,
                "-B",
                str(KIT / "agent_docs_kit.py"),
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
                "race",
                "--apply",
                "--confirm-reviewed",
                "--json",
            ]
            processes.append(
                subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                )
            )
        results = [(proc.communicate(timeout=90), proc.returncode) for proc in processes]
        self.assertEqual(sorted(code for _, code in results), [0, 1], results)
        self.assertEqual(len(self.events()), 1)

    def fork(self, *, outside=False):
        base = adopt_document(self.repo)
        p = proposal_request(self.repo, mode="revise", body=base["body"].replace(b"\nCatalog\n", b"\nA\n"))
        q = proposal_request(self.repo, mode="revise", body=base["body"].replace(b"\nPlan\n", b"\nB\n"))
        self.accept(p, session=str(uuid.uuid4()))
        # A branch-local writer legitimately sees the common parent only.
        event = next(e for e in self.events() if e["payload"]["parents"])
        segment = next(
            path
            for path in (self.root / "docs/living/history").glob("*.jsonl")
            if event["event_id"] in path.read_text()
        )
        saved = segment.read_bytes()
        segment.unlink()
        self.accept(q, session=str(uuid.uuid4()))
        segment.write_bytes(saved)
        return base, p, q

    def test_merged_competing_heads_are_conflict(self):
        self.fork()
        events = self.events()
        project = self.repo["links"]["project_id"]
        for order in (events, list(reversed(events))):
            result = self.m.resolve_acceptances(order, project, "project")
            self.assertEqual(result["selection"], "conflict")
            self.assertEqual(len(result["heads"]), 2)
            self.assertIsNone(result["selected"])

    def test_explicit_resolution_keeps_history(self):
        base, p, q = self.fork()
        before = inventory(self.root)
        resolution = proposal_request(
            self.repo, mode="resolve", body=base["body"].replace(b"\nCatalog\n", b"\nResolved\n")
        )
        self.assertEqual(len(resolution["manifest"]["expected_parents"]), 2)
        self.accept(resolution)
        events = self.events()
        result = self.m.resolve_acceptances(events, self.repo["links"]["project_id"], "project")
        self.assertEqual(result["selection"], "accepted")
        self.assertEqual(len(events), 4)
        for filename, sha in before.items():
            if "/revisions/" in filename:
                self.assertEqual(inventory(self.root)[filename], sha)

    def test_orphan_reference_cycle_and_corruption(self):
        adopt_document(self.repo)
        events = self.events()
        project = self.repo["links"]["project_id"]
        for parents in ([str(uuid.uuid4())], [events[0]["event_id"]]):
            bad = copy.deepcopy(events)
            bad[0]["payload"]["parents"] = parents
            with self.assertRaises(ValueError):
                self.m.resolve_acceptances(bad, project, "project")
        p = next((self.root / "docs/living/revisions").glob("*/document.md"))
        p.write_bytes(p.read_bytes() + b"corrupt")
        with self.assertRaises(ValueError):
            self.events()

    def test_crash_between_review_file_and_event(self):
        import history

        p = proposal_request(self.repo)
        review = review_record(self.repo, p)
        with patch.object(history, "append_event", side_effect=OSError("unknown write outcome")):
            with self.assertRaises(OSError):
                self.accept(p, review, key="retry")
        preserved = self.root / "docs/living/reviews" / self.session / (self.m.digest(b"retry") + ".json")
        self.assertTrue(preserved.exists())
        raw = preserved.read_bytes()
        self.assertEqual(self.events(), [])
        self.accept(p, review, key="retry")
        self.assertEqual(preserved.read_bytes(), raw)
        self.assertEqual(len(self.events()), 1)

    def test_mutation_after_append_returns_recorded_but_stale(self):
        import history

        original = history.append_event
        p = proposal_request(self.repo)

        def mutate(*args, **kwargs):
            event = original(*args, **kwargs)
            (self.root / "src/catalog.py").write_bytes(b"post append")
            return event

        with patch.object(history, "append_event", side_effect=mutate):
            result = self.accept(p)
        self.assertEqual(result["state"], "recorded_but_stale")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(len(self.events()), 1)

    def test_agent_review_never_claims_human_approval(self):
        p = proposal_request(self.repo)
        result = self.accept(p)
        self.assertEqual(
            result["event"]["payload"]["reviewer_declared"], {"kind": "agent", "id": "synthetic-reviewer"}
        )
        self.assertEqual(result["semantic_review"], "not_attested")

    def test_acceptance_artifacts_bound_to_bundle_and_review(self):
        adopt_document(self.repo)
        event = self.events()[0]
        filename = next((self.root / "docs/living/history").glob("*.jsonl"))
        old = filename.read_bytes()
        for ref in ("revision", "review"):
            bad = copy.deepcopy(event)
            bad["payload"][ref]["sha256"] = "0" * 64
            filename.write_bytes(self.m.canonical(bad) + b"\n")
            with self.assertRaises(ValueError):
                self.events()
        filename.write_bytes(old)

    def test_index_origin_control_inputs_never_replace_staged_sources(self):
        staged = (self.root / "src/catalog.py").read_bytes()
        (self.root / "src/catalog.py").write_bytes(b"local B")
        p = proposal_request(self.repo, view="index")
        (self.root / "links.json").write_bytes(b"local map differs")
        (self.root / "snapshot.json").write_bytes(b"local snapshot differs")
        result = self.accept(p)
        self.assertEqual(result["state"], "recorded")
        from doc_evidence import digest, open_view

        source = next(f for f in p["request"]["evidence"]["files"] if f["path"] == "src/catalog.py")
        self.assertEqual(source["sha256"], digest(staged))
        self.assertNotEqual(source["sha256"], digest(b"local B"))
        self.assertEqual(self.m.load_acceptances(open_view(self.root, "index"), "docs/living/history"), [])
        git(self.root, "add", "docs")
        self.assertEqual(
            len(self.m.load_acceptances(open_view(self.root, "index"), "docs/living/history")), 1
        )

    def test_partial_review_cannot_refresh_other_stale_sections(self):
        adopted = adopt_document(self.repo)
        before = self.events()
        (self.root / "src/catalog.py").write_bytes(b"new")
        with self.assertRaisesRegex(ValueError, "Incomplete section review"):
            proposal_request(self.repo, mode="revise", reviewed=["project.catalog"], body=adopted["body"])
        self.assertEqual(self.events(), before)

    def test_map_transition_full_review_and_old_constraints(self):
        adopted = adopt_document(self.repo)
        old = (self.root / "links.json").read_bytes()
        new = copy.deepcopy(self.repo["links"])
        new["documents"][0]["sections"][0]["selector"]["heading"] = "## Catalog v2"
        new["documents"][0]["sections"][2]["claim_kind"] = "observed"
        (self.root / "new-links.json").write_text(json.dumps(new))
        # Keep old accepted map bytes: transition uses a NEW path.
        from doc_evidence import capture_evidence, open_view

        context, _ = self.m.resolved_context(open_view(self.root, "worktree"), new)
        envelope = capture_evidence(
            open_view(self.root, "worktree"),
            new,
            "snapshot.json",
            "new-links.json",
            context["base_documents"],
            context["heads"],
            proposal_ids=context["proposal_ids"],
        )
        body = adopted["body"].replace(b"## Catalog", b"## Catalog v2").replace(b"\nRule\n", b"\nNew rule\n")
        request_id = str(uuid.uuid4())
        (self.root / "migration.md").write_bytes(body)
        req = {
            "schema": "agent-docs-kit/proposal-request",
            "schema_version": 1,
            "request_id": request_id,
            "project_id": new["project_id"],
            "document_id": "project",
            "mode": "revise",
            "candidate_path": "migration.md",
            "expected_parents": context["heads"]["project"],
            "reviewed_sections": ["project.catalog"],
            "reason": "Explicit structural migration",
            "evidence": envelope,
        }
        (self.root / "migration.json").write_text(json.dumps(req))
        with self.assertRaises(ValueError):
            self.m.create_proposal(self.root, "migration.json", "migration")
        req["reviewed_sections"] = sorted(s["id"] for s in new["documents"][0]["sections"])
        (self.root / "migration.json").write_text(json.dumps(req))
        manifest = self.m.create_proposal(self.root, "migration.json", "migration")
        p = {"manifest": manifest, "bundle": "migration"}
        with self.assertRaises(ValueError):
            self.accept(p)
        review = review_record(
            self.repo,
            p,
            authorizations=[
                {
                    "section_id": "project.runtime",
                    "reference": "fixture://human",
                    "reason": "Explicit replacement",
                }
            ],
        )
        self.accept(p, review)
        self.assertEqual((self.root / "links.json").read_bytes(), old)

    def test_resolve_subset_refused_for_conflict_heads(self):
        base, p, q = self.fork()
        with self.assertRaises(ValueError):
            proposal_request(self.repo, mode="resolve", reviewed=["project.catalog"], body=base["body"])

    def test_unknown_append_outcome_preserves_event_and_retry(self):
        import history

        original = history.append_event
        p = proposal_request(self.repo)
        review = review_record(self.repo, p)

        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("lost acknowledgement after durable append")

        with patch.object(history, "append_event", side_effect=lost), self.assertRaises(OSError):
            self.accept(p, review, key="unknown")
        before = self.events()
        self.assertEqual(len(before), 1)
        (self.root / "src/catalog.py").write_bytes(b"changed after lost acknowledgement")
        retried = self.accept(p, review, key="unknown")
        self.assertEqual(retried["event"], before[0])
        self.assertEqual(retried["freshness"]["state"], "stale")
        self.assertEqual(self.events(), before)

    def test_nested_unknown_review_schemas_fail_closed(self):
        p = proposal_request(self.repo)
        filename = review_record(self.repo, p)
        valid = json.loads((self.root / filename).read_bytes())
        mutations = [
            lambda r: r.update(schema="unknown"),
            lambda r: r.update(schema_version=True),
            lambda r: r["reviewer"].update(extra=True),
            lambda r: r["reviewer"].update(id=""),
            lambda r: r.update(decision="approve"),
            lambda r: r.update(
                constraint_authorizations=[
                    {"section_id": "project.runtime", "reference": "x", "reason": "x", "extra": 1}
                ]
            ),
        ]
        for mutate in mutations:
            bad = copy.deepcopy(valid)
            mutate(bad)
            (self.root / filename).write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                self.accept(p, filename)
        self.assertEqual(self.events(), [])

    def test_retry_uses_history_writer_for_durable_acknowledgement(self):
        import history

        p = proposal_request(self.repo)
        review = review_record(self.repo, p)
        first = self.accept(p, review, key="durable-retry")
        segment = self.root / "docs/living/history" / (self.session + ".jsonl")
        before = segment.read_bytes()
        with patch.object(history, "_sync", wraps=history._sync) as sync:
            retried = self.accept(p, review, key="durable-retry")
        self.assertGreaterEqual(
            sync.call_count, 1, "A retry must fsync through the unchanged v1 history writer before ACK"
        )
        self.assertEqual(retried["event"], first["event"])
        self.assertEqual(segment.read_bytes(), before)

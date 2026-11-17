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

from knowledge_support import KIT, inventory, make_repo, require_module, run_cli


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.m = require_module(self, "doc_impact")
        self.e = require_module(self, "doc_evidence")
        self.repo = make_repo(self)
        self.root = self.repo["root"]
        self.links = self.repo["links"]
        self.reviewed = self.capture()

    def capture(self, snapshot="snapshot.json"):
        view = self.e.open_view(self.root, "worktree")
        return self.e.capture_evidence(
            view,
            self.links,
            snapshot,
            "links.json",
            {"project": view.read("PROJECT.md", maximum=1048576)},
            {"project": []},
        )

    def impact(self, current=None):
        return self.m.compute_impact(
            self.links, {"project": {"reviewed_inputs": self.reviewed}}, current or self.capture()
        )

    def test_catalog_change_selects_dependents_only(self):
        (self.root / "src/catalog.py").write_bytes(b"changed\n")
        result = self.impact()
        self.assertEqual(result["review_queue"], ["project.catalog", "project.summary"])
        records = {r["section_id"]: r for r in result["sections"]}
        self.assertIn(
            {"code": "direct_change", "path": "src/catalog.py", "via": None},
            records["project.catalog"]["reasons"],
        )
        self.assertIn(
            {"code": "dependency_change", "path": "src/catalog.py", "via": "project.catalog"},
            records["project.summary"]["reasons"],
        )
        self.assertEqual(records["project.runtime"]["state"], "no_linked_change")

    def test_diamond_keeps_all_causes(self):
        sections = self.links["documents"][0]["sections"]
        sections[:] = [
            dict(sections[0], id="a", depends_on=[]),
            dict(sections[1], id="b", depends_on=["a"]),
            dict(sections[1], id="c", depends_on=["a"]),
            dict(sections[1], id="d", depends_on=["b", "c"]),
        ]
        (self.root / "links.json").write_text(json.dumps(self.links))
        self.reviewed = self.capture()
        (self.root / "src/catalog.py").write_bytes(b"changed\n")
        current = self.capture()
        result = self.impact(current)
        self.assertEqual(result["review_queue"], ["a", "b", "c", "d"])
        last = next(s for s in result["sections"] if s["section_id"] == "d")
        self.assertEqual({r["via"] for r in last["reasons"]}, {"b", "c"})
        self.assertEqual(result, self.impact(current))

    def test_bootstrap_is_unverified(self):
        result = self.m.compute_impact(self.links, {}, self.capture())
        self.assertEqual(result["state"], "review_required")
        self.assertTrue(all(s["state"] == "unverified" for s in result["sections"]))
        self.assertEqual(result["semantic_review"], "not_attested")
        checked = self.m.check_knowledge(self.root, "links.json", "snapshot.json")
        self.assertEqual(checked["review_queue"], ["project.catalog", "project.runtime", "project.summary"])
        self.assertEqual(checked["selection"], {"project": "unaccepted"})

    def test_removed_source_and_unmapped_source(self):
        (self.root / "src/catalog.py").unlink()
        (self.root / "src/unknown").write_bytes(b"\xff")
        result = self.impact()
        self.assertEqual(result["review_queue"], ["project.catalog", "project.summary"])
        self.assertEqual(result["unmapped_sources"], ["src/unknown"])
        self.assertEqual(result["coverage"]["state"], "partial")
        record = next(f for f in result["evidence"]["files"] if f["path"] == "src/catalog.py")
        self.assertFalse(record["present"])

    def test_new_snapshot_does_not_erase_review_debt(self):
        original = copy.deepcopy(self.reviewed)
        (self.root / "src/catalog.py").write_bytes(b"changed\n")
        from living_docs import snapshot

        snapshot(self.root, ["src"], ["PROJECT.md"], self.root / "new.json", discover_roots=["src"])
        result = self.impact(self.capture("new.json"))
        self.assertEqual(result["review_queue"], ["project.catalog", "project.summary"])
        self.assertEqual(self.reviewed, original)

    def test_constraint_is_not_rewritten(self):
        prior = inventory(self.root)
        (self.root / "src/runtime.py").write_bytes(b"runtime = False\n")
        result = self.impact()
        self.assertEqual(result["review_queue"], ["project.runtime"])
        section = next(s for s in result["sections"] if s["section_id"] == "project.runtime")
        self.assertIn(
            {"code": "potential_constraint_conflict", "path": "src/runtime.py", "via": None},
            section["reasons"],
        )
        self.assertEqual(inventory(self.root)["PROJECT.md"], prior["PROJECT.md"])
        self.assertEqual(self.links["documents"][0]["sections"][2]["claim_kind"], "constraint")

    def test_cli_check_does_not_load_llm_or_write(self):
        before = inventory(self.root)
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
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["schema"], "agent-docs-kit/impact")
        self.assertEqual(self.e.evidence_id(report["evidence"]), report["evidence"]["evidence_id"])
        self.assertEqual(inventory(self.root), before)
        with tempfile.TemporaryDirectory() as temp:
            kit = Path(temp)
            for file in KIT.glob("*.py"):
                shutil.copyfile(file, kit / file.name)
            env = dict(os.environ)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            for args in [["--help"], ["knowledge", "--help"], ["knowledge", "check", "--help"]]:
                proc = subprocess.run(
                    [sys.executable, str(kit / "agent_docs_kit.py"), *args],
                    cwd=kit,
                    env=env,
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse((kit / "__pycache__").exists())
            script = (
                "import agent_docs_kit as a, sys; "
                'a.main(["knowledge","check",sys.argv[1],"--map","links.json","--snapshot","snapshot.json","--json"]); '
                'assert not ({"portalocker","openai","anthropic"} & sys.modules.keys())'
            )
            proc = subprocess.run(
                [sys.executable, "-B", "-c", script, str(self.root)],
                cwd=kit,
                env=env,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_unmapped_headings_and_inventory_dispositions(self):
        with (self.root / "PROJECT.md").open("ab") as f:
            f.write(b"\n## Unmanaged\nunknown\n")
        (self.root / "src/other").write_bytes(b"inventory")
        self.links["inventory_only"] = [{"path": "src/other", "reason": "inventory only"}]
        (self.root / "links.json").write_text(json.dumps(self.links))
        report = self.m.check_knowledge(self.root, "links.json", "snapshot.json")
        self.assertEqual(report["unmapped_sections"], {"project": ["## Unmanaged"]})
        self.assertEqual(report["unmapped_sources"], [])
        self.assertEqual(report["coverage"]["inventory_only"][0]["path"], "src/other")
        self.assertEqual(report["state"], "review_required")

    def test_cli_errors_exports_and_root_relative_paths(self):
        args = [
            "knowledge",
            "check",
            str(self.root),
            "--map",
            "links.json",
            "--snapshot",
            "snapshot.json",
            "--json",
        ]
        result = run_cli(self.root.parent, *args)
        self.assertEqual(result.returncode, 1, result.stderr)
        before = inventory(self.root)
        self.assertEqual(run_cli(self.root, *args, "--output", "report.json").returncode, 2)
        self.assertEqual(inventory(self.root), before)
        exported = run_cli(self.root, *args, "--output", "report.json", "--apply")
        self.assertEqual(exported.returncode, 1, exported.stderr)
        self.assertEqual(
            json.loads((self.root / "report.json").read_bytes())["schema"], "agent-docs-kit/impact"
        )
        old = (self.root / "report.json").read_bytes()
        self.assertEqual(run_cli(self.root, *args, "--output", "report.json", "--apply").returncode, 1)
        self.assertEqual((self.root / "report.json").read_bytes(), old)
        for map_path in ["../links.json", "missing.json"]:
            invalid = args.copy()
            invalid[invalid.index("links.json")] = map_path
            result = run_cli(self.root, *invalid)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(json.loads(result.stdout)["state"], "error")

    def test_invalid_reviewed_evidence_never_means_clean(self):
        bad = copy.deepcopy(self.reviewed)
        bad["files"][0]["sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            self.m.compute_impact(self.links, {"project": bad}, self.capture())
        with self.assertRaises(ValueError):
            self.m.compute_impact(self.links, {"unknown": self.reviewed}, self.capture())

    def test_selected_binding_must_name_a_mapped_document(self):
        selected = {
            "document_id": "unknown",
            "path": "PROJECT.md",
            "sha256": hashlib.sha256((self.root / "PROJECT.md").read_bytes()).hexdigest(),
        }
        with self.assertRaises(ValueError):
            self.e.revalidate(
                self.e.open_view(self.root, "worktree"), self.reviewed, phase="selected", selected=selected
            )

    def test_transitive_external_content_not_unrelated_heads(self):
        foreign = copy.deepcopy(self.links["documents"][0])
        foreign["id"] = "other"
        foreign["origin"] = "OTHER.md"
        for section in foreign["sections"]:
            section["id"] = section["id"].replace("project.", "other.")
            section["depends_on"] = [s.replace("project.", "other.") for s in section["depends_on"]]
        self.links["documents"].append(foreign)
        (self.root / "OTHER.md").write_bytes((self.root / "PROJECT.md").read_bytes())
        heads = {"project": [], "other": ["12345678-1234-4234-8234-123456789abc"]}
        bases = {
            "project": (self.root / "PROJECT.md").read_bytes(),
            "other": (self.root / "OTHER.md").read_bytes(),
        }
        context = {
            "base_documents": bases,
            "heads": heads,
            "proposal_ids": {"project": None, "other": "a" * 64},
            "selection": {"project": "unaccepted", "other": "accepted"},
        }
        selected = {
            "document_id": "project",
            "path": "PROJECT.md",
            "sha256": hashlib.sha256(bases["project"]).hexdigest(),
        }
        for dependent in (False, True):
            if dependent:
                self.links["documents"][0]["sections"][1]["depends_on"].append("other.summary")
            (self.root / "links.json").write_text(json.dumps(self.links))
            envelope = self.e.capture_evidence(
                self.e.open_view(self.root, "worktree"),
                self.links,
                "snapshot.json",
                "links.json",
                bases,
                heads,
                proposal_ids=context["proposal_ids"],
            )
            changed = copy.deepcopy(context)
            changed["heads"]["other"] = ["22345678-1234-4234-8234-123456789abc"]
            changed["proposal_ids"]["other"] = "b" * 64
            self.assertEqual(
                self.e.revalidate(
                    self.e.open_view(self.root, "worktree"),
                    envelope,
                    phase="selected",
                    selected=selected,
                    resolved=changed,
                )["state"],
                "matches_review",
            )
            changed["base_documents"]["other"] += b"new content"
            result = self.e.revalidate(
                self.e.open_view(self.root, "worktree"),
                envelope,
                phase="selected",
                selected=selected,
                resolved=changed,
            )
            self.assertEqual(result["state"], "stale" if dependent else "matches_review")
            changed = copy.deepcopy(context)
            changed["selection"]["other"] = "unaccepted"
            changed["heads"]["other"] = []
            changed["proposal_ids"]["other"] = None
            result = self.e.revalidate(
                self.e.open_view(self.root, "worktree"),
                envelope,
                phase="selected",
                selected=selected,
                resolved=changed,
            )
            self.assertEqual(result["state"], "stale" if dependent else "matches_review")

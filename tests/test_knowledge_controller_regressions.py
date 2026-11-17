"""Controller reproductions; no source normalization and no hidden coverage gaps."""

import json
import unittest
from unittest.mock import patch

from knowledge_support import link_map, make_repo

import doc_evidence
import doc_links
from living_docs import snapshot


class ControllerRegressions(unittest.TestCase):
    def test_unicode_separators_cannot_invent_atx_lines(self):
        document = link_map()["documents"][0]
        document["sections"] = [document["sections"][0]]
        for separator in ["\u2028", "\u2029", "\u0085", "\v", "\f"]:
            with self.subTest(separator=repr(separator)):
                raw = ("# Project\ninline" + separator + "## Catalog\ntext\n").encode("utf-8")
                with self.assertRaisesRegex(ValueError, "Missing|ambiguous"):
                    doc_links.section_spans(raw, document)
        raw = "# Project\ninline\u2028text\n## Catalog\ntexto ñ\n".encode("utf-8")
        spans = doc_links.section_spans(raw, document)["spans"]
        start, end = spans["project.catalog"]
        self.assertEqual(raw[start:end], "## Catalog\ntexto ñ\n".encode())

    def test_inventory_only_is_required_on_wire(self):
        value = link_map()
        del value["inventory_only"]
        with self.assertRaisesRegex(ValueError, "missing|Missing|schema"):
            doc_links.parse_links(json.dumps(value).encode())

    def test_selected_file_outside_discovery_roots_remains_unevaluated(self):
        repo = make_repo(self)
        root = repo["root"]
        (root / "package.json").write_bytes(b'{"name":"fixture"}\n')
        snapshot(
            root, ["src", "package.json"], ["PROJECT.md"], root / "expanded.json", discover_roots=["src"]
        )
        view = doc_evidence.open_view(root, "worktree")
        envelope = doc_evidence.capture_evidence(
            view,
            repo["links"],
            "expanded.json",
            "links.json",
            {"project": (root / "PROJECT.md").read_bytes()},
            {"project": []},
        )
        self.assertIn("package.json", envelope["inventory"]["eligible_paths"])
        self.assertIn("package.json", envelope["inventory"]["not_evaluated"])

    def test_fingerprint_source_limit_is_enforced_in_both_views(self):
        repo = make_repo(self)
        root = repo["root"]
        for kind in ["index", "worktree"]:
            with self.subTest(kind=kind), patch.object(doc_evidence, "MAX_SOURCE_BYTES", 4, create=True):
                view = doc_evidence.open_view(root, kind)
                with self.assertRaises(ValueError):
                    view.fingerprint("src/catalog.py")


if __name__ == "__main__":
    unittest.main()

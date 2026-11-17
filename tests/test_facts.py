"""Behavioral checks for exact facts, strict inputs, and read-only evaluation."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import facts


def sample() -> dict:
    return {
        "schema_version": 1,
        "facts": [{
            "id": "version",
            "canonical": {"file": "src/version.py", "pattern": r'(?m)^VERSION = "([^"]+)"$'},
            "copies": [{"file": "README.md", "pattern": r"(?m)^Version: (.+)$"}],
        }],
    }


class FactsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fact evidence ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "project with spaces"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src/version.py").write_text('VERSION = "1.2.3+build"\n', encoding="utf-8")
        (self.root / "README.md").write_text("# Project\nVersion: 1.2.3+build\n", encoding="utf-8")
        self.manifest = self.root / "facts.json"
        self.write_manifest(sample())

    def write_manifest(self, value: dict) -> None:
        self.manifest.write_text(json.dumps(value), encoding="utf-8")

    def report(self) -> dict:
        return facts.check_facts(self.root, self.manifest)

    def inventory(self) -> dict[str, tuple[str, str | None]]:
        return {
            path.relative_to(self.root).as_posix(): (
                "directory" if path.is_dir() else "file",
                None if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in self.root.rglob("*")
        }

    def test_exact_captures_have_relative_provenance_without_semantic_attestation(self) -> None:
        report = self.report()
        self.assertEqual(report["state"], "in_sync")
        self.assertEqual(report["semantic_review"], "not_attested")
        fact = report["facts"][0]
        self.assertEqual(fact["canonical"], {
            "file": "src/version.py", "line": 1, "value": "1.2.3+build",
            "match_count": 1, "reason": "canonical",
        })
        self.assertEqual(fact["copies"], [{
            "file": "README.md", "line": 2, "value": "1.2.3+build",
            "match_count": 1, "reason": "match",
        }])
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertEqual(facts.check_facts(self.root, Path("facts.json")), report)

    def test_no_numeric_whitespace_or_trailing_plus_normalization(self) -> None:
        manifest = sample()
        for canonical, copied in (("10+", "10"), ("01", "1"), ("1,000", "1000"),
                                  ("1.0", "1"), (" 10 ", "10")):
            with self.subTest(canonical=canonical, copy=copied):
                text = {"src/version.py": f'VERSION = "{canonical}"',
                        "README.md": f"Version: {copied}"}
                result = facts.check_manifest(manifest, text.get)
                self.assertEqual(result["state"], "review_required")
                self.assertEqual(result["facts"][0]["canonical"]["value"], canonical)
                self.assertEqual(result["facts"][0]["copies"][0]["reason"], "mismatch")

    def test_missing_file_and_missing_match_are_distinct(self) -> None:
        (self.root / "README.md").unlink()
        result = self.report()
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(result["facts"][0]["copies"][0]["reason"], "missing_file")
        (self.root / "README.md").write_text("No declared version here\n", encoding="utf-8")
        result = self.report()
        self.assertEqual(result["facts"][0]["copies"][0]["reason"], "missing_match")

    def test_ambiguous_copies_do_not_select_the_first_even_when_values_are_equal(self) -> None:
        (self.root / "README.md").write_text(
            "Version: 1.2.3+build\nVersion: 1.2.3+build\n", encoding="utf-8")
        result = self.report()
        observed = result["facts"][0]["copies"][0]
        self.assertEqual(result["state"], "review_required")
        self.assertEqual(observed["reason"], "ambiguous")
        self.assertEqual(observed["match_count"], 2)
        self.assertIsNone(observed["value"])
        self.assertIsNone(observed["line"])

    def test_missing_or_ambiguous_canonical_still_reports_every_copy(self) -> None:
        manifest = sample()
        manifest["facts"][0]["copies"].append({"file": "missing.md", "pattern": r"(.+)"})
        self.write_manifest(manifest)
        canonical = self.root / "src/version.py"
        for content, expected in ((None, "missing_file"), ("other text", "missing_match"),
                                  ('VERSION = "first"\nVERSION = "second"\n', "ambiguous")):
            with self.subTest(expected=expected):
                if content is None:
                    canonical.unlink()
                else:
                    canonical.write_text(content, encoding="utf-8")
                report = self.report()
                fact = report["facts"][0]
                self.assertEqual(report["state"], "review_required")
                self.assertEqual(fact["canonical"]["reason"], expected)
                self.assertIsNone(fact["canonical"]["value"])
                self.assertEqual([item["reason"] for item in fact["copies"]],
                                 ["canonical_unavailable", "missing_file"])
                self.assertEqual(fact["copies"][0]["value"], "1.2.3+build")

    def test_multiline_capture_reports_capture_line(self) -> None:
        self.assertEqual(facts.extract("header\nvalue: exact+\n", r"header\nvalue: (.+)"),
                         ("exact+", 2, 1))

    def test_different_patterns_cannot_compare_the_same_occurrence_to_itself(self) -> None:
        manifest = {"schema_version": 1, "facts": [{
            "id": "version", "canonical": {"file": "README.md", "pattern": r"Version: (1\.0)"},
            "copies": [{"file": "README.md", "pattern": r"Version: (\S+)"}],
        }]}
        with self.assertRaisesRegex(ValueError, "misma ocurrencia"):
            facts.check_manifest(manifest, lambda _: "Version: 1.0\n")

    def test_distinct_occurrences_in_same_file_are_valid_copies(self) -> None:
        manifest = {"schema_version": 1, "facts": [{
            "id": "version", "canonical": {"file": "README.md", "pattern": r"Version: (\S+)"},
            "copies": [{"file": "README.md", "pattern": r"Install: (\S+)"}],
        }]}
        report = facts.check_manifest(manifest, lambda _: "Version: 1.0\nInstall: 1.0\n")
        self.assertEqual(report["state"], "in_sync")

    def test_optional_nonparticipating_capture_cannot_succeed(self) -> None:
        manifest = sample()
        manifest["facts"][0]["canonical"]["pattern"] = r"missing(value)?"
        report = facts.check_manifest(manifest, lambda name: "missing" if name.endswith(".py")
                                      else "Version: ")
        self.assertEqual(report["state"], "review_required")
        self.assertEqual(report["facts"][0]["canonical"]["reason"], "missing_capture")
        self.assertEqual(facts.extract("missing", r"missing(value)?"), (None, None, 1))

    def test_missing_capture_is_not_confused_with_explicit_empty_capture(self) -> None:
        self.assertEqual(facts.extract("", r"()"), ("", 1, 1))

    def test_files_are_cached_but_every_locator_is_evaluated(self) -> None:
        manifest = sample()
        manifest["facts"][0]["copies"].append({"file": "README.md", "pattern": r"Again: (.+)"})
        text = {"src/version.py": 'VERSION = "same"',
                "README.md": "Version: same\nAgain: other"}
        reads: list[str] = []

        def reader(filename: str) -> str | None:
            reads.append(filename)
            return text.get(filename)

        result = facts.check_manifest(manifest, reader)
        self.assertEqual(reads, ["src/version.py", "README.md"])
        self.assertEqual([copy["reason"] for copy in result["facts"][0]["copies"]],
                         ["match", "mismatch"])

    def test_invalid_patterns_are_rejected_before_any_declared_read(self) -> None:
        for pattern in (r"value", r"(one)(two)", r"(", 4, None):
            with self.subTest(pattern=pattern):
                manifest = sample()
                manifest["facts"][0]["copies"][0]["pattern"] = pattern
                with self.assertRaises(ValueError):
                    facts.check_manifest(manifest,
                                         lambda name: self.fail("invalid schema read a file"))

    def test_no_empty_fact_lists_or_copy_lists(self) -> None:
        for manifest in ({"schema_version": 1, "facts": []}, sample()):
            if manifest["facts"]:
                manifest["facts"][0]["copies"] = []
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                facts.check_manifest(manifest, lambda name: "")

    def test_duplicate_ids_and_duplicate_or_tautological_locators_rejected(self) -> None:
        repeated_ids = sample()
        repeated_ids["facts"].append(copy.deepcopy(repeated_ids["facts"][0]))
        repeated_copy = sample()
        repeated_copy["facts"][0]["copies"] *= 2
        self_copy = sample()
        self_copy["facts"][0]["copies"] = [self_copy["facts"][0]["canonical"].copy()]
        for manifest in (repeated_ids, repeated_copy, self_copy):
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                facts.validate_manifest(manifest)

    def test_wrong_schema_types_and_unknown_fields_are_rejected(self) -> None:
        variants = [None, [], {}, {"schema_version": True, "facts": sample()["facts"]},
                    {"schema_version": 2, "facts": sample()["facts"]}]
        for value in ("", " ", 3, None):
            invalid = sample()
            invalid["facts"][0]["id"] = value
            variants.append(invalid)
        for target in ("root", "fact", "locator"):
            invalid = sample()
            container = (invalid if target == "root" else invalid["facts"][0]
                         if target == "fact" else invalid["facts"][0]["canonical"])
            container["unsupported"] = True
            variants.append(invalid)
        for invalid in variants:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                facts.validate_manifest(invalid)

    def test_duplicate_json_keys_are_rejected_at_each_level(self) -> None:
        serialized = json.dumps(sample())
        variants = [
            serialized.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
            serialized.replace('"id": "version"', '"id": "version", "id": "version"'),
            serialized.replace('"file": "README.md"',
                               '"file": "README.md", "file": "README.md"'),
        ]
        for text in variants:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "duplicada"):
                facts.parse_manifest(text)

    def test_invalid_json_and_nonstandard_constants_are_rejected(self) -> None:
        for text in ("", "{} trailing", "[]", '{"schema_version": NaN, "facts": []}',
                     '{"schema_version": Infinity, "facts": []}',
                     '{"schema_version": -Infinity, "facts": []}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                facts.parse_manifest(text)

    def test_declared_paths_cannot_escape_or_use_noncanonical_aliases(self) -> None:
        for name in ("../outside.md", "/etc/passwd", "a/../../outside.md", "C:/outside.md",
                     "C:outside.md", "a\\outside.md", "./README.md", "a//b.md", "a/./b.md",
                     ".", "", "README.md/", "a\x00b"):
            with self.subTest(name=name):
                manifest = sample()
                manifest["facts"][0]["copies"][0]["file"] = name
                with self.assertRaises(ValueError):
                    facts.check_manifest(manifest,
                                         lambda name: self.fail("unsafe path reached reader"))

    def test_invalid_utf8_in_manifest_and_declared_file_is_rejected(self) -> None:
        for path in (self.manifest, self.root / "README.md"):
            with self.subTest(path=path):
                before = path.read_bytes()
                path.write_bytes(b"\xff\xfe")
                with self.assertRaisesRegex(ValueError, "UTF-8"):
                    self.report()
                path.write_bytes(before)

    def test_pure_reader_text_contract_and_size_limit_are_enforced(self) -> None:
        for value in (b"not text", "\ud800", "x" * (facts.MAX_FILE_BYTES + 1),
                      "é" * (facts.MAX_FILE_BYTES // 2 + 1)):
            with self.subTest(kind=type(value).__name__, size=len(value)):
                with self.assertRaises(ValueError):
                    facts.check_manifest(sample(), lambda filename: value)

    def test_file_and_manifest_size_limits(self) -> None:
        for path in (self.manifest, self.root / "README.md"):
            with self.subTest(path=path):
                before = path.read_bytes()
                path.write_bytes(b"a" * (facts.MAX_FILE_BYTES + 1))
                with self.assertRaisesRegex(ValueError, "1 MiB"):
                    self.report()
                path.write_bytes(before)

    def test_missing_manifest_and_nonregular_documents_are_errors(self) -> None:
        with self.assertRaises(ValueError):
            facts.check_facts(self.root, Path("absent.json"))
        (self.root / "README.md").unlink()
        (self.root / "README.md").mkdir()
        with self.assertRaises(ValueError):
            self.report()

    def test_manifest_outside_root_or_traversal_is_rejected(self) -> None:
        outside = self.base / "outside.json"
        outside.write_text(json.dumps(sample()), encoding="utf-8")
        for path in (outside, Path("../outside.json")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                facts.check_facts(self.root, path)

    def test_symlink_files_directories_manifests_and_root_are_rejected(self) -> None:
        original = self.root / "README.md"
        saved = original.read_bytes()
        original.unlink()
        original.symlink_to(self.root / "src/version.py")
        with self.assertRaises(ValueError):
            self.report()
        original.unlink()
        original.write_bytes(saved)
        linked = self.root / "linked"
        linked.symlink_to(self.root / "src", target_is_directory=True)
        manifest = sample()
        manifest["facts"][0]["canonical"]["file"] = "linked/version.py"
        self.write_manifest(manifest)
        with self.assertRaises(ValueError):
            self.report()
        self.write_manifest(sample())
        linked_manifest = self.root / "linked.json"
        linked_manifest.symlink_to(self.manifest)
        with self.assertRaises(ValueError):
            facts.check_facts(self.root, linked_manifest)
        linked_root = self.base / "root-link"
        linked_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            facts.check_facts(linked_root, Path("facts.json"))

    def test_dangling_symlink_is_not_reported_as_ordinary_missing_file(self) -> None:
        path = self.root / "README.md"
        path.unlink()
        path.symlink_to(self.base / "absent.md")
        with self.assertRaises(ValueError):
            self.report()

    def test_reads_never_execute_project_code_and_preserve_every_file(self) -> None:
        source = self.root / "src/version.py"
        source.write_text(
            'VERSION = "1.2.3+build"\n'
            'from pathlib import Path\n'
            'Path("MUST_NOT_EXIST").write_text("executed")\n'
            'raise RuntimeError("project code executed")\n', encoding="utf-8")
        before = self.inventory()
        with patch("subprocess.run", side_effect=AssertionError("no project execution")):
            self.assertEqual(self.report()["state"], "in_sync")
        self.assertEqual(before, self.inventory())
        self.assertFalse((self.root / "MUST_NOT_EXIST").exists())

    def test_mismatched_and_missing_files_are_never_updated(self) -> None:
        manifest = sample()
        manifest["facts"][0]["copies"].append({"file": "missing.md", "pattern": r"(.+)"})
        self.write_manifest(manifest)
        (self.root / "README.md").write_text("Version: old\n", encoding="utf-8")
        before = self.inventory()
        self.assertEqual(self.report()["state"], "review_required")
        self.assertEqual(before, self.inventory())

    def test_source_license_is_exact_and_attribution_is_pinned(self) -> None:
        license = (KIT / "licenses/ssot-check-MIT.txt").read_bytes()
        self.assertEqual(hashlib.sha1(b"blob " + str(len(license)).encode("ascii") + b"\0" + license)
                         .hexdigest(), "2fbcd55c452a545d8fe75efeb3bae0c6ab01d1cc")
        self.assertIn("5106ac6b22daad7e37d39139f6261094549df184", facts.__doc__)


if __name__ == "__main__":
    unittest.main()

"""Bounded validation helper tests; never run the distribution suite recursively."""

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


class ValidationHelpers(unittest.TestCase):
    def module(self):
        path = Path(__file__).resolve().parents[1] / "validation/verify_distribution.py"
        self.assertTrue(path.is_file(), "Missing standalone distribution validator")
        spec = importlib.util.spec_from_file_location("distribution_helper", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_distribution_roots_must_be_disjoint_before_writes(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            base = root / "base"
            candidate = root / "candidate"
            patches = root / "patches"
            for p in (base, candidate, patches):
                p.mkdir()
            for output in (base / "output", candidate / "output", patches / "output", root):
                with self.assertRaises(ValueError):
                    m.validate_roots(base, candidate, patches, output)
            self.assertFalse((base / "output").exists())
            with self.assertRaises(ValueError):
                m.validate_roots(base, base, patches, root / "out")
            self.assertEqual(m.validate_roots(base, candidate, patches, root / "out")[-1], root / "out")

    def test_archive_paths_are_canonical_and_stay_under_one_root(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for i, name in enumerate(
                ("kit/../escape", "kit//a", "kit/./a", "C:/escape", "other/a", "kit/../../x", "kit\\a")
            ):
                archive = root / f"{i}.zip"
                with zipfile.ZipFile(archive, "w") as z:
                    z.writestr(name, "unsafe")
                with self.assertRaises(ValueError):
                    m.extract(archive, root / f"out{i}", "kit")
            self.assertFalse((root / "escape").exists())

    def test_pilot_rejects_overlapping_output_without_copying(self):
        path = Path(__file__).resolve().parents[1] / "validation/pilot_knowledge.py"
        self.assertTrue(path.is_file(), "Missing standalone documentation pilot")
        spec = importlib.util.spec_from_file_location("pilot_helper", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = root / "repo"
            kit = root / "kit"
            repo.mkdir()
            kit.mkdir()
            for output in (repo / "out", kit / "out", root):
                with self.assertRaises(ValueError):
                    m.validate_locations(repo, kit, output)
            self.assertFalse((repo / "out").exists())

    def test_native_failure_names_support_tap_and_spec_reporters(self):
        path = Path(__file__).resolve().parents[1] / "validation/pilot_knowledge.py"
        spec = importlib.util.spec_from_file_location("pilot_helper", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertTrue(hasattr(m, "native_failures"), "Missing native reporter parser")
        self.assertEqual(m.native_failures("not ok 1 - first\nnot ok 2 - second\n"), ["first", "second"])
        self.assertEqual(
            m.native_failures("✖ first (1.2ms)\n✖ second (2ms)\n✖ first (1.2ms)\n"), ["first", "second"]
        )

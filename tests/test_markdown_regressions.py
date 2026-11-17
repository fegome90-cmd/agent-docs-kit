"""Check ignores Markdown code examples without hiding authored links."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import agent_docs_kit as kit


class MarkdownRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="kit markdown ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for relative in kit.DOCUMENTS:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Authored document\n", encoding="utf-8")

    def check_markdown(self, text: str) -> tuple[str, ...]:
        (self.root / "AGENTS.md").write_text(text, encoding="utf-8")
        return kit.check(self.root)

    def assert_real_link_reported(self, text: str) -> None:
        findings = self.check_markdown(text)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("real-missing.md", findings[0])

    def test_indented_code_links_are_ignored(self) -> None:
        for indent in ("    ", "\t", " \t", "   \t"):
            with self.subTest(indent=indent):
                self.assertEqual(self.check_markdown(
                    f"# Examples\n\n{indent}[example](missing.md)\n"
                ), ())

    def test_indented_fence_does_not_hide_following_authored_link(self) -> None:
        for indent in ("    ", "\t"):
            with self.subTest(indent=indent):
                self.assert_real_link_reported(
                    f"# Example\n\n{indent}```\n\n[real](real-missing.md)\n"
                )

    def test_closing_fence_requires_whitespace_after_run(self) -> None:
        for fence in ("```", "~~~"):
            with self.subTest(fence=fence):
                self.assert_real_link_reported(
                    f"{fence}text\n{fence}text\n"
                    "[example](missing.md)\n"
                    f"{fence}\n[real](real-missing.md)\n"
                )

    def test_fences_allow_up_to_three_spaces_and_longer_closing_run(self) -> None:
        for indent in ("", " ", "  ", "   "):
            for fence in ("```", "~~~"):
                with self.subTest(indent=indent, fence=fence):
                    self.assert_real_link_reported(
                        f"{indent}{fence}text\n[example](missing.md)\n"
                        f"{indent}{fence}{fence[0]} \t\n"
                        "[real](real-missing.md)\n"
                    )

    def test_short_mismatched_and_indented_runs_do_not_close_fence(self) -> None:
        for closing in ("```", "~~~~", "    ````", "\t````"):
            with self.subTest(closing=closing):
                self.assert_real_link_reported(
                    f"````text\n{closing}\n[example](missing.md)\n"
                    "````\n[real](real-missing.md)\n"
                )


if __name__ == "__main__":
    unittest.main()

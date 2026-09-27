"""Structural checks for the Milestone 0 repository scaffold."""

from __future__ import annotations

import unittest
from pathlib import Path

import agentic_synth_twin


ROOT = Path(__file__).resolve().parents[1]


class ScaffoldTests(unittest.TestCase):
    def test_package_is_importable(self) -> None:
        self.assertEqual(agentic_synth_twin.__version__, "0.0.0")

    def test_required_project_documents_exist(self) -> None:
        required = (
            "README.md",
            "AGENTS.md",
            "llms.txt",
            "docs/architecture.md",
            "docs/canonical-synth-state.md",
            "docs/deterministic-audition.md",
            "docs/schemas/canonical-synth-state-v1.schema.json",
            "docs/assets/repository-overview.svg",
        )
        for relative_path in required:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())


if __name__ == "__main__":
    unittest.main()

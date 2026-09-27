"""Integrity checks for the committed PR 1 feasibility evidence."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "pr1-clap-probe.json"


class ClapFeasibilityEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_inventory_count_matches_parameters(self) -> None:
        self.assertEqual(
            self.evidence["parameter_count"], len(self.evidence["parameters"])
        )
        self.assertEqual(len(self.evidence["parameters"]), 10)

    def test_mutation_and_restore_are_verified(self) -> None:
        mutation = self.evidence["mutation"]
        self.assertTrue(mutation["changed"])
        self.assertTrue(mutation["restore_verified"])
        self.assertNotEqual(mutation["before"], mutation["after"])
        self.assertEqual(mutation["before"], mutation["restored"])

    def test_parameter_names_are_nonempty_and_ids_are_unique(self) -> None:
        parameters = self.evidence["parameters"]
        self.assertTrue(all(parameter["name"] for parameter in parameters))
        self.assertEqual(
            len({parameter["id"] for parameter in parameters}), len(parameters)
        )


if __name__ == "__main__":
    unittest.main()

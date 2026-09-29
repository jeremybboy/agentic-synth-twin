"""Contract checks for the committed full-library retrieval audit ledger."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/milestone-9/perceptual-retrieval-audit.json"


class PerceptualRetrievalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_full_factory_coverage_and_eligibility_are_explicit(self) -> None:
        cache = self.payload["cache"]
        self.assertEqual(cache["factory_total"], 637)
        self.assertEqual(
            cache["retrieval_eligible"] + cache["failed"], cache["factory_total"]
        )
        self.assertLessEqual(cache["search_eligible"], cache["retrieval_eligible"])

    def test_every_target_has_both_complete_rankings_and_true_base_rank(self) -> None:
        targets = self.payload["targets"]
        self.assertEqual(len(targets), 10)
        for result in targets:
            with self.subTest(target=result["target"]["target_id"]):
                self.assertEqual(len(result["legacy_top_10"]), 10)
                self.assertEqual(len(result["perceptual_top_10"]), 10)
                self.assertGreaterEqual(result["cma_base_perceptual_rank"], 1)

    def test_sanity_and_human_acceptance_boundaries_are_not_conflated(self) -> None:
        sanity = self.payload["sanity"]
        self.assertTrue(sanity["self_retrieval"]["all_sources_rank_one_or_top_tied"])
        self.assertTrue(sanity["near_state"]["all_sources_in_top_10"])
        self.assertFalse(sanity["weights_changed_after_audit"])
        self.assertFalse(self.payload["human_acceptance_complete"])
        self.assertEqual(len(self.payload["human_audit"]), 10)
        for status in self.payload["human_audit"].values():
            self.assertEqual(status["comparison"], "PENDING")
            self.assertEqual(status["plausible_neighborhood"], "UNCLEAR")


if __name__ == "__main__":
    unittest.main()

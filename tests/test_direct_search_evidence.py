"""Integrity checks for the committed Milestone 8A result."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agentic_synth_twin.direct_search import (
    ACTIVE_PARAMETER_IDS,
    DEFAULT_BUDGET,
    OBJECTIVE_WEIGHTS,
    RANDOM_SEARCH_SEED,
    SEARCH_SCHEMA,
    SEARCH_SEED,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "milestone-8a" / "result.json"


class DirectSearchEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_evidence_identifies_the_frozen_experiment(self) -> None:
        self.assertEqual(self.evidence["schema"], SEARCH_SCHEMA)
        self.assertEqual(self.evidence["run"]["seed"], SEARCH_SEED)
        self.assertEqual(self.evidence["run"]["evaluations"], DEFAULT_BUDGET)
        self.assertEqual(self.evidence["random_search"]["seed"], RANDOM_SEARCH_SEED)
        self.assertEqual(self.evidence["random_search"]["budget"], DEFAULT_BUDGET)
        self.assertEqual(
            [item["id"] for item in self.evidence["active_parameters"]],
            list(ACTIVE_PARAMETER_IDS),
        )
        self.assertEqual(self.evidence["fixed_filter"]["id"], 14255)
        self.assertTrue(self.evidence["fixed_filter"]["fixed"])
        self.assertEqual(self.evidence["objective"]["weights"], OBJECTIVE_WEIGHTS)

    def test_result_preserves_the_failed_adaptive_gate(self) -> None:
        result = self.evidence["result"]
        self.assertTrue(result["engine_integrity"])
        self.assertTrue(result["optimization_pass"])
        self.assertFalse(result["adaptive_search_pass"])
        self.assertFalse(result["overall_numeric_pass"])
        self.assertEqual(result["audible_human_check"], "PENDING_OWNER_AUDITION")
        self.assertLess(
            self.evidence["random_search"]["best_loss"],
            self.evidence["run"]["final_best_loss"],
        )

    def test_final_rerender_and_repeat_are_exact(self) -> None:
        best = self.evidence["final_best"]
        rerender = self.evidence["final_rerender"]
        self.assertEqual(best["wav_sha256"], rerender["wav_sha256"])
        self.assertTrue(rerender["byte_identical"])
        self.assertTrue(rerender["matches_search_best"])
        self.assertEqual(best["clipped_samples"], 0)
        self.assertTrue(
            self.evidence["reproducibility"][
                "same_candidate_sequence_real_states_component_losses_and_wav_hashes"
            ]
        )


if __name__ == "__main__":
    unittest.main()

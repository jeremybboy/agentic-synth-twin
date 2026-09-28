"""Integrity checks for committed Milestone 8B evidence."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agentic_synth_twin.multitarget import (
    ADAPTIVE_REQUIRED_WINS,
    OPTIMIZATION_REQUIRED_TARGETS,
    SUITE_SCHEMA,
    TARGET_SUITE,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "milestone-8b" / "result.json"


class MultiTargetEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_evidence_matches_frozen_suite_and_budget(self) -> None:
        self.assertEqual(self.evidence["schema"], SUITE_SCHEMA)
        self.assertEqual(self.evidence["procedure"]["target_count"], len(TARGET_SUITE))
        self.assertEqual(
            [target["id"] for target in self.evidence["targets"]],
            [target["id"] for target in TARGET_SUITE],
        )
        self.assertEqual(
            [target["target_normalized"] for target in self.evidence["targets"]],
            [list(target["normalized"]) for target in TARGET_SUITE],
        )
        self.assertEqual(self.evidence["procedure"]["total_candidate_evaluations"], 2560)

    def test_result_preserves_passes_and_failed_adaptive_gate(self) -> None:
        aggregate = self.evidence["aggregate"]
        self.assertTrue(aggregate["engine_integrity_pass"])
        self.assertEqual(
            aggregate["optimization_required_count"], OPTIMIZATION_REQUIRED_TARGETS
        )
        self.assertTrue(aggregate["optimization_gate_pass"])
        self.assertEqual(aggregate["adaptive_required_wins"], ADAPTIVE_REQUIRED_WINS)
        self.assertEqual(aggregate["cma_win_count"], 6)
        self.assertGreater(
            aggregate["median_cma_best_loss"], aggregate["median_random_best_loss"]
        )
        self.assertFalse(aggregate["adaptive_gate_pass"])
        self.assertEqual(
            aggregate["human_audition"], "PENDING_OWNER_BLIND_COMPARISON"
        )

    def test_every_target_has_real_audio_hashes_and_finite_losses(self) -> None:
        for target in self.evidence["targets"]:
            with self.subTest(target=target["id"]):
                self.assertGreater(target["starting_loss"], 0)
                self.assertGreaterEqual(target["cma_best_loss"], 0)
                self.assertGreaterEqual(target["random_best_loss"], 0)
                self.assertEqual(len(target["target_wav_sha256"]), 64)
                self.assertEqual(len(target["cma_wav_sha256"]), 64)
                self.assertEqual(len(target["random_wav_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()

"""Milestone 8B multi-target validation contracts."""

from __future__ import annotations

import unittest

from agentic_synth_twin.multitarget import (
    ADAPTIVE_REQUIRED_WINS,
    OPTIMIZATION_REQUIRED_TARGETS,
    SUITE_SCHEMA,
    TARGET_SUITE,
    _blind_assignment,
    public_suite,
)


class MultiTargetTests(unittest.TestCase):
    def test_suite_has_eight_unique_bounded_non_instrument_targets(self) -> None:
        self.assertEqual(len(TARGET_SUITE), 8)
        self.assertEqual(len({item["id"] for item in TARGET_SUITE}), 8)
        for target in TARGET_SUITE:
            self.assertEqual(len(target["normalized"]), 4)
            self.assertTrue(all(0.0 <= value <= 1.0 for value in target["normalized"]))
            self.assertNotIn("guitar", target["label"].lower())
            self.assertNotIn("rhodes", target["label"].lower())
        self.assertEqual(OPTIMIZATION_REQUIRED_TARGETS, 6)
        self.assertEqual(ADAPTIVE_REQUIRED_WINS, 5)

    def test_blinding_is_deterministic_and_balanced(self) -> None:
        first = [_blind_assignment(item["id"]) for item in TARGET_SUITE]
        second = [_blind_assignment(item["id"]) for item in TARGET_SUITE]
        self.assertEqual(first, second)
        a_cma = sum(item["candidate_a"] == "cma" for item in first)
        self.assertGreater(a_cma, 0)
        self.assertLess(a_cma, len(first))

    def test_public_suite_withholds_target_and_method_mapping_until_reveal(self) -> None:
        suite = {
            "schema": SUITE_SCHEMA,
            "procedure": {"target_count": 1},
            "aggregate": {"target_count": 1, "engine_integrity_pass": True},
            "targets_private": [
                {
                    "id": "one",
                    "label": "One",
                    "target_parameters_private": {"parameter_values_normalized": [0.2] * 4},
                    "audio_private": {
                        "target": "target.wav",
                        "start": "start.wav",
                        "cma": "cma.wav",
                        "random": "random.wav",
                    },
                    "blind_assignment_private": {
                        "candidate_a": "random",
                        "candidate_b": "cma",
                    },
                    "cma": {"best_loss": 0.1},
                    "random": {"best_loss": 0.2},
                }
            ],
        }
        hidden = public_suite(suite)
        self.assertNotIn("target_parameters", hidden["targets"][0])
        self.assertNotIn("blind_assignment", hidden["targets"][0])
        self.assertTrue(hidden["targets"][0]["audio"]["candidate_a"].endswith("random.wav"))
        revealed = public_suite(suite, reveal=True)
        self.assertEqual(
            revealed["targets"][0]["blind_assignment"]["candidate_b"], "cma"
        )
        self.assertIn("target_parameters", revealed["targets"][0])


if __name__ == "__main__":
    unittest.main()

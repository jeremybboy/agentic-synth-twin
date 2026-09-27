"""Synthetic dataset sampling and committed evidence checks."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from agentic_synth_twin.dataset import (
    ATTACK_ID,
    CUTOFF_ID,
    DATASET_SAMPLE_COUNT,
    DATASET_SEED,
    FILTER_TYPE_ID,
    SELECTED_PARAMETER_IDS,
    plan_parameter_states,
)
from agentic_synth_twin.synth_state import load_canonical_state


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "docs" / "evidence" / "milestone-2-canonical-state.json"
ACCEPTED_WAV = ROOT / "docs" / "evidence" / "milestone-3-c3.wav"
CALIBRATION = ROOT / "docs" / "evidence" / "milestone-4-calibration-results.json"
DSP = ROOT / "docs" / "evidence" / "milestone-5-dsp-measurements.json"
DATASET = ROOT / "docs" / "evidence" / "milestone-6-synthetic-dataset.json"


class DatasetPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = load_canonical_state(CANONICAL)
        cls.inventory = {
            parameter["id"]: parameter for parameter in cls.state["parameters"]
        }

    def test_plan_is_deterministic_bounded_and_contains_exact_baseline(self) -> None:
        first = plan_parameter_states(self.state)
        second = plan_parameter_states(self.state)
        other_seed = plan_parameter_states(self.state, seed=DATASET_SEED + 1)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_seed)
        self.assertEqual(len(first), DATASET_SAMPLE_COUNT)
        self.assertEqual(first[0]["sample_id"], "sample-0000")
        self.assertTrue(first[0]["is_canonical_baseline"])
        for parameter_id in SELECTED_PARAMETER_IDS:
            self.assertEqual(
                first[0]["parameter_values"][str(parameter_id)],
                self.inventory[parameter_id]["current"],
            )
        for index, sample in enumerate(first):
            self.assertEqual(sample["sample_id"], f"sample-{index:04d}")
            for parameter_id in SELECTED_PARAMETER_IDS:
                value = sample["parameter_values"][str(parameter_id)]
                parameter = self.inventory[parameter_id]
                self.assertGreaterEqual(value, parameter["min"])
                self.assertLessEqual(value, parameter["max"])

    def test_filter_categories_are_balanced(self) -> None:
        plan = plan_parameter_states(self.state)
        counts = {
            value: sum(
                sample["parameter_values"][str(FILTER_TYPE_ID)] == value
                for sample in plan
            )
            for value in range(6)
        }
        self.assertEqual(sum(counts.values()), DATASET_SAMPLE_COUNT)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_continuous_dimensions_use_every_stratum_once(self) -> None:
        plan = plan_parameter_states(self.state)[1:]
        count = len(plan)
        for parameter_id in (CUTOFF_ID, ATTACK_ID):
            parameter = self.inventory[parameter_id]
            strata = {
                min(
                    count - 1,
                    int(
                        (sample["parameter_values"][str(parameter_id)] - parameter["min"])
                        / (parameter["max"] - parameter["min"])
                        * count
                    ),
                )
                for sample in plan
            }
            self.assertEqual(len(strata), count)


class DatasetEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = json.loads(DATASET.read_text(encoding="utf-8"))

    def test_dataset_is_bound_to_prior_evidence(self) -> None:
        self.assertEqual(
            self.dataset["schema"], "agentic-synth-twin/synthetic-dataset/v1"
        )
        source = self.dataset["source"]
        self.assertEqual(
            source["canonical_state_sha256"], hashlib.sha256(CANONICAL.read_bytes()).hexdigest()
        )
        self.assertEqual(
            source["calibration_results_sha256"],
            hashlib.sha256(CALIBRATION.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            source["dsp_measurements_sha256"],
            hashlib.sha256(DSP.read_bytes()).hexdigest(),
        )

    def test_all_256_rows_are_deterministic_unclipped_and_measured(self) -> None:
        samples = self.dataset["samples"]
        self.assertEqual(len(samples), DATASET_SAMPLE_COUNT)
        self.assertEqual(self.dataset["summary"]["clipped_sample_count"], 0)
        self.assertEqual(
            samples[0]["wav"]["sha256"],
            hashlib.sha256(ACCEPTED_WAV.read_bytes()).hexdigest(),
        )
        for sample in samples:
            verification = sample["render_verification"]
            self.assertEqual(verification["renders"], 2)
            self.assertTrue(verification["byte_identical"])
            self.assertEqual(verification["clipped_samples"], 0)
            self.assertEqual(len(verification["parameter_changes"]), 3)
            self.assertEqual(
                [change["id"] for change in verification["parameter_changes"]],
                list(SELECTED_PARAMETER_IDS),
            )
            features = sample["wav"]["features"]
            self.assertGreater(features["spectral_centroid_hz"], 0)
            self.assertLessEqual(features["peak_dbfs"], 0)

    def test_wavs_are_local_only(self) -> None:
        self.assertEqual(
            self.dataset["storage"]["local_only"],
            "WAV files under ignored work/dataset/audio",
        )
        tracked = ROOT / "docs" / "evidence"
        self.assertFalse(any(tracked.glob("milestone-6-*.wav")))


if __name__ == "__main__":
    unittest.main()

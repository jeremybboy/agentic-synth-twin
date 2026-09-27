"""Leakage-safe Milestone 7 surrogate training checks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import numpy as np

from agentic_synth_twin.surrogate import (
    FILTER_VALUES,
    FREEZE_SCHEMA,
    FROZEN_DATASET_SHA256,
    MODEL_SCHEMA,
    RESULTS_SCHEMA,
    SPLIT_SCHEMA,
    TARGET_KEYS,
    build_model,
    create_split,
    default_config,
    load_dataset,
    load_model,
    prepare_features,
    prepare_targets,
    train_surrogate,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "docs" / "evidence" / "milestone-6-synthetic-dataset.json"


class SurrogateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_dataset(DATASET)

    def test_frozen_dataset_hash_is_authoritative(self) -> None:
        self.assertEqual(hashlib.sha256(DATASET.read_bytes()).hexdigest(), FROZEN_DATASET_SHA256)
        self.assertEqual(len(self.dataset["samples"]), 256)

    def test_split_is_deterministic_disjoint_complete_and_stratified(self) -> None:
        first = create_split(self.dataset)
        second = create_split(self.dataset)
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], SPLIT_SCHEMA)
        self.assertEqual(first["counts"], {"train": 166, "validation": 38, "test": 52})
        groups = {name: set(values) for name, values in first["sample_ids"].items()}
        self.assertFalse(groups["train"] & groups["validation"])
        self.assertFalse(groups["train"] & groups["test"])
        self.assertFalse(groups["validation"] & groups["test"])
        self.assertEqual(len(set().union(*groups.values())), 256)
        for split in groups:
            counts = first["filter_type_counts"][split]
            self.assertEqual(set(counts), {str(value) for value in FILTER_VALUES})
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_filter_type_is_one_hot_not_ordinal(self) -> None:
        rows = self.dataset["samples"][:24]
        x = prepare_features(rows)
        y = prepare_targets(rows)
        model = build_model("linear", default_config())
        model.fit(x, y)
        transformer = model.named_steps["features"]
        transformed = transformer.transform(x)
        self.assertEqual(transformed.shape[1], 8)
        encoder = transformer.named_transformers_["filter_type"]
        self.assertEqual(encoder.categories_[0].tolist(), list(FILTER_VALUES))

    def test_complete_test_workflow_freezes_before_one_test_evaluation(self) -> None:
        config = deepcopy(default_config())
        config["models"]["random_forest"]["n_estimators"] = 8
        config["models"]["random_forest"]["n_jobs"] = 1
        config["learning_curve_sizes"] = [32]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            results = train_surrogate(
                dataset_path=DATASET,
                output_directory=output,
                repository_root=ROOT,
                rerender=False,
                config_override=config,
            )
            self.assertEqual(results["schema"], RESULTS_SCHEMA)
            self.assertEqual(results["protocol"]["final_test_evaluations"], 1)
            self.assertTrue(results["protocol"]["test_locked_during_model_selection"])
            self.assertTrue(results["protocol"]["freeze_unchanged_after_test"])
            freeze = json.loads((output / "freeze.json").read_text())
            self.assertEqual(freeze["schema"], FREEZE_SCHEMA)
            self.assertEqual(freeze["test_status"], "locked")
            bundle = load_model(output / "model.joblib")
            self.assertEqual(bundle["schema"], MODEL_SCHEMA)
            self.assertEqual(tuple(bundle["targets"]), TARGET_KEYS)
            predictions = json.loads((output / "predictions.json").read_text())
            self.assertEqual(len(predictions["records"]), 256)
            self.assertEqual(
                {record["split"] for record in predictions["records"]},
                {"train", "validation", "test"},
            )
            for model in results["candidates"].values():
                self.assertTrue(
                    np.isfinite(model["validation"]["selection_mean_normalized_mae"])
                )


class CommittedSurrogateEvidenceTests(unittest.TestCase):
    EVIDENCE = ROOT / "docs" / "evidence" / "milestone-7"

    def test_committed_evidence_and_model_are_hash_bound(self) -> None:
        results_path = self.EVIDENCE / "results.json"
        if not results_path.is_file():
            self.skipTest("committed Milestone 7 evidence is captured after code validation")
        results = json.loads(results_path.read_text())
        self.assertEqual(results["schema"], RESULTS_SCHEMA)
        self.assertEqual(results["dataset"]["sha256"], FROZEN_DATASET_SHA256)
        self.assertEqual(results["split"]["counts"], {"train": 166, "validation": 38, "test": 52})
        self.assertEqual(results["protocol"]["final_test_evaluations"], 1)
        for artifact in results["artifacts"].values():
            path = self.EVIDENCE / artifact["filename"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact["sha256"])
        bundle = load_model(self.EVIDENCE / "model.joblib")
        self.assertEqual(bundle["model_type"], results["selection"]["selected_model"])
        rerender = json.loads((self.EVIDENCE / "rerender-check.json").read_text())
        self.assertTrue(rerender["all_exact"])


if __name__ == "__main__":
    unittest.main()

"""Evidence-export boundary tests for perceptual multi-start runs."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "perceptual-multistart-refinement.json"
SPEC = importlib.util.spec_from_file_location(
    "export_perceptual_multistart_evidence",
    ROOT / "scripts" / "export_perceptual_multistart_evidence.py",
)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


class PerceptualMultiStartEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_completed_failed_verification_remains_exportable(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = (
                Path(temporary)
                / "targets"
                / "rhodes-style-electric-piano"
                / "run-test"
            )
            run.mkdir(parents=True)
            multistart = {
                "stage": "COMPLETE",
                "verified_stable_best": None,
                "numeric_best": None,
                "attempted_start_ranks": [],
                "starts_full": [],
                "skipped_starts_full": [],
                "history": [],
                "stage_b_survivors": [],
                "verification_attempts": [{"stable": False}],
                "human_judgment": "PENDING_OWNER",
                "configuration": {"master_seed": 42},
                "frozen_family_weights": {"log_mel": 0.55},
            }
            (run / "multistart.json").write_text(json.dumps(multistart))
            (run / "preset-index.json").write_text(
                json.dumps({"perceptual_top_10": [{"preset_name": "EP 2"}]})
            )
            (run / "run.json").write_text(
                json.dumps(
                    {
                        "external_target": {
                            "target_id": "rhodes-style-electric-piano",
                            "wav_sha256": "abc",
                        }
                    }
                )
            )

            exported = EXPORTER.load_run(run, ROOT)

            self.assertFalse(exported["summary"]["stable_verification_succeeded"])
            self.assertIsNone(exported["summary"]["verified_stable_score"])
            self.assertEqual(exported["perceptual_top_10"][0]["preset_name"], "EP 2")
            self.assertEqual(exported["target"]["wav_sha256"], "abc")

    def test_committed_cross_target_schedule_is_frozen_and_complete(self):
        self.assertTrue(self.evidence["configuration_unchanged_across_targets"])
        self.assertEqual(
            [run["target_id"] for run in self.evidence["runs"]],
            [
                "rhodes-style-electric-piano",
                "plucked-electric-guitar",
                "analog-sub-bass",
            ],
        )
        for run in self.evidence["runs"]:
            summary = run["summary"]
            self.assertEqual(summary["valid_start_count"], 5)
            self.assertEqual(summary["cma_evaluations"], 288)
            self.assertEqual(summary["human_judgment"], "PENDING_OWNER")
            self.assertEqual(len(run["perceptual_top_10"]), 10)

    def test_committed_run_preserves_hierarchy_candidates_and_verification(self):
        families = {"log_mel", "envelope", "harmonic", "flatness"}
        for run in self.evidence["runs"]:
            full = run["full_evidence"]
            self.assertEqual(len(full["history"]), 288)
            self.assertTrue(full["verification_attempts"])
            self.assertEqual(full["human_judgment"], "PENDING_OWNER")
            for start in full["starts_full"]:
                self.assertEqual(
                    len(start["structural_exclusion_ledger"]),
                    start["inventory_parameter_count"],
                )
                self.assertTrue(start["module_probes"])
                self.assertTrue(start["probes"])
                self.assertGreaterEqual(start["selected_dimension"], 4)
                self.assertLessEqual(start["selected_dimension"], 12)
                for probe in start["module_probes"] + start["probes"]:
                    for direction in probe["directions"]:
                        if direction["valid"]:
                            if direction["direction"] != "neutral":
                                self.assertEqual(
                                    set(direction["family_effects"]), families
                                )
                        else:
                            self.assertTrue(direction["invalid_reason"])
            for candidate in full["history"]:
                if candidate["valid"]:
                    self.assertEqual(
                        set(candidate["weighted_contributions"]), families
                    )


if __name__ == "__main__":
    unittest.main()

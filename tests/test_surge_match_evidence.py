import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "milestone-9" / "result.json"


class SurgeMatchEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_fixed_run_passed_predeclared_numeric_gate(self):
        result = self.data["result"]
        run = self.data["run"]
        self.assertTrue(result["surge_integrity"])
        self.assertTrue(result["preset_retrieval_completed"])
        self.assertTrue(result["local_optimization_pass"])
        self.assertGreaterEqual(
            run["improvement_percent"], result["local_optimization_gate_percent"]
        )
        self.assertEqual(result["human_listening"], "PENDING_OWNER_AUDITION")

    def test_preset_index_is_bounded_ranked_and_hash_bound(self):
        library = self.data["preset_library"]
        self.assertEqual(library["selected"], 120)
        self.assertEqual(library["indexed"] + library["failed"], 120)
        self.assertEqual(len(library["top_five"]), 5)
        self.assertEqual([row["rank"] for row in library["top_five"]], list(range(1, 6)))
        self.assertTrue(all(len(row["state_sha256"]) == 64 for row in library["top_five"]))
        self.assertTrue(all(len(row["wav_sha256"]) == 64 for row in library["top_five"]))

    def test_cma_and_random_have_equal_budgets(self):
        run = self.data["run"]
        random_control = self.data["random_control"]
        self.assertEqual(run["evaluations"], run["budget"])
        self.assertEqual(random_control["budget"], run["budget"])
        self.assertTrue(random_control["same_target_base_parameters_bounds_objective"])
        self.assertLess(run["best_loss"], random_control["best"]["total_loss"])

    def test_final_best_was_independently_rerendered(self):
        final = self.data["final_result"]
        verification = self.data["final_verification"]
        self.assertTrue(verification["byte_identical"])
        self.assertTrue(verification["matches_search_best"])
        self.assertEqual(final["final_audio_sha256"], verification["wav_sha256"])
        self.assertEqual(len(final["changed_parameters"]), 8)


if __name__ == "__main__":
    unittest.main()

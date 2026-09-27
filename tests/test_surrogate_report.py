"""Read-only Milestone 7 training cockpit checks."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentic_synth_twin.surrogate_report import (
    SurrogateReportError,
    create_report_server,
    load_report_data,
    render_report_page,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "docs" / "evidence" / "milestone-6-synthetic-dataset.json"
EVIDENCE = ROOT / "docs" / "evidence" / "milestone-7"


class SurrogateReportTests(unittest.TestCase):
    def test_page_exposes_required_scientific_views_and_boundaries(self) -> None:
        page = render_report_page()
        for phrase in (
            "Surrogate training cockpit",
            "TEST LOCKED",
            "MODEL FROZEN",
            "Learning curves",
            "Model comparison",
            "Predicted vs actual",
            "Residual view",
            "Regional error",
            "Feature importance",
            "Structured holdout",
            "Real-synth rerender",
            "does not understand timbre",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, page)

    def test_committed_report_data_verifies_every_artifact(self) -> None:
        if not (EVIDENCE / "results.json").is_file():
            self.skipTest("committed Milestone 7 evidence is captured after code validation")
        report = load_report_data(
            results_path=EVIDENCE / "results.json",
            predictions_path=EVIDENCE / "predictions.json",
            status_path=EVIDENCE / "status.json",
            dataset_path=DATASET,
        )
        self.assertEqual(report["status"]["phase"], "complete")
        self.assertEqual(len(report["predictions"]["records"]), 256)

    def test_server_is_read_only_and_loopback_only(self) -> None:
        if not (EVIDENCE / "results.json").is_file():
            self.skipTest("committed Milestone 7 evidence is captured after code validation")
        server = create_report_server(
            results_path=EVIDENCE / "results.json",
            predictions_path=EVIDENCE / "predictions.json",
            status_path=EVIDENCE / "status.json",
            dataset_path=DATASET,
            host="127.0.0.1",
            port=0,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/", timeout=5) as response:
                self.assertIn(b"Surrogate training cockpit", response.read())
            with urllib.request.urlopen(base + "/api/report", timeout=5) as response:
                self.assertEqual(json.load(response)["status"]["phase"], "complete")
            request = urllib.request.Request(base + "/", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 501)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        with self.assertRaisesRegex(SurrogateReportError, "loopback"):
            create_report_server(
                results_path=EVIDENCE / "results.json",
                predictions_path=EVIDENCE / "predictions.json",
                status_path=EVIDENCE / "status.json",
                dataset_path=DATASET,
                host="0.0.0.0",
                port=0,
            )


if __name__ == "__main__":
    unittest.main()

"""Blinded multi-target report persistence and HTTP checks."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentic_synth_twin.multitarget import SUITE_SCHEMA
from agentic_synth_twin.multitarget_report import (
    MultiTargetReportError,
    MultiTargetSession,
    create_multitarget_server,
    render_multitarget_page,
)


def _fixture(root: Path) -> None:
    for name in ("target.wav", "start.wav", "cma.wav", "random.wav"):
        (root / name).write_bytes(b"RIFF-test")
    suite = {
        "schema": SUITE_SCHEMA,
        "procedure": {"target_count": 1},
        "aggregate": {
            "target_count": 1,
            "engine_integrity_pass": True,
            "cma_win_count": 1,
            "median_cma_best_loss": 0.1,
            "median_random_best_loss": 0.2,
            "adaptive_gate_pass": True,
            "human_audition": "PENDING_OWNER_BLIND_COMPARISON",
        },
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
                    "candidate_a": "cma",
                    "candidate_b": "random",
                },
                "cma": {"best_loss": 0.1},
                "random": {"best_loss": 0.2},
                "cma_wins": True,
                "engine_integrity": True,
            }
        ],
    }
    (root / "suite.json").write_text(json.dumps(suite), encoding="utf-8")


class MultiTargetReportTests(unittest.TestCase):
    def test_page_explains_blinding_and_scope_boundary(self) -> None:
        page = render_multitarget_page()
        self.assertIn("CANDIDATE A", page)
        self.assertIn("CANDIDATE B", page)
        self.assertIn("REVEAL METHODS AND RESULTS", page)
        self.assertIn("does not claim guitar, Rhodes", page)

    def test_session_requires_all_judgments_before_reveal_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            session = MultiTargetSession(root)
            with self.assertRaisesRegex(MultiTargetReportError, "complete all"):
                session.reveal()
            with self.assertRaisesRegex(MultiTargetReportError, "listen to"):
                session.record_judgment("one", "A")
            for role in ("target", "candidate_a", "candidate_b"):
                session.record_listen("one", role)
            hidden = session.record_judgment("one", "A")
            self.assertFalse(hidden["revealed"])
            self.assertNotIn("blind_assignment", hidden["targets"][0])
            revealed = session.reveal()
            self.assertEqual(
                revealed["targets"][0]["blind_assignment"]["candidate_a"], "cma"
            )
            persisted = json.loads((root / "judgments.json").read_text())
            self.assertEqual(persisted["judgments"]["one"]["choice"], "A")
            self.assertEqual(
                persisted["judgments"]["one"]["selected_method_private"], "cma"
            )

    def test_loopback_http_records_judgment_and_serves_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            session = MultiTargetSession(root)
            server = create_multitarget_server(session, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/api/status", timeout=5) as response:
                    self.assertFalse(json.load(response)["revealed"])
                for role in ("target", "candidate_a", "candidate_b"):
                    listen = urllib.request.Request(
                        base + "/api/listen",
                        data=json.dumps({"target_id": "one", "role": role}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(listen, timeout=5) as response:
                        self.assertIn(role, json.load(response)["heard"]["one"])
                request = urllib.request.Request(
                    base + "/api/judgment",
                    data=json.dumps({"target_id": "one", "choice": "B"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(json.load(response)["completed_judgments"], 1)
                with urllib.request.urlopen(base + "/audio/target.wav", timeout=5) as response:
                    self.assertEqual(response.headers.get_content_type(), "audio/wav")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        with self.assertRaisesRegex(MultiTargetReportError, "loopback"):
            create_multitarget_server(session, host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()

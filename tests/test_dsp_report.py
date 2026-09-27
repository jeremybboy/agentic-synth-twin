"""Read-only DSP browser report checks."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentic_synth_twin.dsp_report import (
    DSPReportError,
    create_report_server,
    load_report_data,
    render_report_page,
)


ROOT = Path(__file__).resolve().parents[1]
ACCEPTED_WAV = ROOT / "docs" / "evidence" / "milestone-3-c3.wav"


class DSPReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.audio_root = root / "probes"
        probe = self.audio_root / "01-parameter-17"
        probe.mkdir(parents=True)
        shutil.copyfile(ACCEPTED_WAV, probe / "A-baseline.wav")
        shutil.copyfile(ACCEPTED_WAV, probe / "B-variant.wav")
        wav_hash = hashlib.sha256(ACCEPTED_WAV.read_bytes()).hexdigest()
        audio = {"sha256": wav_hash, "features": {}}
        self.evidence = {
            "schema": "agentic-synth-twin/dsp-measurements/v1",
            "source": {"session_id": "session-test"},
            "summary": {"measurement_count": 1, "human_yes_count": 1},
            "measurements": [
                {
                    "ordinal": 1,
                    "parameter_id": 17,
                    "parameter_name": "Oscillator Detuning (in cents)",
                    "baseline_value": 69,
                    "variant_value": 80,
                    "human_meaningful_difference": True,
                    "A": audio,
                    "B": audio,
                    "deltas": {},
                }
            ],
        }
        self.measurements = root / "measurements.json"
        self.measurements.write_text(json.dumps(self.evidence), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_page_explains_human_and_measurement_boundary(self) -> None:
        page = render_report_page()
        self.assertIn("Measured change vs. human hearing", page)
        self.assertIn("Do not confuse magnitude with perception", page)
        self.assertIn("DOWNLOAD MEASUREMENTS JSON", page)

    def test_loader_binds_audio_urls_to_declared_hashes(self) -> None:
        report, audio_paths = load_report_data(self.measurements, self.audio_root)
        self.assertEqual(report["measurements"][0]["A"]["url"], "/audio/1/A.wav")
        self.assertEqual(set(audio_paths), {"/audio/1/A.wav", "/audio/1/B.wav"})
        (self.audio_root / "01-parameter-17" / "B-variant.wav").write_bytes(b"bad")
        with self.assertRaisesRegex(DSPReportError, "hash mismatch"):
            load_report_data(self.measurements, self.audio_root)

    def test_server_is_read_only_and_loopback_only(self) -> None:
        server = create_report_server(
            measurements_path=self.measurements,
            audio_root=self.audio_root,
            host="127.0.0.1",
            port=0,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/", timeout=5) as response:
                self.assertIn(b"DSP Comparison", response.read())
            with urllib.request.urlopen(base + "/api/measurements", timeout=5) as response:
                self.assertEqual(json.load(response)["summary"]["measurement_count"], 1)
            with urllib.request.urlopen(base + "/audio/1/A.wav", timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(), "audio/wav")
                self.assertEqual(
                    hashlib.sha256(response.read()).hexdigest(),
                    self.evidence["measurements"][0]["A"]["sha256"],
                )
            request = urllib.request.Request(base + "/", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 501)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        with self.assertRaisesRegex(DSPReportError, "loopback"):
            create_report_server(
                measurements_path=self.measurements,
                audio_root=self.audio_root,
                host="0.0.0.0",
                port=0,
            )


if __name__ == "__main__":
    unittest.main()

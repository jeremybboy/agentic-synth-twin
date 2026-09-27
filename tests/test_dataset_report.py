"""Read-only synthetic dataset explorer checks."""

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

from agentic_synth_twin.dataset_report import (
    DatasetReportError,
    create_report_server,
    load_report_data,
    render_report_page,
)


ROOT = Path(__file__).resolve().parents[1]
ACCEPTED_WAV = ROOT / "docs" / "evidence" / "milestone-3-c3.wav"


class DatasetReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.audio_root = root / "audio"
        self.audio_root.mkdir()
        audio = self.audio_root / "sample-0000.wav"
        shutil.copyfile(ACCEPTED_WAV, audio)
        self.dataset = {
            "schema": "agentic-synth-twin/synthetic-dataset/v1",
            "sampling": {"seed": 20_260_927},
            "summary": {
                "sample_count": 1,
                "clipped_sample_count": 0,
                "unique_wav_sha256_count": 1,
            },
            "samples": [
                {
                    "sample_id": "sample-0000",
                    "is_canonical_baseline": True,
                    "parameter_values": {"14255": 0, "17": 69, "2874": 0.01},
                    "wav": {
                        "filename": audio.name,
                        "sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                        "features": {},
                    },
                }
            ],
        }
        self.dataset_path = root / "dataset.json"
        self.dataset_path.write_text(json.dumps(self.dataset), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_page_states_dataset_authority_boundary(self) -> None:
        page = render_report_page()
        self.assertIn("Verified dataset explorer", page)
        self.assertIn("This is coverage, not intelligence", page)
        self.assertIn("No model has been trained", page)
        self.assertIn("DOWNLOAD DATASET JSON", page)

    def test_loader_binds_every_audio_url_to_its_hash(self) -> None:
        dataset, paths = load_report_data(self.dataset_path, self.audio_root)
        self.assertEqual(
            dataset["samples"][0]["audio_url"], "/audio/sample-0000.wav"
        )
        self.assertEqual(set(paths), {"/audio/sample-0000.wav"})
        (self.audio_root / "sample-0000.wav").write_bytes(b"bad")
        with self.assertRaisesRegex(DatasetReportError, "hash mismatch"):
            load_report_data(self.dataset_path, self.audio_root)

    def test_server_is_read_only_and_loopback_only(self) -> None:
        server = create_report_server(
            dataset_path=self.dataset_path,
            audio_root=self.audio_root,
            host="127.0.0.1",
            port=0,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/", timeout=5) as response:
                self.assertIn(b"Dataset Explorer", response.read())
            with urllib.request.urlopen(base + "/api/dataset", timeout=5) as response:
                self.assertEqual(json.load(response)["summary"]["sample_count"], 1)
            with urllib.request.urlopen(
                base + "/audio/sample-0000.wav", timeout=5
            ) as response:
                self.assertEqual(response.headers.get_content_type(), "audio/wav")
                self.assertEqual(
                    hashlib.sha256(response.read()).hexdigest(),
                    self.dataset["samples"][0]["wav"]["sha256"],
                )
            request = urllib.request.Request(base + "/", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 501)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        with self.assertRaisesRegex(DatasetReportError, "loopback"):
            create_report_server(
                dataset_path=self.dataset_path,
                audio_root=self.audio_root,
                host="0.0.0.0",
                port=0,
            )


if __name__ == "__main__":
    unittest.main()

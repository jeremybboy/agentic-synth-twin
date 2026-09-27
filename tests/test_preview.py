"""Local browser preview checks."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from agentic_synth_twin.preview import create_server, render_page


ROOT = Path(__file__).resolve().parents[1]
WAV = ROOT / "docs" / "evidence" / "milestone-3-c3.wav"
MANIFEST = ROOT / "docs" / "evidence" / "milestone-3-c3.json"


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_page_has_one_play_button_and_truthful_metadata(self) -> None:
        page = render_page(self.manifest)
        self.assertEqual(page.count("<button"), 1)
        self.assertIn("PLAY C3", page)
        self.assertIn("MIDI 48", page)
        self.assertIn("2 byte-identical renders", page)
        self.assertIn("ignores event velocity", page)

    def test_server_exposes_page_audio_manifest_and_health(self) -> None:
        server = create_server(
            wav_path=WAV,
            manifest_path=MANIFEST,
            host="127.0.0.1",
            port=0,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(f"{base}/", timeout=5) as response:
                page = response.read()
                self.assertEqual(response.status, 200)
                self.assertIn(b"PLAY C3", page)
            with urllib.request.urlopen(f"{base}/audition.wav", timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(), "audio/wav")
                self.assertEqual(response.read(4), b"RIFF")
            with urllib.request.urlopen(f"{base}/manifest.json", timeout=5) as response:
                served = json.load(response)
                self.assertEqual(served["wav"]["sha256"], self.manifest["wav"]["sha256"])
            with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
                self.assertEqual(json.load(response), {"status": "ok"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_rejects_audio_that_does_not_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wrong_wav = Path(directory) / "wrong.wav"
            wrong_wav.write_bytes(b"not the committed audio")
            with self.assertRaisesRegex(ValueError, "does not match manifest"):
                create_server(
                    wav_path=wrong_wav,
                    manifest_path=MANIFEST,
                    host="127.0.0.1",
                    port=0,
                )


if __name__ == "__main__":
    unittest.main()

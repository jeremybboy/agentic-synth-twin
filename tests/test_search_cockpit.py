"""Live search cockpit HTTP and presentation checks."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentic_synth_twin.search_cockpit import (
    SearchCockpitError,
    create_cockpit_server,
    render_cockpit_page,
)


class _FakeRun:
    def __init__(self, audio: Path):
        self.audio = audio
        self.status = "IDLE"

    def public_status(self) -> dict[str, object]:
        return {"status": self.status, "target_revealed": False}

    def history_public(self) -> list[object]:
        return []

    def spectra_public(self) -> dict[str, object]:
        item = {"values": [[0.0]], "time_seconds": 2.5, "max_frequency_hz": 22050}
        return {"target": item, "best": item, "shared_max": 0.0}

    def audio_path_for_url(self, path: str) -> Path:
        return self.audio

    def render_playable_note(
        self,
        *,
        source: str,
        midi_key: int,
        velocity: int,
        normalized: list[float] | None = None,
    ) -> Path:
        if source != "custom" or midi_key != 60 or velocity != 96:
            raise AssertionError("unexpected playable note request")
        if normalized != [0.1, 0.2, 0.3, 0.4]:
            raise AssertionError("unexpected playable patch")
        return self.audio

    def start_search(self) -> dict[str, object]:
        self.status = "SEARCHING"
        return self.public_status()

    pause_search = start_search
    resume_search = start_search
    stop_search = start_search
    reveal_target = start_search


class SearchCockpitTests(unittest.TestCase):
    def test_page_contains_required_live_views_without_hidden_target_values(self) -> None:
        page = render_cockpit_page()
        for text in (
            "REAL SYNTH SEARCH",
            "Live synth parameters",
            "Search-space exploration",
            "CMA-ES search distribution",
            "Convergence",
            "Objective breakdown",
            "TARGET",
            "STARTING PATCH",
            "CURRENT BEST",
            "Playable patch",
            "CONNECT USB MIDI",
            "LOAD CURRENT BEST",
            "Edited patch ready",
            "Computer keyboard (Ableton layout)",
            "white notes A S D F G H J K L",
            "Z/X changes octave",
            "REVEAL TARGET PARAMETERS",
            "Select external target",
            "/api/targets/select",
            "[hidden]{display:none!important}",
        ):
            self.assertIn(text, page)
        self.assertNotIn("0.72, 0.68, 0.28, 0.62", page)
        self.assertNotIn('id="targetSlider"', page)

    def test_loopback_server_exposes_status_controls_and_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.write_bytes(b"RIFF-test")
            run = _FakeRun(audio)
            server = create_cockpit_server(run, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/", timeout=5) as response:
                    self.assertIn(b"Real Synth Search", response.read())
                with urllib.request.urlopen(base + "/api/status", timeout=5) as response:
                    self.assertEqual(json.load(response)["status"], "IDLE")
                request = urllib.request.Request(
                    base + "/api/run/start",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(json.load(response)["status"], "SEARCHING")
                with urllib.request.urlopen(base + "/audio/target.wav", timeout=5) as response:
                    self.assertEqual(response.headers.get_content_type(), "audio/wav")
                playable_url = (
                    base
                    + "/api/playable/note?source=custom&note=60&velocity=96"
                    + "&values=0.1,0.2,0.3,0.4"
                )
                with urllib.request.urlopen(playable_url, timeout=5) as response:
                    self.assertEqual(response.headers.get_content_type(), "audio/wav")
                    self.assertEqual(response.read(), b"RIFF-test")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        with self.assertRaisesRegex(SearchCockpitError, "loopback"):
            create_cockpit_server(_FakeRun(Path("missing")), host="0.0.0.0", port=0)

    def test_unknown_mutation_route_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.write_bytes(b"RIFF-test")
            server = create_cockpit_server(
                _FakeRun(audio), host="127.0.0.1", port=0
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/run/reset",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(rejected.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_external_target_selection_api_uses_discrete_json_contract(self) -> None:
        class TargetRun(_FakeRun):
            selected = "analog-sub-bass"

            def targets_public(self) -> dict[str, object]:
                return {"selected_target_id": self.selected, "targets": [
                    {"target_id": "analog-sub-bass", "title": "Analog Sub Bass"},
                    {"target_id": "fm-bell", "title": "FM Bell"},
                ]}

            def select_target(self, target_id: str) -> dict[str, object]:
                if self.status in {"SEARCHING", "PAUSING", "PAUSED"}:
                    raise RuntimeError("stop the active search")
                self.selected = target_id
                return self.targets_public()

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.write_bytes(b"RIFF-test")
            run = TargetRun(audio)
            server = create_cockpit_server(run, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/api/targets", timeout=5) as response:
                    self.assertEqual(len(json.load(response)["targets"]), 2)
                request = urllib.request.Request(
                    base + "/api/targets/select",
                    data=json.dumps({"target_id": "fm-bell"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(json.load(response)["selected_target_id"], "fm-bell")
                run.status = "SEARCHING"
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(rejected.exception.code, 409)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

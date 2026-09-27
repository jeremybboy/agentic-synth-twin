"""Evidence checks for the deterministic real-synth audition."""

from __future__ import annotations

import hashlib
import json
import unittest
import wave
from pathlib import Path

from agentic_synth_twin.audition import (
    AUDITION_SCHEMA,
    BITS_PER_SAMPLE,
    CHANNELS,
    MIDI_KEY,
    NOTE_FRAMES,
    SAMPLE_RATE,
    TAIL_FRAMES,
    TOTAL_FRAMES,
    VELOCITY,
)


ROOT = Path(__file__).resolve().parents[1]
WAV = ROOT / "docs" / "evidence" / "milestone-3-c3.wav"
MANIFEST = ROOT / "docs" / "evidence" / "milestone-3-c3.json"
CANONICAL_STATE = ROOT / "docs" / "evidence" / "milestone-2-canonical-state.json"


class AuditionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.wav_bytes = WAV.read_bytes()

    def test_manifest_records_fixed_audition(self) -> None:
        audition = self.manifest["audition"]
        self.assertEqual(self.manifest["schema"], AUDITION_SCHEMA)
        self.assertEqual(audition["note_name"], "C3")
        self.assertEqual(audition["midi_key"], MIDI_KEY)
        self.assertEqual(audition["velocity"], VELOCITY)
        self.assertEqual(audition["sample_rate"], SAMPLE_RATE)
        self.assertEqual(audition["note_duration_seconds"], NOTE_FRAMES / SAMPLE_RATE)
        self.assertEqual(audition["release_tail_seconds"], TAIL_FRAMES / SAMPLE_RATE)

    def test_wav_structure_and_hash_match_manifest(self) -> None:
        evidence = self.manifest["wav"]
        self.assertEqual(hashlib.sha256(self.wav_bytes).hexdigest(), evidence["sha256"])
        with wave.open(str(WAV), "rb") as wav:
            self.assertEqual(wav.getnchannels(), CHANNELS)
            self.assertEqual(wav.getsampwidth() * 8, BITS_PER_SAMPLE)
            self.assertEqual(wav.getframerate(), SAMPLE_RATE)
            self.assertEqual(wav.getnframes(), TOTAL_FRAMES)
            pcm = wav.readframes(TOTAL_FRAMES)
        self.assertTrue(any(pcm))
        self.assertEqual(hashlib.sha256(pcm).hexdigest(), evidence["pcm_sha256"])

    def test_canonical_state_is_traceable(self) -> None:
        expected = self.manifest["canonical_state"]["json_sha256"]
        actual = hashlib.sha256(CANONICAL_STATE.read_bytes()).hexdigest()
        self.assertEqual(actual, expected)

    def test_two_render_determinism_and_no_clipping_are_recorded(self) -> None:
        self.assertEqual(self.manifest["determinism"]["verification_runs"], 2)
        self.assertTrue(self.manifest["determinism"]["byte_identical"])
        self.assertEqual(self.manifest["wav"]["clipped_samples"], 0)
        self.assertGreater(self.manifest["wav"]["peak_float"], 0)

    def test_velocity_limitation_is_explicit(self) -> None:
        limitation = self.manifest["known_limitations"]
        self.assertFalse(limitation["velocity_affects_sound"])
        self.assertIn("ignores event velocity", limitation["velocity_note"])


if __name__ == "__main__":
    unittest.main()

"""DSP descriptor and committed measurement evidence checks."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from agentic_synth_twin.dsp import DSPError, analyze_wav


ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_RESULTS = (
    ROOT / "docs" / "evidence" / "milestone-4-calibration-results.json"
)
MEASUREMENTS = ROOT / "docs" / "evidence" / "milestone-5-dsp-measurements.json"
ACCEPTED_WAV = ROOT / "docs" / "evidence" / "milestone-3-c3.wav"


def write_pcm16(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    clipped = np.clip(samples, -1.0, 1.0 - 1.0 / 32768.0)
    pcm = np.round(clipped * 32768.0).astype("<i2")
    if pcm.ndim == 1:
        pcm = pcm[:, np.newaxis]
    with wave.open(str(path), "wb") as target:
        target.setnchannels(pcm.shape[1])
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm.tobytes())


class DSPDescriptorTests(unittest.TestCase):
    def test_stereo_sine_level_and_spectrum(self) -> None:
        sample_rate = 44_100
        duration = 1.0
        time = np.arange(round(sample_rate * duration)) / sample_rate
        sine = 0.5 * np.sin(2.0 * math.pi * 440.0 * time)
        stereo = np.column_stack((sine, -sine))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "antiphase.wav"
            write_pcm16(path, stereo, sample_rate)
            result = analyze_wav(path, note_off_frame=sample_rate)
        features = result["features"]
        self.assertAlmostEqual(features["rms_dbfs"], -9.0309, places=3)
        self.assertAlmostEqual(features["peak_dbfs"], -6.0206, places=3)
        self.assertAlmostEqual(features["spectral_centroid_hz"], 440.0, delta=2.0)
        self.assertAlmostEqual(features["spectral_rolloff_85_hz"], 430.664, delta=22.0)

    def test_attack_and_release_are_measured_from_envelope(self) -> None:
        sample_rate = 8_000
        note_off = sample_rate
        total = note_off + sample_rate // 2
        time = np.arange(total) / sample_rate
        attack_frames = round(0.2 * sample_rate)
        release_frames = round(0.3 * sample_rate)
        envelope = np.ones(total)
        envelope[:attack_frames] = np.linspace(0.0, 1.0, attack_frames)
        envelope[note_off : note_off + release_frames] = np.linspace(
            1.0, 0.0, release_frames
        )
        envelope[note_off + release_frames :] = 0.0
        samples = 0.6 * envelope * np.sin(2.0 * math.pi * 220.0 * time)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "envelope.wav"
            write_pcm16(path, samples, sample_rate)
            result = analyze_wav(path, note_off_frame=note_off)
        features = result["features"]
        self.assertAlmostEqual(features["attack_10_to_90_seconds"], 0.16, delta=0.02)
        self.assertAlmostEqual(features["release_90_to_10_seconds"], 0.24, delta=0.03)
        self.assertFalse(features["release_censored_by_tail"])

    def test_release_is_explicitly_censored_when_tail_does_not_decay(self) -> None:
        sample_rate = 8_000
        note_off = sample_rate // 2
        time = np.arange(sample_rate) / sample_rate
        samples = 0.4 * np.sin(2.0 * math.pi * 220.0 * time)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sustain.wav"
            write_pcm16(path, samples, sample_rate)
            result = analyze_wav(path, note_off_frame=note_off)
        self.assertIsNone(result["features"]["release_90_to_10_seconds"])
        self.assertTrue(result["features"]["release_censored_by_tail"])

    def test_silence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silence.wav"
            write_pcm16(path, np.zeros(1_000), 8_000)
            with self.assertRaisesRegex(DSPError, "silent"):
                analyze_wav(path, note_off_frame=500)


class DSPMeasurementEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(MEASUREMENTS.read_text(encoding="utf-8"))
        cls.calibration = json.loads(CALIBRATION_RESULTS.read_text(encoding="utf-8"))

    def test_evidence_is_bound_to_completed_calibration(self) -> None:
        self.assertEqual(
            self.evidence["schema"], "agentic-synth-twin/dsp-measurements/v1"
        )
        expected_hash = hashlib.sha256(CALIBRATION_RESULTS.read_bytes()).hexdigest()
        self.assertEqual(
            self.evidence["source"]["calibration_results_sha256"], expected_hash
        )
        self.assertEqual(
            self.evidence["source"]["session_id"], self.calibration["session"]["id"]
        )
        source = self.evidence["source"]
        self.assertEqual(
            source["plugin"]["source_commit"],
            "f33b31fff459d66ac18207ec152b323aaa9306f9",
        )
        self.assertEqual(source["audition"]["note_name"], "C3")
        self.assertEqual(source["audition"]["sample_rate"], 44_100)
        self.assertEqual(
            source["accepted_wav_sha256"],
            hashlib.sha256(ACCEPTED_WAV.read_bytes()).hexdigest(),
        )

    def test_every_completed_probe_has_valid_measurements(self) -> None:
        measurements = self.evidence["measurements"]
        self.assertEqual(len(measurements), 10)
        self.assertEqual(
            [item["ordinal"] for item in measurements], list(range(1, 11))
        )
        accepted_hash = hashlib.sha256(ACCEPTED_WAV.read_bytes()).hexdigest()
        for item in measurements:
            self.assertEqual(item["A"]["sha256"], accepted_hash)
            self.assertNotEqual(item["A"]["sha256"], item["B"]["sha256"])
            verification = item["render_verification"]
            self.assertTrue(verification["A_byte_identical"])
            self.assertTrue(verification["B_byte_identical"])
            self.assertTrue(verification["A_matches_milestone_3"])
            self.assertEqual(verification["A_clipped_samples"], 0)
            self.assertEqual(verification["B_clipped_samples"], 0)
            for side in ("A", "B"):
                features = item[side]["features"]
                self.assertLessEqual(features["peak_dbfs"], 0.0)
                self.assertGreater(features["spectral_centroid_hz"], 0.0)
                self.assertGreater(features["spectral_rolloff_85_hz"], 0.0)
                self.assertGreaterEqual(features["attack_10_to_90_seconds"], 0.0)

    def test_human_labels_are_preserved_not_inferred(self) -> None:
        yes_names = {
            item["parameter_name"]
            for item in self.evidence["measurements"]
            if item["human_meaningful_difference"]
        }
        self.assertEqual(
            yes_names,
            {
                "Oscillator Detuning (in cents)",
                "Unison Spread in Cents",
                "Filter Type",
                "Cutoff in Keys",
                "Amplitude Attack (s)",
            },
        )


if __name__ == "__main__":
    unittest.main()

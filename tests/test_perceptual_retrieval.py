import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from agentic_synth_twin.perceptual_retrieval import (
    FAMILY_WEIGHTS,
    describe_audio,
    load_descriptor,
    normalize_and_rank,
    raw_family_distances,
    save_descriptor,
    score_with_frozen_medians,
)


def write_tone(path: Path, *, harmonics=(1.0,), gain=0.2, decay=0.0):
    sample_rate = 44_100
    frames = sample_rate * 5 // 2
    time = np.arange(frames) / sample_rate
    signal = np.zeros(frames)
    for number, level in enumerate(harmonics, start=1):
        signal += level * np.sin(2 * np.pi * 130.8127826502993 * number * time)
    if decay:
        signal *= np.exp(-decay * time)
    signal *= gain / max(float(np.max(np.abs(signal))), 1e-12)
    pcm = np.rint(signal * 32767).astype("<i2")
    stereo = np.column_stack((pcm, pcm)).reshape(-1)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(stereo.tobytes())


class PerceptualRetrievalTests(unittest.TestCase):
    def test_self_distance_is_exact_zero_before_cache_quantization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tone.wav"
            write_tone(path, harmonics=(1.0, 0.4, 0.2), decay=1.0)
            descriptor = describe_audio(path)
            distances = raw_family_distances(descriptor, descriptor)
            self.assertTrue(all(distances[name] == 0.0 for name in FAMILY_WEIGHTS))

    def test_analysis_normalization_removes_gain_from_ranking_families(self):
        with tempfile.TemporaryDirectory() as directory:
            quiet = Path(directory) / "quiet.wav"
            loud = Path(directory) / "loud.wav"
            write_tone(quiet, harmonics=(1.0, 0.3), gain=0.08, decay=0.8)
            write_tone(loud, harmonics=(1.0, 0.3), gain=0.6, decay=0.8)
            distances = raw_family_distances(describe_audio(quiet), describe_audio(loud))
            # PCM16 quantization leaves a low-level log-mel floor after gain scaling.
            self.assertLess(distances["log_mel"], 0.15)
            for family in ("envelope", "harmonic", "flatness"):
                self.assertLess(distances[family], 0.002)
            self.assertGreater(distances["absolute_rms_difference"], 0.1)

    def test_added_harmonics_and_changed_decay_move_expected_families(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / "plain.wav"
            bright = Path(directory) / "bright.wav"
            write_tone(plain, harmonics=(1.0,), decay=0.3)
            write_tone(bright, harmonics=(1.0, 0.8, 0.7, 0.6), decay=2.0)
            distances = raw_family_distances(describe_audio(plain), describe_audio(bright))
            self.assertGreater(distances["log_mel"], 0.1)
            self.assertGreater(distances["harmonic"], 0.001)
            self.assertGreater(distances["envelope"], 0.01)

    def test_cache_round_trip_and_uncapped_median_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            wav = Path(directory) / "tone.wav"
            cache = Path(directory) / "tone.npz"
            write_tone(wav, harmonics=(1.0, 0.2), decay=0.7)
            source = describe_audio(wav)
            save_descriptor(source, cache)
            restored = load_descriptor(cache)
            self.assertLess(raw_family_distances(source, restored)["log_mel"], 0.002)
        rows = [
            {"preset_relative_path": "a", "raw_distances": {name: 1.0 for name in FAMILY_WEIGHTS}},
            {"preset_relative_path": "b", "raw_distances": {name: 2.0 for name in FAMILY_WEIGHTS}},
            {"preset_relative_path": "c", "raw_distances": {name: 20.0 for name in FAMILY_WEIGHTS}},
        ]
        ranked, medians = normalize_and_rank(rows)
        self.assertTrue(all(value == 2.0 for value in medians.values()))
        self.assertEqual([row["preset_relative_path"] for row in ranked], ["a", "b", "c"])
        self.assertGreater(ranked[-1]["retrieval_score"], 1.0)

    def test_frozen_median_score_preserves_family_arithmetic(self):
        with tempfile.TemporaryDirectory() as directory:
            target_path = Path(directory) / "target.wav"
            candidate_path = Path(directory) / "candidate.wav"
            write_tone(target_path, harmonics=(1.0,), decay=0.2)
            write_tone(candidate_path, harmonics=(1.0, 0.6, 0.4), decay=1.7)
            target = describe_audio(target_path)
            candidate = describe_audio(candidate_path)
            medians = {
                "log_mel": 2.0,
                "envelope": 4.0,
                "harmonic": 5.0,
                "flatness": 10.0,
            }
            score = score_with_frozen_medians(target, candidate, medians)
        for family, weight in FAMILY_WEIGHTS.items():
            self.assertAlmostEqual(
                score["normalized_distances"][family],
                score["raw_distances"][family] / medians[family],
            )
            self.assertAlmostEqual(
                score["weighted_contributions"][family],
                weight * score["normalized_distances"][family],
            )
        self.assertAlmostEqual(
            score["retrieval_score"],
            sum(score["weighted_contributions"].values()),
        )


if __name__ == "__main__":
    unittest.main()

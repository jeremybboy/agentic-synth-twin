"""Contract checks for terminal-only human A/B calibration."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentic_synth_twin.ab_probe import (
    PROBE_SCHEMA,
    ProbeError,
    generate_probe,
    record_judgment,
)
from agentic_synth_twin.audition import (
    BITS_PER_SAMPLE,
    BLOCK_SIZE,
    CHANNELS,
    MIDI_KEY,
    NOTE_FRAMES,
    SAMPLE_RATE,
    TAIL_FRAMES,
    TOTAL_FRAMES,
    VELOCITY,
)


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "docs" / "evidence" / "milestone-2-canonical-state.json"
ACCEPTED_MANIFEST = ROOT / "docs" / "evidence" / "milestone-3-c3.json"
ACCEPTED_WAV = ROOT / "docs" / "evidence" / "milestone-3-c3.wav"
EVIDENCE_DIRECTORY = ROOT / "docs" / "evidence" / "milestone-4-unison-count"


def renderer_metadata(parameter_id: int | None = None, value: float | None = None):
    return {
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "bits_per_sample": BITS_PER_SAMPLE,
        "midi_key": MIDI_KEY,
        "velocity": VELOCITY,
        "note_frames": NOTE_FRAMES,
        "tail_frames": TAIL_FRAMES,
        "total_frames": TOTAL_FRAMES,
        "block_size": BLOCK_SIZE,
        "peak_float": 0.25,
        "clipped_samples": 0,
        "parameter_change": (
            None
            if parameter_id is None
            else {"id": parameter_id, "requested": value, "applied": value}
        ),
    }


class HumanABProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plugin = self.root / "test.clap"
        self.plugin.mkdir()
        self.renderer = self.root / "renderer"
        self.renderer.write_text("fake", encoding="utf-8")
        self.renderer.chmod(0o755)
        self.output = self.root / "probe"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fake_render(self, **kwargs):
        wav_path = kwargs["wav_path"]
        wav_bytes = bytearray(ACCEPTED_WAV.read_bytes())
        parameter_id = kwargs.get("parameter_id")
        parameter_value = kwargs.get("parameter_value")
        if parameter_id is not None:
            wav_bytes[-1] ^= 1
        wav_path.write_bytes(wav_bytes)
        digest = hashlib.sha256(wav_bytes).hexdigest()
        return (
            {
                "filename": wav_path.name,
                "sha256": digest,
                "pcm_sha256": "test-pcm",
                "byte_size": len(wav_bytes),
                "channels": CHANNELS,
                "bits_per_sample": BITS_PER_SAMPLE,
                "sample_rate": SAMPLE_RATE,
                "frames": TOTAL_FRAMES,
                "duration_seconds": TOTAL_FRAMES / SAMPLE_RATE,
                "non_silent": True,
                "peak_float": 0.25,
                "clipped_samples": 0,
            },
            renderer_metadata(parameter_id, parameter_value),
        )

    @patch("agentic_synth_twin.ab_probe.render_verified_wav")
    def test_generate_probe_records_real_inventory_and_unjudged_status(
        self, render
    ) -> None:
        render.side_effect = self.fake_render
        manifest = generate_probe(
            canonical_state_path=CANONICAL,
            accepted_manifest_path=ACCEPTED_MANIFEST,
            accepted_wav_path=ACCEPTED_WAV,
            plugin_path=self.plugin,
            renderer_path=self.renderer,
            output_directory=self.output,
            parameter_id=1378,
            parameter_value=7,
        )
        self.assertEqual(manifest["schema"], PROBE_SCHEMA)
        self.assertEqual(manifest["status"], "awaiting_human_judgment")
        self.assertEqual(manifest["parameter"]["name"], "Unison Count")
        self.assertEqual(manifest["parameter"]["baseline_value"], 3)
        self.assertEqual(manifest["parameter"]["variant_applied"], 7)
        self.assertTrue(manifest["A"]["matches_milestone_3"])
        self.assertNotEqual(
            manifest["A"]["wav"]["sha256"], manifest["B"]["wav"]["sha256"]
        )
        self.assertIsNone(manifest["human_judgment"])
        written = json.loads((self.output / "ab-probe.json").read_text(encoding="utf-8"))
        self.assertEqual(written, manifest)

    @patch("agentic_synth_twin.ab_probe.render_verified_wav")
    def test_record_judgment_preserves_human_authority(self, render) -> None:
        render.side_effect = self.fake_render
        generate_probe(
            canonical_state_path=CANONICAL,
            accepted_manifest_path=ACCEPTED_MANIFEST,
            accepted_wav_path=ACCEPTED_WAV,
            plugin_path=self.plugin,
            renderer_path=self.renderer,
            output_directory=self.output,
            parameter_id=1378,
            parameter_value=7,
        )
        recorded = record_judgment(
            manifest_path=self.output / "ab-probe.json",
            meaningful_difference=True,
        )
        self.assertEqual(recorded["status"], "completed")
        self.assertTrue(recorded["human_judgment"]["meaningful_difference"])
        self.assertEqual(recorded["human_judgment"]["authority"], "human_listener")
        with self.assertRaises(ProbeError):
            record_judgment(
                manifest_path=self.output / "ab-probe.json",
                meaningful_difference=False,
            )

    def test_rejects_unknown_parameter_before_rendering(self) -> None:
        with self.assertRaisesRegex(ProbeError, "not in the real inventory"):
            generate_probe(
                canonical_state_path=CANONICAL,
                accepted_manifest_path=ACCEPTED_MANIFEST,
                accepted_wav_path=ACCEPTED_WAV,
                plugin_path=self.plugin,
                renderer_path=self.renderer,
                output_directory=self.output,
                parameter_id=999,
                parameter_value=1,
            )

    def test_rejects_baseline_or_out_of_range_variant(self) -> None:
        for value in (3, 8):
            with self.subTest(value=value), self.assertRaises(ProbeError):
                generate_probe(
                    canonical_state_path=CANONICAL,
                    accepted_manifest_path=ACCEPTED_MANIFEST,
                    accepted_wav_path=ACCEPTED_WAV,
                    plugin_path=self.plugin,
                    renderer_path=self.renderer,
                    output_directory=self.output,
                    parameter_id=1378,
                    parameter_value=value,
                )


class CompletedProbeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(
            (EVIDENCE_DIRECTORY / "ab-probe.json").read_text(encoding="utf-8")
        )

    def test_probe_is_completed_by_human_listener(self) -> None:
        self.assertEqual(self.manifest["schema"], PROBE_SCHEMA)
        self.assertEqual(self.manifest["status"], "completed")
        judgment = self.manifest["human_judgment"]
        self.assertTrue(judgment["meaningful_difference"])
        self.assertEqual(judgment["authority"], "human_listener")
        self.assertIn("volume", judgment["notes"])

    def test_probe_changes_only_the_declared_inventory_parameter(self) -> None:
        parameter = self.manifest["parameter"]
        self.assertEqual(parameter["id"], 1378)
        self.assertEqual(parameter["name"], "Unison Count")
        self.assertEqual(parameter["baseline_value"], 3)
        self.assertEqual(parameter["variant_value"], 7)
        self.assertEqual(parameter["variant_applied"], 7)

    def test_committed_wavs_match_manifest_and_are_distinct(self) -> None:
        hashes = []
        for side, filename in (("A", "A-baseline.wav"), ("B", "B-variant.wav")):
            path = EVIDENCE_DIRECTORY / filename
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual, self.manifest[side]["wav"]["sha256"])
            self.assertEqual(self.manifest[side]["wav"]["clipped_samples"], 0)
            hashes.append(actual)
        self.assertEqual(
            hashes[0], hashlib.sha256(ACCEPTED_WAV.read_bytes()).hexdigest()
        )
        self.assertNotEqual(hashes[0], hashes[1])

    def test_each_side_records_two_byte_identical_renders(self) -> None:
        determinism = self.manifest["determinism"]
        self.assertEqual(determinism["renders_per_side"], 2)
        self.assertTrue(determinism["A_byte_identical"])
        self.assertTrue(determinism["B_byte_identical"])


if __name__ == "__main__":
    unittest.main()

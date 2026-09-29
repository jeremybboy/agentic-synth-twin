import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentic_synth_twin.audition import (
    BITS_PER_SAMPLE,
    BLOCK_SIZE,
    CHANNELS,
    NOTE_FRAMES,
    SAMPLE_RATE,
    TAIL_FRAMES,
)
from agentic_synth_twin.synth_adapter import (
    ClapSynthAdapter,
    SurgeXTAdapter,
    SynthAdapterError,
    sha256_file,
)


class SynthAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plugin = self.root / "Example.clap"
        self.plugin.mkdir()
        self.renderer = self.root / "renderer"
        self.probe = self.root / "probe"
        for executable in (self.renderer, self.probe):
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
        self.state = self.root / "state.bin"
        self.state.write_bytes(b"state")

    def tearDown(self):
        self.temporary.cleanup()

    def _adapter(self):
        return ClapSynthAdapter(
            plugin_path=self.plugin,
            renderer_path=self.renderer,
            probe_path=self.probe,
            deterministic_overrides={99: 1.0},
        )

    def test_render_contract_applies_values_and_deterministic_overrides(self):
        output = self.root / "note.wav"

        def fake_run(command, **_kwargs):
            output_path = Path(command[3])
            output_path.write_bytes(b"same deterministic wav")
            values = [(int(command[i]), float(command[i + 1])) for i in range(10, len(command), 2)]
            metadata = {
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "bits_per_sample": BITS_PER_SAMPLE,
                "midi_key": 48,
                "velocity": 100,
                "note_frames": NOTE_FRAMES,
                "tail_frames": TAIL_FRAMES,
                "total_frames": NOTE_FRAMES + TAIL_FRAMES,
                "block_size": BLOCK_SIZE,
                "clipped_samples": 0,
                "peak_float": 0.5,
                "parameter_changes": [
                    {"id": key, "requested": value, "applied": value}
                    for key, value in values
                ],
            }
            return type("Completed", (), {"stdout": json.dumps(metadata), "stderr": ""})()

        with patch("agentic_synth_twin.synth_adapter.subprocess.run", side_effect=fake_run):
            result = self._adapter().render_verified(
                state_path=self.state,
                wav_path=output,
                parameter_values={7: 0.25},
                midi_key=48,
                velocity=100,
                note_frames=NOTE_FRAMES,
            )
        self.assertTrue(result["byte_identical"])
        self.assertEqual(result["applied_values"], {"7": 0.25, "99": 1.0})
        self.assertEqual(result["wav_sha256"], sha256_file(output))

    def test_playable_bounds_are_enforced_before_process_launch(self):
        with self.assertRaisesRegex(SynthAdapterError, "MIDI key"):
            self._adapter().render_note(
                state_path=self.state,
                wav_path=self.root / "bad.wav",
                parameter_values={},
                midi_key=128,
                velocity=100,
                note_frames=NOTE_FRAMES,
            )

    def test_record_mode_preserves_requested_and_real_applied_values(self):
        output = self.root / "coerced.wav"

        def fake_run(command, **_kwargs):
            self.assertIn("--parameter-retention", command)
            output.write_bytes(b"coerced real synth wav")
            metadata = {
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "bits_per_sample": BITS_PER_SAMPLE,
                "midi_key": 48,
                "velocity": 100,
                "note_frames": NOTE_FRAMES,
                "tail_frames": TAIL_FRAMES,
                "total_frames": NOTE_FRAMES + TAIL_FRAMES,
                "block_size": BLOCK_SIZE,
                "clipped_samples": 0,
                "peak_float": 0.5,
                "parameter_changes": [
                    {"id": 7, "requested": 0.26, "applied": 0.25},
                    {"id": 99, "requested": 1.0, "applied": 1.0},
                ],
            }
            return type("Completed", (), {"stdout": json.dumps(metadata), "stderr": ""})()

        with patch("agentic_synth_twin.synth_adapter.subprocess.run", side_effect=fake_run):
            result = self._adapter().render_note(
                state_path=self.state,
                wav_path=output,
                parameter_values={7: 0.26},
                midi_key=48,
                velocity=100,
                note_frames=NOTE_FRAMES,
                allow_parameter_coercion=True,
            )
        self.assertEqual(result["requested_values"], {"7": 0.26, "99": 1.0})
        self.assertEqual(result["applied_values"], {"7": 0.25, "99": 1.0})
        self.assertEqual(
            result["coerced_parameters"],
            [{"id": 7, "requested": 0.26, "applied": 0.25}],
        )


class SurgePresetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.plugin = root / "Surge XT.clap"
        self.plugin.mkdir()
        self.renderer = root / "renderer"
        self.probe = root / "probe"
        for executable in (self.renderer, self.probe):
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
        self.data = root / "SurgeXTData"
        self.presets = self.data / "patches_factory"
        for category in ("Bass", "Keys", "Pads"):
            directory = self.presets / category
            directory.mkdir(parents=True)
            for index in range(2):
                (directory / f"{category}-{index}.fxp").write_bytes(
                    b"FXP wrapper"
                    + b"sub3<?xml version='1.0'?><state>"
                    + b"x" * 64
                    + b"</state>"
                )
        self.adapter = SurgeXTAdapter(
            plugin_path=self.plugin,
            renderer_path=self.renderer,
            probe_path=self.probe,
            factory_data_path=self.data,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_bounded_factory_selection_is_balanced_and_can_require_a_preset(self):
        selected = self.adapter.bounded_factory_presets(
            4, required_relative_path="Pads/Pads-1.fxp"
        )
        self.assertEqual(len(selected), 4)
        self.assertIn("Pads/Pads-1.fxp", [item.relative_path for item in selected])
        self.assertGreaterEqual(len({item.category for item in selected}), 2)

    def test_native_state_extraction_is_hashed_and_has_no_fxp_wrapper(self):
        preset = self.adapter.factory_presets()[0]
        destination = Path(self.temporary.name) / "state.bin"
        evidence = self.adapter.extract_preset_state(preset, destination)
        self.assertTrue(destination.read_bytes().startswith(b"sub3<?xml"))
        self.assertEqual(evidence["state_sha256"], sha256_file(destination))


if __name__ == "__main__":
    unittest.main()

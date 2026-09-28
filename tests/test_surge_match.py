import tempfile
import unittest
from pathlib import Path

from agentic_synth_twin.search_cockpit import render_cockpit_page
from agentic_synth_twin.surge_match import (
    ACTIVE_PARAMETER_IDS,
    DETERMINISTIC_OVERRIDES,
    SurgeMatchError,
    SurgeMatchRun,
    discover_active_parameters,
    vector_from_current,
    vector_to_real,
)


def inventory():
    names = {
        627_352_114: "A Osc 1 Shape",
        627_352_115: "A Osc 1 Width 1",
        627_352_119: "A Osc 1 Unison Detune",
        4_092_842_705: "A Filter 1 Cutoff",
        8_095_466: "A Filter 1 Resonance",
        2_005_836_581: "A Amp EG Attack",
        3_946_337_085: "A Amp EG Decay",
        3_817_344_714: "A Amp EG Release",
    }
    return {
        "parameters": [
            {
                "id": parameter_id,
                "name": names[parameter_id],
                "module": "/A/",
                "min": 0.0,
                "max": 1.0,
                "default": 0.0,
                "current": 0.25,
                "flags": 0,
            }
            for parameter_id in ACTIVE_PARAMETER_IDS
        ]
    }


class SurgeMatchTests(unittest.TestCase):
    def test_parameter_mapping_uses_exact_real_inventory(self):
        parameters = discover_active_parameters(inventory())
        self.assertEqual([item["id"] for item in parameters], list(ACTIVE_PARAMETER_IDS))
        self.assertTrue(all(item["approval"] == "SEARCH" for item in parameters))
        self.assertEqual(vector_from_current(parameters), [0.25] * 8)
        self.assertEqual(len(vector_to_real([0.5] * 8, parameters)), 8)

    def test_parameter_identity_drift_is_rejected(self):
        changed = inventory()
        changed["parameters"][0]["name"] = "invented semantic"
        with self.assertRaisesRegex(SurgeMatchError, "identity changed"):
            discover_active_parameters(changed)

    def test_keyboard_and_working_patch_remain_in_the_same_cockpit(self):
        page = render_cockpit_page()
        self.assertIn('id="piano"', page)
        self.assertIn("const computerNotes={'a':0,'w':1", page)
        self.assertIn("CONNECT USB MIDI", page)
        self.assertIn("LOAD CURRENT BEST", page)
        self.assertIn("LOAD BASE", page)
        self.assertIn("/api/playable/note", page)
        self.assertIn("Closest factory presets", page)

    def test_playable_note_keeps_piano_and_velocity_bounds_and_caches(self):
        class FakeAdapter:
            def __init__(self):
                self.calls = 0

            def render_note(self, *, wav_path, **_kwargs):
                self.calls += 1
                Path(wav_path).write_bytes(b"audio")

        with tempfile.TemporaryDirectory() as directory:
            run = SurgeMatchRun.__new__(SurgeMatchRun)
            run.parameters = discover_active_parameters(inventory())
            run.start_vector = [0.25] * 8
            run.best = None
            run.base_state_path = Path(directory) / "base.bin"
            run.base_state_path.write_bytes(b"base")
            run.target_preset_provenance = {"state_path": str(run.base_state_path)}
            run.audio_directory = Path(directory)
            run.adapter = FakeAdapter()
            import threading

            run._playable_lock = threading.Lock()
            first = run.render_playable_note(
                source="starting", midi_key=48, velocity=100
            )
            second = run.render_playable_note(
                source="starting", midi_key=48, velocity=100
            )
            self.assertEqual(first, second)
            self.assertEqual(run.adapter.calls, 1)
            with self.assertRaisesRegex(SurgeMatchError, "piano range"):
                run.render_playable_note(source="starting", midi_key=20, velocity=100)
            with self.assertRaisesRegex(SurgeMatchError, "velocity"):
                run.render_playable_note(source="starting", midi_key=48, velocity=0)

    def test_deterministic_overrides_cover_both_scenes(self):
        self.assertEqual(len(DETERMINISTIC_OVERRIDES), 6)
        self.assertEqual(set(DETERMINISTIC_OVERRIDES.values()), {1.0})


if __name__ == "__main__":
    unittest.main()

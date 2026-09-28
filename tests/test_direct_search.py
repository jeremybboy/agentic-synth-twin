"""Milestone 8A direct real-synth search contracts."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

import numpy as np

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Could not import matplotlib.pyplot.*")
    import cma

from agentic_synth_twin.direct_search import (
    ACTIVE_PARAMETER_IDS,
    DirectSearchError,
    SearchRun,
    benchmark_random_search,
    compute_audio_objective,
    denormalize_value,
    discover_search_parameters,
    normalize_value,
)
from agentic_synth_twin.synth_state import load_canonical_state


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "docs" / "evidence" / "milestone-2-canonical-state.json"
BASELINE = ROOT / "docs" / "evidence" / "milestone-3-c3.wav"
UNISON_VARIANT = (
    ROOT
    / "docs"
    / "evidence"
    / "milestone-4-unison-count"
    / "B-variant.wav"
)


class DirectSearchTests(unittest.TestCase):
    def test_search_parameters_and_ranges_come_from_real_inventory(self) -> None:
        parameters = discover_search_parameters(load_canonical_state(STATE))
        self.assertEqual(
            [item["id"] for item in parameters[:4]], list(ACTIVE_PARAMETER_IDS)
        )
        self.assertEqual(
            [(item["min"], item["max"]) for item in parameters[:4]],
            [(-200, 200), (0, 100), (1, 127), (0, 1)],
        )
        self.assertEqual(parameters[-1]["id"], 14255)
        self.assertTrue(parameters[-1]["fixed"])

    def test_normalization_round_trip_and_bounds(self) -> None:
        parameter = {"min": -200, "max": 200}
        for value in (-200, -17.5, 0, 123.25, 200):
            normalized = normalize_value(value, parameter)
            self.assertAlmostEqual(denormalize_value(normalized, parameter), value)
        with self.assertRaisesRegex(DirectSearchError, "outside"):
            normalize_value(201, parameter)
        with self.assertRaisesRegex(DirectSearchError, "outside"):
            denormalize_value(-0.01, parameter)

    def test_objective_is_deterministic_and_zero_for_identical_audio(self) -> None:
        first = compute_audio_objective(BASELINE, BASELINE)
        second = compute_audio_objective(BASELINE, BASELINE)
        self.assertEqual(first, second)
        self.assertEqual(first["spectral_loss"], 0.0)
        self.assertEqual(first["envelope_loss"], 0.0)
        self.assertEqual(first["loudness_loss"], 0.0)
        self.assertEqual(first["total_loss"], 0.0)

    def test_objective_detects_a_verified_real_synth_difference(self) -> None:
        objective = compute_audio_objective(BASELINE, UNISON_VARIANT)
        self.assertGreater(objective["spectral_loss"], 0.0)
        self.assertGreater(objective["total_loss"], 0.0)

    def test_pycma_ask_tell_is_seed_reproducible(self) -> None:
        def trace(seed: int) -> list[list[float]]:
            optimizer = cma.CMAEvolutionStrategy(
                [0.5] * 4,
                0.2,
                {
                    "bounds": [0.0, 1.0],
                    "seed": seed,
                    "popsize": 4,
                    "verbose": -9,
                    "verb_log": 0,
                    "verb_disp": 0,
                },
            )
            values: list[list[float]] = []
            for _ in range(3):
                candidates = optimizer.ask()
                values.extend([[round(float(v), 12) for v in row] for row in candidates])
                losses = [float(np.sum((np.asarray(row) - 0.7) ** 2)) for row in candidates]
                optimizer.tell(candidates, losses)
            return values

        self.assertEqual(trace(20260928), trace(20260928))
        self.assertNotEqual(trace(20260928), trace(20260929))

    def _fake_run(self, root: Path) -> SearchRun:
        plugin = root / "fake.clap"
        plugin.mkdir()
        renderer = root / "renderer"
        renderer.write_text("#!/bin/sh\n", encoding="utf-8")
        renderer.chmod(0o755)

        def prepare(instance: SearchRun) -> None:
            instance.state_path.write_bytes(b"state")
            instance.reference = object()
            instance.target = {
                "wav_sha256": "target-hash",
                "byte_identical": True,
                "target_parameters_private": {
                    "normalized": [0.72, 0.68, 0.28, 0.62],
                    "real": {},
                },
            }
            instance.start = {
                "wav_sha256": "start-hash",
                "byte_identical": True,
                "total_loss": 1.0,
            }

        with mock.patch.object(SearchRun, "_prepare_fixed_audio", prepare):
            return SearchRun(
                canonical_state_path=STATE,
                plugin_path=plugin,
                renderer_path=renderer,
                output_directory=root / "run",
                seed=4,
                generations=2,
                population_size=2,
            )

    def test_target_secrecy_and_control_state_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self._fake_run(Path(directory))
            self.assertNotIn("target_parameters", run.public_status())
            with self.assertRaisesRegex(DirectSearchError, "hidden"):
                run.reveal_target()
            run.status = "SEARCHING"
            self.assertEqual(run.pause_search()["status"], "PAUSING")
            run.status = "PAUSED"
            self.assertEqual(run.resume_search()["status"], "SEARCHING")
            run.status = "PAUSED"
            self.assertEqual(run.stop_search()["status"], "PAUSED")
            run.status = "STOPPED"
            revealed = run.reveal_target()
            self.assertTrue(revealed["target_revealed"])
            self.assertEqual(
                revealed["target_parameters"]["normalized"],
                [0.72, 0.68, 0.28, 0.62],
            )

    def test_best_so_far_and_candidate_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self._fake_run(Path(directory))
            render_result = {
                "parameter_values_normalized": [0.1, 0.2, 0.3, 0.4],
                "parameter_values_real": {str(value): 0.5 for value in ACTIVE_PARAMETER_IDS},
                "wav_sha256": "candidate-hash",
                "peak_float": 0.2,
                "clipped_samples": 0,
            }
            objectives = iter(
                [
                    {
                        "spectral_loss": 0.2,
                        "envelope_loss": 0.2,
                        "loudness_loss": 0.2,
                        "total_loss": 0.2,
                        "candidate_rms_dbfs": -20.0,
                        "target_rms_dbfs": -22.0,
                    },
                    {
                        "spectral_loss": 0.4,
                        "envelope_loss": 0.4,
                        "loudness_loss": 0.4,
                        "total_loss": 0.4,
                        "candidate_rms_dbfs": -18.0,
                        "target_rms_dbfs": -22.0,
                    },
                ]
            )
            with mock.patch(
                "agentic_synth_twin.direct_search.render_candidate",
                return_value=render_result,
            ), mock.patch(
                "agentic_synth_twin.direct_search.compute_audio_objective",
                side_effect=lambda *_: next(objectives),
            ):
                first = run._evaluate([0.1, 0.2, 0.3, 0.4], generation=1, candidate_index=1)
                second = run._evaluate([0.4, 0.3, 0.2, 0.1], generation=1, candidate_index=2)
            self.assertTrue(first["is_new_best"])
            self.assertFalse(second["is_new_best"])
            self.assertEqual(run.best["total_loss"], 0.2)
            self.assertEqual(len(run.history), 2)
            self.assertEqual(first["run_id"], run.run_id)
            self.assertEqual(first["wav_sha256"], "candidate-hash")

    def test_playable_note_is_bounded_and_content_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self._fake_run(Path(directory))

            def fake_playable_render(**kwargs: object) -> dict[str, object]:
                Path(kwargs["wav_path"]).write_bytes(b"RIFF-test")
                return {}

            with mock.patch(
                "agentic_synth_twin.direct_search._run_playable_renderer",
                side_effect=fake_playable_render,
            ) as renderer:
                first = run.render_playable_note(
                    source="custom",
                    midi_key=60,
                    velocity=96,
                    normalized=[0.1, 0.2, 0.3, 0.4],
                )
                second = run.render_playable_note(
                    source="custom",
                    midi_key=60,
                    velocity=96,
                    normalized=[0.1, 0.2, 0.3, 0.4],
                )
            self.assertEqual(first, second)
            self.assertEqual(first.read_bytes(), b"RIFF-test")
            renderer.assert_called_once()
            self.assertEqual(renderer.call_args.kwargs["midi_key"], 60)
            self.assertEqual(renderer.call_args.kwargs["velocity"], 96)
            with self.assertRaisesRegex(DirectSearchError, "piano range"):
                run.render_playable_note(source="starting", midi_key=20, velocity=96)
            with self.assertRaisesRegex(DirectSearchError, "velocity"):
                run.render_playable_note(source="starting", midi_key=60, velocity=0)
            with self.assertRaisesRegex(DirectSearchError, "four normalized"):
                run.render_playable_note(
                    source="custom", midi_key=60, velocity=96, normalized=[0.1]
                )
            with self.assertRaisesRegex(DirectSearchError, "within 0..1"):
                run.render_playable_note(
                    source="custom",
                    midi_key=60,
                    velocity=96,
                    normalized=[0.1, 0.2, 0.3, 1.1],
                )

    def test_random_search_control_is_seeded_and_equal_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self._fake_run(Path(directory))

            def fake_render(**kwargs: object) -> dict[str, object]:
                normalized = list(kwargs["normalized"])
                return {
                    "parameter_values_normalized": normalized,
                    "parameter_values_real": {
                        str(value): normalized[index]
                        for index, value in enumerate(ACTIVE_PARAMETER_IDS)
                    },
                    "wav_sha256": "hash",
                    "peak_float": 0.1,
                    "clipped_samples": 0,
                }

            def fake_objective(_: object, path: object) -> dict[str, float]:
                index = int(Path(path).stem.split("-")[-1])
                loss = 0.5 - index * 0.01
                return {
                    "spectral_loss": loss,
                    "envelope_loss": loss,
                    "loudness_loss": loss,
                    "total_loss": loss,
                    "candidate_rms_dbfs": -20.0,
                    "target_rms_dbfs": -20.0,
                }

            with mock.patch(
                "agentic_synth_twin.direct_search.render_candidate",
                side_effect=fake_render,
            ), mock.patch(
                "agentic_synth_twin.direct_search.compute_audio_objective",
                side_effect=fake_objective,
            ):
                result = benchmark_random_search(run, budget=4, seed=55)
            self.assertEqual(result["budget"], 4)
            self.assertEqual(len(result["history"]), 4)
            self.assertEqual(result["best"]["evaluation"], 4)
            saved = json.loads(
                (run.output_directory / "random-search" / "result.json").read_text()
            )
            self.assertEqual(saved["seed"], 55)


if __name__ == "__main__":
    unittest.main()

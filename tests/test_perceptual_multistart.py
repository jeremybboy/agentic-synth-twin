"""Contract tests for adaptive perceptual multi-start refinement."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agentic_synth_twin.perceptual_multistart import (
    DEEPEN_EVALUATIONS,
    MAX_DIMENSIONS,
    PILOT_EVALUATIONS,
    PerceptualMultiStartEngine,
    derive_seed,
    discover_continuous_parameter_pool,
    group_parameters_by_module,
    local_bounds,
    probe_values,
    rank_pilot_trajectories,
    screen_group_from_metadata,
    select_adaptive_parameters,
    structural_parameter_screen,
)
from agentic_synth_twin.synth_adapter import SynthAdapterError


def parameter(parameter_id: int, *, current: float = 0.5, flags: int = 0) -> dict:
    return {
        "id": parameter_id,
        "name": f"Real {parameter_id}",
        "module": "/Scene A/",
        "flags": flags,
        "min": 0.0,
        "max": 1.0,
        "default": 0.25,
        "current": current,
        "normalized_base": current,
    }


def probe(
    parameter_id: int,
    *,
    gain: float,
    influence: float,
    family: str | None = None,
) -> dict:
    reductions = {
        "log_mel": 0.0,
        "envelope": 0.0,
        "harmonic": 0.0,
        "flatness": 0.0,
    }
    if family:
        reductions[family] = gain
    return {
        "parameter": parameter(parameter_id),
        "responsive": True,
        "target_aligned_gain": gain,
        "influence_magnitude": influence,
        "family_reductions": reductions,
    }


class PerceptualMultiStartTests(unittest.TestCase):
    def test_pool_excludes_stepped_hidden_readonly_and_invalid_controls(self):
        inventory = {
            "parameters": [
                parameter(1),
                parameter(2, flags=1),
                parameter(3, flags=4),
                parameter(4, flags=8),
                {**parameter(5), "min": 1.0, "max": 1.0},
                {**parameter(6), "current": float("nan")},
            ]
        }
        pool = discover_continuous_parameter_pool(inventory)
        self.assertEqual([row["id"] for row in pool], [1])
        self.assertEqual(pool[0]["name"], "Real 1")

    def test_structural_screen_records_every_rule_and_real_module(self):
        fixed = parameter(9)
        inventory = {
            "parameters": [
                parameter(1),
                {**parameter(2), "name": "Bypass Surge XT", "module": ""},
                {**parameter(3), "name": "M1: -", "module": "/Macros/"},
                {**parameter(4), "name": "Scene Mode", "module": "/Global & FX/"},
                fixed,
                {
                    **parameter(10),
                    "name": "FX B1 FX Type",
                    "module": "/Global & FX/",
                    "current_text": "Off",
                },
                {
                    **parameter(11),
                    "name": "FX B1 Param 1",
                    "module": "/Global & FX/",
                    "current_text": "0%",
                },
            ]
        }
        pool, ledger = structural_parameter_screen(
            inventory, host_fixed_parameter_ids=[9]
        )
        self.assertEqual([row["id"] for row in pool], [1])
        self.assertEqual(len(ledger), len(inventory["parameters"]))
        rules = {row["id"]: row["rule"] for row in ledger}
        self.assertEqual(rules[2], "PLUGIN_NAMED_BYPASS_CONTROL")
        self.assertEqual(rules[3], "PLUGIN_NAMED_UNASSIGNED_MACRO")
        self.assertEqual(rules[4], "PLUGIN_NAMED_ROUTING_OR_CAPACITY_CONTROL")
        self.assertEqual(rules[9], "HOST_FIXED_DETERMINISTIC_CONTROL")
        self.assertEqual(rules[10], "CLAP_VALUE_TEXT_INACTIVE_FX_SLOT")
        self.assertEqual(rules[11], "CLAP_VALUE_TEXT_INACTIVE_FX_SLOT")
        self.assertEqual(list(group_parameters_by_module(pool)), ["/Scene A/"])

    def test_broad_real_modules_split_only_on_explicit_name_prefixes(self):
        self.assertEqual(
            screen_group_from_metadata("/A LFOs/", "A LFO 3 Rate"),
            ("/A LFOs/A LFO 3", "LFO_NUMBER_PREFIX"),
        )
        self.assertEqual(
            screen_group_from_metadata("/Global & FX/", "FX S2 Output - Mix"),
            ("/Global & FX/FX S2", "FX_SLOT_PREFIX"),
        )
        self.assertEqual(
            screen_group_from_metadata("/A Common/", "A Feedback"),
            ("/A Common/", "CLAP_MODULE_PATH"),
        )

    def test_probe_and_local_bounds_clip_deterministically(self):
        self.assertEqual(probe_values(0.02), {"minus": 0.0, "plus": 0.07})
        self.assertEqual(probe_values(0.98), {"minus": 0.93, "plus": 1.0})
        self.assertEqual(local_bounds(0.1), (0.0, 0.3))
        self.assertEqual(local_bounds(0.9), (0.7, 1.0))

    def test_selection_keeps_family_representatives_gain_and_interactions(self):
        rows = [
            probe(1, gain=0.01, influence=0.10, family="log_mel"),
            probe(2, gain=0.02, influence=0.11, family="envelope"),
            probe(3, gain=0.03, influence=0.12, family="harmonic"),
            probe(4, gain=0.04, influence=0.13, family="flatness"),
        ]
        rows.extend(
            probe(index, gain=0.02 - index / 10_000, influence=index / 100)
            for index in range(5, 17)
        )
        selected = select_adaptive_parameters(
            rows,
            {"log_mel": 1.0, "envelope": 1.0, "harmonic": 1.0, "flatness": 1.0},
        )
        selected_ids = [row["id"] for row in selected]
        self.assertTrue({1, 2, 3, 4}.issubset(selected_ids))
        self.assertEqual(len(selected), MAX_DIMENSIONS)
        self.assertEqual(len(selected_ids), len(set(selected_ids)))
        self.assertTrue(all("local_bounds" in row for row in selected))

    def test_unresponsive_controls_never_enter_the_frozen_space(self):
        rows = [probe(index, gain=0.1, influence=1.0) for index in range(1, 6)]
        rows[0]["responsive"] = False
        selected = select_adaptive_parameters(
            rows,
            {"log_mel": 1.0, "envelope": 0.0, "harmonic": 0.0, "flatness": 0.0},
        )
        self.assertNotIn(1, [row["id"] for row in selected])

    def test_hierarchy_individually_probes_only_responsive_real_subgroups(self):
        class ScreenAdapter:
            deterministic_overrides = {}

            def inspect_state(self, _state_path):
                rows = []
                for parameter_id, name, module in (
                    (1, "A Filter 1 Cutoff", "/A Filters/"),
                    (2, "A Filter 1 Resonance", "/A Filters/"),
                    (3, "A LFO 2 Rate", "/A LFOs/"),
                    (4, "A LFO 2 Phase", "/A LFOs/"),
                ):
                    rows.append(
                        {
                            **parameter(parameter_id),
                            "name": name,
                            "module": module,
                        }
                    )
                return {"parameters": rows}

            def render_note(self, *, wav_path, parameter_values, **_kwargs):
                path = Path(wav_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                if any(int(key) in {3, 4} for key in parameter_values):
                    payload = b"base"
                else:
                    payload = json.dumps(parameter_values, sort_keys=True).encode()
                path.write_bytes(payload)
                applied = {str(key): float(value) for key, value in parameter_values.items()}
                coercions = []
                if 2 in parameter_values:
                    coercions.append(
                        {"id": 2, "requested": float(parameter_values[2]), "applied": 0.5}
                    )
                    applied["2"] = 0.5
                return {
                    "wav_sha256": hashlib.sha256(payload).hexdigest(),
                    "peak_float": 0.5,
                    "clipped_samples": 0,
                    "applied_values": applied,
                    "coerced_parameters": coercions,
                }

        class ScreenEngine(PerceptualMultiStartEngine):
            def _score_path(self, path):
                payload = Path(path).read_bytes()
                if payload in {b"base", b"{}"}:
                    score = 1.0
                else:
                    values = json.loads(payload)
                    score = 0.8 if float(values.get("1", values.get(1, 0.5))) != 0.5 else 1.0
                contributions = {
                    "log_mel": score * 0.55,
                    "envelope": score * 0.25,
                    "harmonic": score * 0.15,
                    "flatness": score * 0.05,
                }
                return {
                    "raw_distances": contributions,
                    "normalized_distances": contributions,
                    "weighted_contributions": contributions,
                    "retrieval_score": score,
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.bin"
            state.write_bytes(b"state")
            base = root / "base.wav"
            base.write_bytes(b"base")
            target = root / "target.wav"
            target.write_bytes(b"target")
            contributions = {
                "log_mel": 0.55,
                "envelope": 0.25,
                "harmonic": 0.15,
                "flatness": 0.05,
            }
            engine = ScreenEngine(
                adapter=ScreenAdapter(),
                target_path=target,
                target_descriptor=None,
                family_medians={key: 1.0 for key in contributions},
                perceptual_ranking=[],
                output_directory=root / "out",
                probe_workers=1,
            )
            result = engine._screen_start(
                {
                    "perceptual_rank": 1,
                    "preset_name": "Fake",
                    "preset_category": "Tests",
                    "preset_relative_path": "Tests/Fake.fxp",
                    "state_path": str(state),
                    "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
                    "audio_path": str(base),
                    "wav_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
                    "retrieval_score": 1.0,
                    "raw_distances": contributions,
                    "normalized_distances": contributions,
                    "weighted_contributions": contributions,
                },
                1,
            )
        self.assertEqual(result["responsive_module_count"], 1)
        self.assertEqual(result["individual_parameters_probed"], 1)
        self.assertEqual(result["individual_probe_count"], 3)
        self.assertEqual(
            {row["parameter"]["id"] for row in result["probes"]}, {1}
        )
        self.assertEqual(
            [row["id"] for row in result["module_behavior_exclusions"]], [2]
        )

    def test_seed_budget_and_pilot_ranking_are_absolute(self):
        self.assertEqual(derive_seed(100, 4), 104)
        self.assertEqual(PILOT_EVALUATIONS, 32)
        self.assertEqual(DEEPEN_EVALUATIONS, 64)
        trajectories = [
            {"optimization_start_index": 1, "perceptual_rank": 1, "base_score": 0.1,
             "best": {"retrieval_score": 0.09}},
            {"optimization_start_index": 2, "perceptual_rank": 2, "base_score": 1.0,
             "best": {"retrieval_score": 0.08}},
            {"optimization_start_index": 3, "perceptual_rank": 3, "base_score": 0.3,
             "best": {"retrieval_score": 0.08}},
        ]
        ranked = rank_pilot_trajectories(trajectories)
        self.assertEqual([row["optimization_start_index"] for row in ranked], [3, 2, 1])

    def test_full_scheduler_continues_two_states_and_separates_stable_best(self):
        class FakeAdapter:
            deterministic_overrides = {}

            def __init__(self):
                self.fail_next = True
                self.verification_calls = 0

            def render_note(self, *, wav_path, parameter_values, **_kwargs):
                if self.fail_next:
                    self.fail_next = False
                    raise SynthAdapterError("declared fake render failure")
                path = Path(wav_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(
                    {str(key): value for key, value in sorted(parameter_values.items())},
                    sort_keys=True,
                ).encode()
                if path.parent.name.startswith(".verify-"):
                    self.verification_calls += 1
                    if self.verification_calls <= 2:
                        payload += f"-unstable-{self.verification_calls}".encode()
                path.write_bytes(payload)
                digest = hashlib.sha256(payload).hexdigest()
                return {
                    "wav_sha256": digest,
                    "peak_float": 0.5,
                    "clipped_samples": 0,
                    "applied_values": {
                        str(key): float(value) for key, value in parameter_values.items()
                    },
                }

        class FakeEngine(PerceptualMultiStartEngine):
            def _screen_start(self, row, start_index):
                state = self.output_directory / f"state-{start_index}.bin"
                audio = self.output_directory / f"base-{start_index}.wav"
                state.write_bytes(f"state-{start_index}".encode())
                audio.write_text("{}", encoding="utf-8")
                contributions = {
                    "log_mel": 0.55,
                    "envelope": 0.25,
                    "harmonic": 0.15,
                    "flatness": 0.05,
                }
                parameters = [
                    {
                        **parameter(start_index * 10 + offset),
                        "local_bounds": [0.3, 0.7],
                        "target_aligned_gain": 0.1,
                        "influence_magnitude": 1.0,
                        "family_reductions": contributions,
                        "probe_scores": {"minus": 1.1, "plus": 0.9},
                        "main_family_effect": "log_mel",
                    }
                    for offset in range(4)
                ]
                base_score = 1.0 + start_index / 10
                return {
                    "optimization_start_index": start_index,
                    "perceptual_rank": start_index,
                    "preset_name": f"Preset {start_index}",
                    "preset_category": "Test",
                    "preset_relative_path": f"Test/{start_index}.fxp",
                    "state_path": str(state),
                    "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
                    "base_audio_path": str(audio),
                    "base_wav_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                    "base_score": base_score,
                    "base_raw_distances": contributions,
                    "base_normalized_distances": contributions,
                    "base_weighted_contributions": contributions,
                    "continuous_parameters_considered": 4,
                    "inventory_parameter_count": 4,
                    "structural_exclusion_ledger": [],
                    "module_count": 1,
                    "module_probe_count": 2,
                    "module_probes": [],
                    "responsive_modules": ["/Scene A/"],
                    "responsive_module_count": 1,
                    "individual_parameters_probed": 4,
                    "individual_probe_count": 8,
                    "probe_count": 8,
                    "responsive_count": 4,
                    "probes": [],
                    "selected_parameters": parameters,
                    "selected_dimension": 4,
                    "adaptive_status": "READY",
                    "seed": derive_seed(self.master_seed, start_index),
                    "seed_derivation": "master_seed + perceptual_rank",
                    "history": [],
                    "generation_summaries": [],
                    "best": {
                        "candidate_kind": "BASE",
                        "retrieval_score": base_score,
                        "raw_distances": contributions,
                        "normalized_distances": contributions,
                        "weighted_contributions": contributions,
                        "audio_path": str(audio),
                        "audio_url": None,
                        "wav_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                        "parameter_values_normalized": [0.5] * 4,
                        "parameter_values_real": {
                            str(item["id"]): 0.5 for item in parameters
                        },
                        "perceptual_rank": start_index,
                        "optimization_start_index": start_index,
                    },
                }

            def _score_path(self, path):
                values = json.loads(Path(path).read_text(encoding="utf-8"))
                score = 0.2 + sum((float(value) - 0.35) ** 2 for value in values.values())
                contributions = {
                    "log_mel": score * 0.55,
                    "envelope": score * 0.25,
                    "harmonic": score * 0.15,
                    "flatness": score * 0.05,
                }
                return {
                    "raw_distances": contributions,
                    "normalized_distances": contributions,
                    "weighted_contributions": contributions,
                    "retrieval_score": score,
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.wav"
            target.write_bytes(b"target")
            engine = FakeEngine(
                adapter=FakeAdapter(),
                target_path=target,
                target_descriptor=None,
                family_medians={
                    "log_mel": 1.0,
                    "envelope": 1.0,
                    "harmonic": 1.0,
                    "flatness": 1.0,
                },
                perceptual_ranking=[{"perceptual_rank": rank} for rank in range(1, 6)],
                output_directory=root / "run",
                probe_workers=1,
            )
            engine.run()
            self.assertEqual(len(engine.history), 5 * 32 + 2 * 64)
            self.assertEqual(len(engine.stage_b_survivors), 2)
            counts = {
                start["optimization_start_index"]: len(start["history"])
                for start in engine.starts
            }
            self.assertEqual(sorted(counts.values()), [32, 32, 32, 96, 96])
            self.assertEqual(sum(not row["valid"] for row in engine.history), 1)
            for start in engine.starts:
                ids = {item["id"] for item in start["selected_parameters"]}
                self.assertTrue(
                    all(set(map(int, row["parameter_values_real"])) == ids for row in start["history"])
                )
            survivor = next(
                row for row in engine.starts
                if row["optimization_start_index"] == engine.stage_b_survivors[0]
            )
            self.assertEqual(
                [row["generation"] for row in survivor["generation_summaries"]],
                list(range(1, 13)),
            )
            self.assertGreaterEqual(len(engine.verification_attempts), 2)
            self.assertFalse(engine.verification_attempts[0]["stable"])
            self.assertIsNotNone(engine.verified_stable_best)
            self.assertNotEqual(
                engine.numeric_best["wav_sha256"],
                engine.verified_stable_best["wav_sha256"],
            )


if __name__ == "__main__":
    unittest.main()

"""Adaptive perceptual multi-start refinement through the real Surge CLAP.

Retrieval chooses presets, a deterministic finite-difference screen chooses a
small preset-specific parameter space, and CMA-ES searches combinations inside
that frozen space.  No stage infers semantic parameter meaning.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .audition import MIDI_KEY, NOTE_FRAMES, VELOCITY, _write_json_atomic
from .direct_search import cma
from .perceptual_retrieval import (
    FAMILY_WEIGHTS,
    PerceptualDescriptor,
    PerceptualRetrievalError,
    describe_audio,
    score_with_frozen_medians,
)
from .synth_adapter import ClapSynthAdapter, SynthAdapterError, sha256_file


SCHEMA = "agentic-synth-twin/perceptual-multistart-refinement/v1"
MASTER_SEED = 20_261_001
PROBE_DELTA = 0.05
BASELINE_REPLICATES = 4
MODULE_RESPONSE_EPSILON = 1e-6
LOCAL_RADIUS = 0.20
MIN_DIMENSIONS = 4
TARGET_ALIGNED_LIMIT = 8
MAX_DIMENSIONS = 12
POPULATION_SIZE = 8
PILOT_GENERATIONS = 4
DEEPEN_GENERATIONS = 8
PILOT_EVALUATIONS = POPULATION_SIZE * PILOT_GENERATIONS
DEEPEN_EVALUATIONS = POPULATION_SIZE * DEEPEN_GENERATIONS
INITIAL_SIGMA = 0.15
INVALID_PENALTY = 1_000_000.0
MAX_STARTS = 5
MAX_RETRIEVAL_RANK = 10
PROBE_WORKERS = min(8, max(4, os.cpu_count() or 4))

CLAP_PARAM_IS_STEPPED = 1 << 0
CLAP_PARAM_IS_HIDDEN = 1 << 2
CLAP_PARAM_IS_READONLY = 1 << 3
EXCLUDED_FLAGS = CLAP_PARAM_IS_STEPPED | CLAP_PARAM_IS_HIDDEN | CLAP_PARAM_IS_READONLY
STRUCTURAL_NON_AUDIO_NAMES = {
    "Active Scene",
    "Scene Mode",
    "Split Point",
    "Polyphony Limit",
}


class PerceptualMultiStartError(RuntimeError):
    """Raised when adaptive refinement violates its frozen experiment contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rounded(value: float) -> float:
    return round(float(value), 12)


def discover_continuous_parameter_pool(
    inventory: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return visible writable continuous controls exactly as reported by CLAP."""

    pool, _ledger = structural_parameter_screen(inventory)
    return pool


def structural_parameter_screen(
    inventory: Mapping[str, Any],
    *,
    host_fixed_parameter_ids: Sequence[int] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply only explicit metadata/state exclusions and retain a full ledger."""

    pool: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    fixed_ids = {int(value) for value in host_fixed_parameter_ids}
    inactive_fx_slots = {
        match.group(1)
        for source in inventory.get("parameters", [])
        if (match := re.match(r"^(FX [ABSG][0-9]+) FX Type$", str(source.get("name", ""))))
        and str(source.get("current_text", "")).strip().casefold() == "off"
    }
    for index, source in enumerate(inventory.get("parameters", [])):
        source_record = {
            key: source.get(key)
            for key in (
                "id", "name", "module", "flags", "min", "max", "default",
                "current", "current_text",
            )
        }
        try:
            parameter_id = int(source["id"])
            flags = int(source["flags"])
            minimum = float(source["min"])
            maximum = float(source["max"])
            current = float(source["current"])
            default = float(source["default"])
            name = str(source["name"])
            module = str(source.get("module", ""))
        except (KeyError, TypeError, ValueError):
            ledger.append(
                {
                    "inventory_index": index,
                    **source_record,
                    "decision": "EXCLUDE",
                    "rule": "INVALID_OR_INCOMPLETE_REAL_METADATA",
                }
            )
            continue
        rule = None
        if flags & CLAP_PARAM_IS_STEPPED:
            rule = "CLAP_FLAG_STEPPED"
        elif flags & CLAP_PARAM_IS_HIDDEN:
            rule = "CLAP_FLAG_HIDDEN"
        elif flags & CLAP_PARAM_IS_READONLY:
            rule = "CLAP_FLAG_READONLY"
        elif parameter_id in fixed_ids:
            rule = "HOST_FIXED_DETERMINISTIC_CONTROL"
        elif name == "Bypass Surge XT":
            rule = "PLUGIN_NAMED_BYPASS_CONTROL"
        elif module == "/Macros/" and name.startswith("M") and name.endswith(": -"):
            rule = "PLUGIN_NAMED_UNASSIGNED_MACRO"
        elif any(name == slot or name.startswith(f"{slot} ") for slot in inactive_fx_slots):
            rule = "CLAP_VALUE_TEXT_INACTIVE_FX_SLOT"
        elif name in STRUCTURAL_NON_AUDIO_NAMES:
            rule = "PLUGIN_NAMED_ROUTING_OR_CAPACITY_CONTROL"
        elif not all(math.isfinite(value) for value in (minimum, maximum, current, default)):
            rule = "NONFINITE_REAL_METADATA"
        elif maximum <= minimum:
            rule = "NONPOSITIVE_REAL_RANGE"
        elif not minimum <= current <= maximum:
            rule = "CURRENT_VALUE_OUTSIDE_REAL_RANGE"
        if rule is not None:
            ledger.append(
                {
                    "inventory_index": index,
                    **source_record,
                    "decision": "EXCLUDE",
                    "rule": rule,
                }
            )
            continue
        normalized = (current - minimum) / (maximum - minimum)
        screen_group, screen_group_rule = screen_group_from_metadata(module, name)
        item = {
            "id": parameter_id,
            "name": name,
            "label": name,
            "module": module,
            "screen_group": screen_group,
            "screen_group_rule": screen_group_rule,
            "flags": flags,
            "min": minimum,
            "max": maximum,
            "default": default,
            "current": current,
            "current_text": source.get("current_text"),
            "normalized_base": _rounded(normalized),
            "inventory_source": "real CLAP parameter inventory",
        }
        pool.append(item)
        ledger.append(
            {
                "inventory_index": index,
                **source_record,
                "screen_group": screen_group,
                "screen_group_rule": screen_group_rule,
                "decision": "INCLUDE_FOR_MODULE_SCREEN",
                "rule": "VISIBLE_WRITABLE_CONTINUOUS_FINITE_REAL_CONTROL",
            }
        )
    pool.sort(key=lambda row: (row["id"], row["name"]))
    ledger.sort(key=lambda row: row["inventory_index"])
    return pool, ledger


def screen_group_from_metadata(module: str, name: str) -> tuple[str, str]:
    """Split broad modules using only prefixes that Surge reports by name."""

    match = re.match(r"^([AB]) (Scene )?LFO ([0-9]+) ", name)
    if match:
        prefix = f"{match.group(1)} {'Scene ' if match.group(2) else ''}LFO {match.group(3)}"
        return f"{module}{prefix}", "LFO_NUMBER_PREFIX"
    match = re.match(r"^([AB]) Osc ([0-9]+) ", name)
    if match:
        return f"{module}{match.group(1)} Osc {match.group(2)}", "OSCILLATOR_NUMBER_PREFIX"
    match = re.match(r"^([AB]) (Amp|Filter) EG ", name)
    if match:
        return f"{module}{match.group(1)} {match.group(2)} EG", "ENVELOPE_KIND_PREFIX"
    match = re.match(r"^([AB]) Filter ([0-9]+) ", name)
    if match:
        return f"{module}{match.group(1)} Filter {match.group(2)}", "FILTER_NUMBER_PREFIX"
    match = re.match(r"^([AB]) Ring Modulation ([^ ]+) ", name)
    if match:
        return (
            f"{module}{match.group(1)} Ring Modulation {match.group(2)}",
            "MIX_SOURCE_PREFIX",
        )
    match = re.match(r"^([AB]) (Noise|Pre-Filter) ", name)
    if match:
        return f"{module}{match.group(1)} {match.group(2)}", "MIX_SOURCE_PREFIX"
    match = re.match(r"^FX ([ABSG][0-9]+) ", name)
    if match:
        return f"{module}FX {match.group(1)}", "FX_SLOT_PREFIX"
    return module, "CLAP_MODULE_PATH"


def group_parameters_by_module(
    parameters: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for parameter in parameters:
        module = str(parameter.get("screen_group", ""))
        if not module:
            raise PerceptualMultiStartError(
                f"structurally eligible control has no screen group: {parameter.get('id')}"
            )
        groups.setdefault(module, []).append(dict(parameter))
    return {
        module: sorted(rows, key=lambda row: (row["id"], row["name"]))
        for module, rows in sorted(groups.items())
    }


def probe_values(base: float, delta: float = PROBE_DELTA) -> dict[str, float]:
    if not math.isfinite(base) or not 0.0 <= base <= 1.0:
        raise PerceptualMultiStartError("probe base must be within normalized 0..1")
    if not math.isfinite(delta) or delta <= 0.0:
        raise PerceptualMultiStartError("probe delta must be positive and finite")
    return {
        "minus": _rounded(max(0.0, base - delta)),
        "plus": _rounded(min(1.0, base + delta)),
    }


def local_bounds(base: float, radius: float = LOCAL_RADIUS) -> tuple[float, float]:
    if not math.isfinite(base) or not 0.0 <= base <= 1.0:
        raise PerceptualMultiStartError("local bound base must be within normalized 0..1")
    return _rounded(max(0.0, base - radius)), _rounded(min(1.0, base + radius))


def normalized_to_real(value: float, parameter: Mapping[str, Any]) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise PerceptualMultiStartError("normalized parameter value must be in 0..1")
    return _rounded(
        float(parameter["min"])
        + numeric * (float(parameter["max"]) - float(parameter["min"]))
    )


def select_adaptive_parameters(
    probes: Sequence[Mapping[str, Any]],
    base_contributions: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Apply the frozen family, target-gain, and interaction selection rules."""

    responsive = [row for row in probes if row.get("responsive")]
    selected: list[Mapping[str, Any]] = []

    def add(row: Mapping[str, Any]) -> None:
        if row not in selected and len(selected) < MAX_DIMENSIONS:
            selected.append(row)

    for family in FAMILY_WEIGHTS:
        if float(base_contributions.get(family, 0.0)) <= 0.0:
            continue
        choices = [
            row
            for row in responsive
            if float(row.get("family_reductions", {}).get(family, 0.0)) > 0.0
        ]
        if choices:
            choices.sort(
                key=lambda row: (
                    -float(row["family_reductions"][family]),
                    int(row["parameter"]["id"]),
                )
            )
            add(choices[0])

    aligned = sorted(
        responsive,
        key=lambda row: (
            -float(row.get("target_aligned_gain", -math.inf)),
            int(row["parameter"]["id"]),
        ),
    )
    for row in aligned:
        if len(selected) >= TARGET_ALIGNED_LIMIT:
            break
        add(row)

    influential = sorted(
        responsive,
        key=lambda row: (
            -float(row.get("influence_magnitude", 0.0)),
            int(row["parameter"]["id"]),
        ),
    )
    interaction_added = 0
    for row in influential:
        before = len(selected)
        add(row)
        if len(selected) > before:
            interaction_added += 1
        if interaction_added >= 4 or len(selected) >= MAX_DIMENSIONS:
            break

    return [
        {
            **dict(row["parameter"]),
            "target_aligned_gain": float(row["target_aligned_gain"]),
            "influence_magnitude": float(row["influence_magnitude"]),
            "family_reductions": dict(row["family_reductions"]),
            "probe_scores": {
                item["direction"]: item.get("retrieval_score")
                for item in row.get("directions", [])
            },
            "main_family_effect": max(
                row["family_reductions"],
                key=lambda family: row["family_reductions"][family],
            ),
            "local_bounds": list(local_bounds(float(row["parameter"]["normalized_base"]))),
        }
        for row in selected
    ]


def derive_seed(master_seed: int, perceptual_rank: int) -> int:
    if master_seed < 1 or perceptual_rank < 1:
        raise PerceptualMultiStartError("seed inputs must be positive")
    return int(master_seed + perceptual_rank)


def rank_pilot_trajectories(
    trajectories: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return sorted(
        trajectories,
        key=lambda row: (
            float(row["best"]["retrieval_score"]),
            float(row["base_score"]),
            int(row["perceptual_rank"]),
        ),
    )


class PerceptualMultiStartEngine:
    """One immutable target's sensitivity screen and two-stage CMA search."""

    def __init__(
        self,
        *,
        adapter: ClapSynthAdapter,
        target_path: str | Path,
        target_descriptor: PerceptualDescriptor,
        family_medians: Mapping[str, float],
        perceptual_ranking: Sequence[Mapping[str, Any]],
        output_directory: str | Path,
        master_seed: int = MASTER_SEED,
        progress: Callable[[dict[str, Any]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        probe_workers: int = PROBE_WORKERS,
    ) -> None:
        self.adapter = adapter
        self.target_path = Path(target_path).resolve()
        self.target_descriptor = target_descriptor
        self.family_medians = {key: float(value) for key, value in family_medians.items()}
        self.ranking = [dict(row) for row in perceptual_ranking[:MAX_RETRIEVAL_RANK]]
        self.output_directory = Path(output_directory).resolve()
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.audio_directory = self.output_directory / "audio"
        self.audio_directory.mkdir(exist_ok=True)
        self.master_seed = int(master_seed)
        self.progress = progress
        self.should_stop = should_stop or (lambda: False)
        self.probe_workers = max(1, int(probe_workers))
        self.stage = "IDLE"
        self.error: str | None = None
        self.starts: list[dict[str, Any]] = []
        self.skipped_starts: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.stage_a_ranking: list[int] = []
        self.stage_b_survivors: list[int] = []
        self.numeric_best: dict[str, Any] | None = None
        self.verification_attempts: list[dict[str, Any]] = []
        self.verified_stable_best: dict[str, Any] | None = None
        self.started_at = _now()

    def _notify(self, event: str, **details: Any) -> None:
        if event != "sensitivity_progress":
            self._persist()
        if self.progress is not None:
            self.progress({"event": event, **details, "snapshot": self.public_snapshot()})

    def _persist(self) -> None:
        _write_json_atomic(self.evidence(), self.output_directory / "multistart.json")

    def _score_path(self, path: Path) -> dict[str, Any]:
        return score_with_frozen_medians(
            self.target_descriptor,
            describe_audio(path),
            self.family_medians,
        )

    def _render_probe_set(
        self,
        *,
        state_path: Path,
        base_hash: str,
        path: Path,
        parameter_values: Mapping[int, float],
        probe_values_normalized: Mapping[int, float],
        direction: str,
        allow_no_audio_effect: bool = False,
    ) -> dict[str, Any]:
        try:
            rendered = self.adapter.render_note(
                state_path=state_path,
                wav_path=path,
                parameter_values=parameter_values,
                midi_key=MIDI_KEY,
                velocity=VELOCITY,
                note_frames=NOTE_FRAMES,
                allow_clipping=True,
                allow_parameter_coercion=True,
            )
            invalid_reason = None
            if float(rendered.get("peak_float", 0.0)) <= 0.0:
                invalid_reason = "SILENT"
            elif int(rendered.get("clipped_samples", 0)) > 0:
                invalid_reason = f"CLIPPED:{rendered['clipped_samples']}"
            identical = rendered["wav_sha256"] == base_hash
            if invalid_reason is None and identical and not allow_no_audio_effect:
                invalid_reason = "NO_AUDIO_EFFECT"
            score = None if invalid_reason else self._score_path(path)
            return {
                "direction": direction,
                "normalized_values": {
                    str(key): value for key, value in probe_values_normalized.items()
                },
                "real_values": {str(key): value for key, value in parameter_values.items()},
                "applied_values": dict(rendered["applied_values"]),
                "coerced_parameters": list(rendered["coerced_parameters"]),
                "valid": invalid_reason is None,
                "invalid_reason": invalid_reason,
                "wav_path": str(path),
                "wav_sha256": rendered["wav_sha256"],
                "byte_identical_to_base": identical,
                "peak_float": rendered["peak_float"],
                "clipped_samples": rendered["clipped_samples"],
                **(score or {}),
            }
        except (SynthAdapterError, PerceptualRetrievalError, OSError, ValueError) as error:
            return {
                "direction": direction,
                "normalized_values": {
                    str(key): value for key, value in probe_values_normalized.items()
                },
                "real_values": {str(key): value for key, value in parameter_values.items()},
                "valid": False,
                "invalid_reason": f"{type(error).__name__}: {error}",
            }

    def _render_parameter_probe(
        self,
        *,
        start_index: int,
        state_path: Path,
        base_hash: str,
        parameter: Mapping[str, Any],
        direction: str,
        normalized: float,
    ) -> dict[str, Any]:
        parameter_id = int(parameter["id"])
        path = (
            self.audio_directory
            / f"start-{start_index:02d}"
            / "parameter-probes"
            / f"p-{parameter_id}-{direction}.wav"
        )
        result = self._render_probe_set(
            state_path=state_path,
            base_hash=base_hash,
            path=path,
            parameter_values={parameter_id: normalized_to_real(normalized, parameter)},
            probe_values_normalized={parameter_id: normalized},
            direction=direction,
            allow_no_audio_effect=direction == "neutral",
        )
        return {
            **result,
            "normalized_value": normalized,
            "real_value": normalized_to_real(normalized, parameter),
        }

    def _screen_start(
        self, row: Mapping[str, Any], start_index: int
    ) -> dict[str, Any]:
        state_path = Path(row["state_path"])
        inventory = self.adapter.inspect_state(state_path)
        pool, structural_ledger = structural_parameter_screen(
            inventory,
            host_fixed_parameter_ids=self.adapter.deterministic_overrides,
        )
        modules = group_parameters_by_module(pool)
        baseline_probes: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self.probe_workers, BASELINE_REPLICATES)
        ) as executor:
            futures = [
                executor.submit(
                    self._render_probe_set,
                    state_path=state_path,
                    base_hash=str(row["wav_sha256"]),
                    path=(
                        self.audio_directory
                        / f"start-{start_index:02d}"
                        / "screen-controls"
                        / f"control-{index:02d}.wav"
                    ),
                    parameter_values={},
                    probe_values_normalized={},
                    direction=f"control-{index}",
                    allow_no_audio_effect=True,
                )
                for index in range(1, BASELINE_REPLICATES + 1)
            ]
            baseline_probes = [future.result() for future in futures]
        valid_baselines = [item for item in baseline_probes if item["valid"]]
        if len(valid_baselines) < 2:
            raise PerceptualMultiStartError(
                f"preset rank {row['perceptual_rank']} produced fewer than two valid no-change controls"
            )
        base_normalized = {
            family: float(
                np.median(
                    [item["normalized_distances"][family] for item in valid_baselines]
                )
            )
            for family in FAMILY_WEIGHTS
        }
        base_contributions = {
            family: float(
                np.median(
                    [item["weighted_contributions"][family] for item in valid_baselines]
                )
            )
            for family in FAMILY_WEIGHTS
        }
        baseline_score = float(
            np.median([item["retrieval_score"] for item in valid_baselines])
        )
        baseline_noise_floor = {
            family: max(
                abs(float(item["normalized_distances"][family]) - base_normalized[family])
                for item in valid_baselines
            )
            for family in FAMILY_WEIGHTS
        }
        module_jobs: list[tuple[str, list[dict[str, Any]], str, dict[int, float]]] = []
        for module, parameters in modules.items():
            module_jobs.append(
                (
                    module,
                    parameters,
                    "neutral",
                    {
                        int(parameter["id"]): float(parameter["normalized_base"])
                        for parameter in parameters
                    },
                )
            )
            for direction in ("minus", "plus"):
                normalized = {
                    int(parameter["id"]): probe_values(parameter["normalized_base"])[direction]
                    for parameter in parameters
                }
                if all(
                    math.isclose(
                        normalized[int(parameter["id"])],
                        float(parameter["normalized_base"]),
                        abs_tol=1e-12,
                    )
                    for parameter in parameters
                ):
                    continue
                module_jobs.append((module, parameters, direction, normalized))
        jobs: list[tuple[dict[str, Any], str, float]] = []
        self._notify(
            "module_screen_started",
            perceptual_rank=row["perceptual_rank"],
            inventory_size=len(inventory.get("parameters", [])),
            structurally_eligible=len(pool),
            module_count=len(modules),
            module_probe_count=len(module_jobs),
        )
        module_directions: dict[str, list[dict[str, Any]]] = {
            module: [] for module in modules
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.probe_workers) as executor:
            futures = {}
            for module, parameters, direction, normalized in module_jobs:
                slug = hashlib.sha256(module.encode()).hexdigest()[:12]
                path = (
                    self.audio_directory
                    / f"start-{start_index:02d}"
                    / "module-probes"
                    / f"module-{slug}-{direction}.wav"
                )
                real_values = {
                    int(parameter["id"]): normalized_to_real(
                        normalized[int(parameter["id"])], parameter
                    )
                    for parameter in parameters
                }
                future = executor.submit(
                    self._render_probe_set,
                    state_path=state_path,
                    base_hash=str(row["wav_sha256"]),
                    path=path,
                    parameter_values=real_values,
                    probe_values_normalized=normalized,
                    direction=direction,
                    allow_no_audio_effect=direction == "neutral",
                )
                futures[future] = module
            for future in concurrent.futures.as_completed(futures):
                module_directions[futures[future]].append(future.result())
                if self.should_stop():
                    for pending in futures:
                        pending.cancel()
                    raise PerceptualMultiStartError("search stopped by owner")

        module_probes: list[dict[str, Any]] = []
        responsive_module_names: set[str] = set()
        coerced_parameter_ids: set[int] = set()
        for module, parameters in modules.items():
            directions = sorted(module_directions[module], key=lambda item: item["direction"])
            neutral = next(
                (item for item in directions if item["direction"] == "neutral"),
                None,
            )
            neutral_normalized = (
                dict(neutral["normalized_distances"])
                if neutral is not None and neutral["valid"] else None
            )
            for direction in directions:
                direction["family_effects"] = (
                    {
                        family: abs(
                            float(direction["normalized_distances"][family])
                            - float(neutral_normalized[family])
                        )
                        for family in FAMILY_WEIGHTS
                    }
                    if (
                        direction["valid"]
                        and direction["direction"] != "neutral"
                        and neutral_normalized is not None
                    ) else {}
                )
                direction["family_effect_magnitude"] = sum(
                    direction["family_effects"].values()
                )
            responsive = any(
                direction["valid"]
                and direction["direction"] != "neutral"
                and any(
                    float(direction["family_effects"][family])
                    > baseline_noise_floor[family] + MODULE_RESPONSE_EPSILON
                    for family in FAMILY_WEIGHTS
                )
                for direction in directions
            )
            if responsive:
                responsive_module_names.add(module)
            for direction in directions:
                coerced_parameter_ids.update(
                    int(item["id"])
                    for item in direction.get("coerced_parameters", [])
                    if int(item["id"]) in {int(row["id"]) for row in parameters}
                )
            module_probes.append(
                {
                    "module": module,
                    "parameter_count": len(parameters),
                    "parameter_ids": [int(item["id"]) for item in parameters],
                    "directions": directions,
                    "responsive": responsive,
                    "response_epsilon": MODULE_RESPONSE_EPSILON,
                    "baseline_noise_floor": dict(baseline_noise_floor),
                    "neutral_control_valid": bool(neutral and neutral["valid"]),
                    "decision": (
                        "INDIVIDUAL_PARAMETER_SCREEN"
                        if responsive
                        else "EXCLUDE_AFTER_MODULE_SCREEN"
                    ),
                }
            )

        individual_pool = [
            parameter for parameter in pool
            if parameter["screen_group"] in responsive_module_names
            and int(parameter["id"]) not in coerced_parameter_ids
        ]
        module_behavior_exclusions = [
            {
                "id": int(parameter["id"]),
                "name": parameter["name"],
                "screen_group": parameter["screen_group"],
                "decision": "EXCLUDE_BEFORE_INDIVIDUAL_SCREEN",
                "rule": "REAL_SYNTH_COERCED_MODULE_PROBE_VALUE",
            }
            for parameter in pool
            if int(parameter["id"]) in coerced_parameter_ids
        ]
        for parameter in individual_pool:
            jobs.append(
                (parameter, "neutral", float(parameter["normalized_base"]))
            )
            for direction, value in probe_values(parameter["normalized_base"]).items():
                if math.isclose(value, parameter["normalized_base"], abs_tol=1e-12):
                    continue
                jobs.append((parameter, direction, value))
        self._notify(
            "parameter_screen_started",
            perceptual_rank=row["perceptual_rank"],
            responsive_modules=sorted(responsive_module_names),
            parameter_count=len(individual_pool),
            parameter_probe_count=len(jobs),
        )
        grouped: dict[int, list[dict[str, Any]]] = {
            int(parameter["id"]): [] for parameter in individual_pool
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.probe_workers) as executor:
            futures = {
                executor.submit(
                    self._render_parameter_probe,
                    start_index=start_index,
                    state_path=state_path,
                    base_hash=str(row["wav_sha256"]),
                    parameter=parameter,
                    direction=direction,
                    normalized=value,
                ): parameter
                for parameter, direction, value in jobs
            }
            for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                parameter = futures[future]
                grouped[int(parameter["id"])].append(future.result())
                if completed % 50 == 0:
                    self._notify(
                        "sensitivity_progress",
                        perceptual_rank=row["perceptual_rank"],
                        completed=completed,
                        total=len(jobs),
                    )
                if self.should_stop():
                    for pending in futures:
                        pending.cancel()
                    raise PerceptualMultiStartError("search stopped by owner")

        probes: list[dict[str, Any]] = []
        for parameter in individual_pool:
            directions = sorted(grouped[int(parameter["id"])], key=lambda item: item["direction"])
            neutral = next(
                (item for item in directions if item["direction"] == "neutral"),
                None,
            )
            neutral_normalized = (
                dict(neutral["normalized_distances"])
                if neutral is not None and neutral["valid"] else None
            )
            for direction in directions:
                direction["family_effects"] = (
                    {
                        family: abs(
                            float(direction["normalized_distances"][family])
                            - float(neutral_normalized[family])
                        )
                        for family in FAMILY_WEIGHTS
                    }
                    if (
                        direction["valid"]
                        and direction["direction"] != "neutral"
                        and neutral_normalized is not None
                    ) else {}
                )
                direction["family_effect_magnitude"] = sum(
                    direction["family_effects"].values()
                )
            valid = [
                item for item in directions
                if item["valid"]
                and item["direction"] != "neutral"
                and any(
                    float(item["family_effects"][family])
                    > baseline_noise_floor[family] + MODULE_RESPONSE_EPSILON
                    for family in FAMILY_WEIGHTS
                )
            ]
            if valid:
                best = min(valid, key=lambda item: item["retrieval_score"])
                most_changed = max(
                    valid,
                    key=lambda item: sum(
                        abs(
                            float(item["normalized_distances"][family])
                            - float(neutral_normalized[family])
                        )
                        for family in FAMILY_WEIGHTS
                    ),
                )
                family_reductions = {
                    family: max(
                        0.0,
                        float(neutral["weighted_contributions"][family])
                        - min(float(item["weighted_contributions"][family]) for item in valid),
                    )
                    for family in FAMILY_WEIGHTS
                }
                influence = sum(
                    abs(
                        float(most_changed["normalized_distances"][family])
                        - float(neutral_normalized[family])
                    )
                    for family in FAMILY_WEIGHTS
                )
                target_gain = float(neutral["retrieval_score"]) - float(best["retrieval_score"])
            else:
                family_reductions = {family: 0.0 for family in FAMILY_WEIGHTS}
                influence = 0.0
                target_gain = None
            probes.append(
                {
                    "parameter": parameter,
                    "directions": directions,
                    "responsive": bool(valid),
                    "target_aligned_gain": target_gain,
                    "influence_magnitude": float(influence),
                    "family_reductions": family_reductions,
                }
            )
        selected = select_adaptive_parameters(probes, base_contributions)
        search_base: dict[str, Any] | None = None
        if len(selected) >= MIN_DIMENSIONS:
            search_base = self._render_probe_set(
                state_path=state_path,
                base_hash=str(row["wav_sha256"]),
                path=(
                    self.audio_directory
                    / f"start-{start_index:02d}"
                    / "search-base.wav"
                ),
                parameter_values={
                    int(parameter["id"]): float(parameter["current"])
                    for parameter in selected
                },
                probe_values_normalized={
                    int(parameter["id"]): float(parameter["normalized_base"])
                    for parameter in selected
                },
                direction="selected-parameter-neutral",
                allow_no_audio_effect=True,
            )
        adaptive_status = (
            "READY"
            if search_base is not None and search_base["valid"]
            else (
                "ADAPTIVE_SEARCH_BASE_INVALID"
                if len(selected) >= MIN_DIMENSIONS
                else "ADAPTIVE_SEARCH_INSUFFICIENT"
            )
        )
        active_base = search_base if search_base is not None and search_base["valid"] else None
        active_score = float(active_base["retrieval_score"]) if active_base else float(row["retrieval_score"])
        active_raw = dict(active_base["raw_distances"]) if active_base else dict(row["raw_distances"])
        active_normalized = (
            dict(active_base["normalized_distances"])
            if active_base else dict(row["normalized_distances"])
        )
        active_contributions = (
            dict(active_base["weighted_contributions"])
            if active_base else dict(row["weighted_contributions"])
        )
        active_audio_path = (
            str(active_base["wav_path"]) if active_base else str(row["audio_path"])
        )
        active_audio_url = (
            "/audio/" + Path(active_audio_path).relative_to(self.audio_directory).as_posix()
            if active_base else row.get("audio_url")
        )
        active_hash = str(active_base["wav_sha256"]) if active_base else str(row["wav_sha256"])
        return {
            "optimization_start_index": start_index,
            "perceptual_rank": int(row["perceptual_rank"]),
            "preset_name": row["preset_name"],
            "preset_category": row["preset_category"],
            "preset_relative_path": row["preset_relative_path"],
            "state_path": str(state_path),
            "state_sha256": row["state_sha256"],
            "retrieval_base_audio_path": row["audio_path"],
            "retrieval_base_wav_sha256": row["wav_sha256"],
            "retrieval_base_score": float(row["retrieval_score"]),
            "base_audio_path": active_audio_path,
            "base_wav_sha256": active_hash,
            "base_score": active_score,
            "base_raw_distances": active_raw,
            "base_normalized_distances": active_normalized,
            "base_weighted_contributions": active_contributions,
            "search_base_probe": search_base,
            "screening_baseline": {
                "replicate_count": BASELINE_REPLICATES,
                "valid_replicate_count": len(valid_baselines),
                "probes": baseline_probes,
                "median_retrieval_score": baseline_score,
                "median_normalized_distances": base_normalized,
                "median_weighted_contributions": base_contributions,
                "family_noise_floor": baseline_noise_floor,
                "responsiveness_rule": "family effect > measured no-change noise floor + epsilon",
            },
            "inventory_parameter_count": len(inventory.get("parameters", [])),
            "structural_exclusion_ledger": structural_ledger,
            "structural_exclusion_count": sum(
                item["decision"] == "EXCLUDE" for item in structural_ledger
            ),
            "continuous_parameters_considered": len(pool),
            "module_count": len(modules),
            "module_probe_count": len(module_jobs),
            "module_probes": module_probes,
            "responsive_modules": sorted(responsive_module_names),
            "responsive_module_count": len(responsive_module_names),
            "module_behavior_exclusions": module_behavior_exclusions,
            "module_behavior_exclusion_count": len(module_behavior_exclusions),
            "individual_parameters_probed": len(individual_pool),
            "individual_probe_count": len(jobs),
            "probe_count": (
                BASELINE_REPLICATES + len(module_jobs) + len(jobs)
                + (1 if search_base is not None else 0)
            ),
            "responsive_count": sum(bool(item["responsive"]) for item in probes),
            "probes": probes,
            "selected_parameters": selected,
            "selected_dimension": len(selected),
            "adaptive_status": adaptive_status,
            "seed": derive_seed(self.master_seed, int(row["perceptual_rank"])),
            "seed_derivation": "master_seed + perceptual_rank",
            "history": [],
            "generation_summaries": [],
            "best": {
                "candidate_kind": "BASE",
                "retrieval_score": active_score,
                "raw_distances": active_raw,
                "normalized_distances": active_normalized,
                "weighted_contributions": active_contributions,
                "audio_path": active_audio_path,
                "audio_url": active_audio_url,
                "wav_sha256": active_hash,
                "parameter_values_normalized": [
                    float(item["normalized_base"]) for item in selected
                ],
                "parameter_values_real": {
                    str(item["id"]): float(item["current"]) for item in selected
                },
                "perceptual_rank": int(row["perceptual_rank"]),
                "optimization_start_index": start_index,
            },
        }

    @staticmethod
    def _global_from_local(local: Sequence[float], parameters: Sequence[Mapping[str, Any]]) -> list[float]:
        values = []
        for value, parameter in zip(local, parameters, strict=True):
            low, high = parameter["local_bounds"]
            clipped = min(1.0, max(0.0, float(value)))
            values.append(_rounded(float(low) + clipped * (float(high) - float(low))))
        return values

    @staticmethod
    def _local_base(parameters: Sequence[Mapping[str, Any]]) -> list[float]:
        return [
            _rounded(
                (float(parameter["normalized_base"]) - float(parameter["local_bounds"][0]))
                / (float(parameter["local_bounds"][1]) - float(parameter["local_bounds"][0]))
            )
            for parameter in parameters
        ]

    def _evaluate_candidate(
        self,
        trajectory: dict[str, Any],
        *,
        stage: str,
        generation: int,
        candidate_index: int,
        local_vector: Sequence[float],
    ) -> dict[str, Any]:
        evaluation = len(trajectory["history"]) + 1
        global_vector = self._global_from_local(local_vector, trajectory["selected_parameters"])
        values = {
            int(parameter["id"]): normalized_to_real(value, parameter)
            for value, parameter in zip(
                global_vector, trajectory["selected_parameters"], strict=True
            )
        }
        path = (
            self.audio_directory
            / f"start-{trajectory['optimization_start_index']:02d}"
            / "cma"
            / f"eval-{evaluation:04d}.wav"
        )
        invalid_reason = None
        score: dict[str, Any] | None = None
        rendered: dict[str, Any] = {}
        try:
            rendered = self.adapter.render_note(
                state_path=trajectory["state_path"],
                wav_path=path,
                parameter_values=values,
                midi_key=MIDI_KEY,
                velocity=VELOCITY,
                note_frames=NOTE_FRAMES,
                allow_clipping=True,
                allow_parameter_coercion=True,
            )
            if float(rendered.get("peak_float", 0.0)) <= 0.0:
                invalid_reason = "SILENT"
            elif int(rendered.get("clipped_samples", 0)) > 0:
                invalid_reason = f"CLIPPED:{rendered['clipped_samples']}"
            else:
                score = self._score_path(path)
        except (SynthAdapterError, PerceptualRetrievalError, OSError, ValueError) as error:
            invalid_reason = f"{type(error).__name__}: {error}"
        scalar = float(score["retrieval_score"]) if score else INVALID_PENALTY
        prior = float(trajectory["best"]["retrieval_score"])
        record = {
            "stage": stage,
            "generation": generation,
            "candidate_index": candidate_index,
            "trajectory_evaluation": evaluation,
            "total_evaluations": len(self.history) + 1,
            "optimization_start_index": trajectory["optimization_start_index"],
            "perceptual_rank": trajectory["perceptual_rank"],
            "parameter_values_local": [_rounded(value) for value in local_vector],
            "parameter_values_normalized": global_vector,
            "parameter_values_real": {str(key): value for key, value in values.items()},
            "parameter_values_applied": dict(rendered.get("applied_values", {})),
            "coerced_parameters": list(rendered.get("coerced_parameters", [])),
            "audio_path": str(path),
            "audio_url": "/audio/" + path.relative_to(self.audio_directory).as_posix(),
            "wav_sha256": rendered.get("wav_sha256"),
            "peak_float": rendered.get("peak_float"),
            "clipped_samples": rendered.get("clipped_samples"),
            "valid": score is not None,
            "invalid_reason": invalid_reason,
            "optimizer_loss": scalar,
            "is_new_best": score is not None and scalar < prior,
            "timestamp": _now(),
            **(score or {
                "retrieval_score": INVALID_PENALTY,
                "raw_distances": {},
                "normalized_distances": {},
                "weighted_contributions": {},
            }),
        }
        trajectory["history"].append(record)
        self.history.append(record)
        if record["is_new_best"]:
            trajectory["best"] = dict(record)
        return record

    def _run_generations(
        self,
        trajectory: dict[str, Any],
        optimizer: Any,
        *,
        stage: str,
        first_generation: int,
        generations: int,
    ) -> None:
        for generation in range(first_generation, first_generation + generations):
            if self.should_stop():
                raise PerceptualMultiStartError("search stopped by owner")
            vectors = optimizer.ask()
            losses: list[float] = []
            for candidate_index, raw in enumerate(vectors, start=1):
                record = self._evaluate_candidate(
                    trajectory,
                    stage=stage,
                    generation=generation,
                    candidate_index=candidate_index,
                    local_vector=raw,
                )
                losses.append(float(record["optimizer_loss"]))
                if len(self.history) % 8 == 0:
                    self._notify(
                        "cma_generation_progress",
                        stage=stage,
                        perceptual_rank=trajectory["perceptual_rank"],
                        evaluations=len(trajectory["history"]),
                    )
            optimizer.tell(vectors, losses)
            spread = optimizer.sigma * np.sqrt(
                np.maximum(np.diag(np.asarray(optimizer.C)), 0.0)
            )
            trajectory["generation_summaries"].append(
                {
                    "stage": stage,
                    "generation": generation,
                    "evaluations": len(trajectory["history"]),
                    "median_loss": _rounded(float(np.median(losses))),
                    "best_loss": float(trajectory["best"]["retrieval_score"]),
                    "optimizer_mean_local": [_rounded(value) for value in optimizer.mean],
                    "optimizer_spread_local": [_rounded(value) for value in spread],
                    "optimizer_sigma": _rounded(optimizer.sigma),
                }
            )

    def _verify_candidates(self) -> None:
        candidates: list[dict[str, Any]] = []
        for start in self.starts:
            candidates.append(dict(start["best"]))
            candidates.extend(dict(row) for row in start["history"] if row["valid"])
        candidates.sort(
            key=lambda row: (
                float(row["retrieval_score"]),
                int(row["perceptual_rank"]),
                int(row.get("trajectory_evaluation", 0)),
            )
        )
        unique_candidates: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for candidate in candidates:
            identity = (
                int(candidate["optimization_start_index"]),
                json.dumps(
                    candidate["parameter_values_real"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            if identity in seen:
                continue
            seen.add(identity)
            unique_candidates.append(candidate)
        candidates = unique_candidates
        self.numeric_best = dict(candidates[0])
        self.stage = "VERIFYING"
        for candidate in candidates:
            start = next(
                row
                for row in self.starts
                if row["optimization_start_index"] == candidate["optimization_start_index"]
            )
            values = candidate["parameter_values_real"]
            verification_index = len(self.verification_attempts) + 1
            destination = self.audio_directory / "verification" / f"candidate-{verification_index:04d}.wav"
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with tempfile.TemporaryDirectory(
                    dir=destination.parent, prefix=".verify-"
                ) as directory:
                    first_path = Path(directory) / "first.wav"
                    second_path = Path(directory) / "second.wav"
                    first = self.adapter.render_note(
                        state_path=start["state_path"], wav_path=first_path,
                        parameter_values=values, midi_key=MIDI_KEY, velocity=VELOCITY,
                        note_frames=NOTE_FRAMES, allow_parameter_coercion=True,
                    )
                    second = self.adapter.render_note(
                        state_path=start["state_path"], wav_path=second_path,
                        parameter_values=values, midi_key=MIDI_KEY, velocity=VELOCITY,
                        note_frames=NOTE_FRAMES, allow_parameter_coercion=True,
                    )
                    stable = (
                        first["wav_sha256"] == second["wav_sha256"]
                        and first["applied_values"] == second["applied_values"]
                    )
                    attempt = {
                        "candidate": candidate,
                        "stable": stable,
                        "first_wav_sha256": first["wav_sha256"],
                        "second_wav_sha256": second["wav_sha256"],
                        "requested_parameter_values": dict(values),
                        "first_parameter_readback": first["applied_values"],
                        "second_parameter_readback": second["applied_values"],
                        "first_parameter_coercions": first.get(
                            "coerced_parameters", []
                        ),
                        "second_parameter_coercions": second.get(
                            "coerced_parameters", []
                        ),
                        "attempted_at": _now(),
                    }
                    self.verification_attempts.append(attempt)
                    if stable:
                        shutil.move(first_path, destination)
                        score = self._score_path(destination)
                        self.verified_stable_best = {
                            **candidate,
                            **score,
                            "audio_path": str(destination),
                            "audio_url": "/audio/"
                            + destination.relative_to(self.audio_directory).as_posix(),
                            "wav_sha256": sha256_file(destination),
                            "verification_attempt": verification_index,
                            "verified_stable": True,
                        }
                        break
            except (SynthAdapterError, PerceptualRetrievalError, OSError, ValueError) as error:
                self.verification_attempts.append(
                    {
                        "candidate": candidate,
                        "stable": False,
                        "error": f"{type(error).__name__}: {error}",
                        "attempted_at": _now(),
                    }
                )
            self._notify("verification_attempt", attempt=verification_index)

    def run(self) -> dict[str, Any]:
        try:
            self.stage = "SENSITIVITY"
            for row in self.ranking:
                screened = self._screen_start(row, len(self.starts) + 1)
                if screened["adaptive_status"] == "READY":
                    self.starts.append(screened)
                else:
                    self.skipped_starts.append(screened)
                self._notify(
                    "start_screened",
                    perceptual_rank=row["perceptual_rank"],
                    status=screened["adaptive_status"],
                    dimension=screened["selected_dimension"],
                )
                if len(self.starts) >= MAX_STARTS:
                    break
            if not self.starts:
                raise PerceptualMultiStartError(
                    "no Top-10 preset exposed four responsive continuous controls"
                )

            self.stage = "PILOT"
            optimizers: dict[int, Any] = {}
            for start in self.starts:
                parameters = start["selected_parameters"]
                optimizer = cma.CMAEvolutionStrategy(
                    self._local_base(parameters),
                    INITIAL_SIGMA,
                    {
                        "bounds": [0.0, 1.0],
                        "seed": start["seed"],
                        "popsize": POPULATION_SIZE,
                        "maxiter": PILOT_GENERATIONS + DEEPEN_GENERATIONS,
                        "verbose": -9,
                        "verb_disp": 0,
                        "verb_log": 0,
                    },
                )
                optimizers[start["optimization_start_index"]] = optimizer
                self._run_generations(
                    start, optimizer, stage="A", first_generation=1,
                    generations=PILOT_GENERATIONS,
                )
            ranked = rank_pilot_trajectories(self.starts)
            self.stage_a_ranking = [int(row["optimization_start_index"]) for row in ranked]
            survivors = list(ranked[: min(2, len(ranked))])
            self.stage_b_survivors = [int(row["optimization_start_index"]) for row in survivors]
            self._notify("pilot_complete", survivors=self.stage_b_survivors)

            self.stage = "DEEPEN"
            for start in survivors:
                self._run_generations(
                    start,
                    optimizers[start["optimization_start_index"]],
                    stage="B",
                    first_generation=PILOT_GENERATIONS + 1,
                    generations=DEEPEN_GENERATIONS,
                )
            self._verify_candidates()
            self.stage = "COMPLETE"
            self._notify("complete")
            return self.evidence()
        except Exception as error:
            self.stage = "ERROR"
            self.error = f"{type(error).__name__}: {error}"
            self._persist()
            raise

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "stage": self.stage,
            "error": self.error,
            "starts": [
                {
                    key: row[key]
                    for key in (
                        "optimization_start_index", "perceptual_rank", "preset_name",
                        "preset_category", "preset_relative_path", "base_score",
                        "adaptive_status", "continuous_parameters_considered",
                        "inventory_parameter_count", "module_count",
                        "structural_exclusion_count",
                        "module_probe_count", "responsive_modules",
                        "responsive_module_count", "individual_parameters_probed",
                        "module_behavior_exclusion_count",
                        "individual_probe_count", "probe_count", "responsive_count", "selected_dimension",
                        "module_probes",
                        "selected_parameters", "generation_summaries", "best",
                    )
                    if key in row
                }
                for row in self.starts + self.skipped_starts
            ],
            "evaluations": len(self.history),
            "budget": len(self.starts) * PILOT_EVALUATIONS
            + min(2, len(self.starts)) * DEEPEN_EVALUATIONS,
            "stage_a_ranking": list(self.stage_a_ranking),
            "stage_b_survivors": list(self.stage_b_survivors),
            "numeric_best": self.numeric_best,
            "verified_stable_best": self.verified_stable_best,
            "verification_attempt_count": len(self.verification_attempts),
            "cma_explanation": (
                "CMA-ES does not choose the preset. Retrieval chooses starting states; "
                "CMA-ES proposes combinations of frozen real Surge controls and updates "
                "its mean, covariance, and sigma from real-render perceptual scores."
            ),
        }

    def evidence(self) -> dict[str, Any]:
        return {
            **self.public_snapshot(),
            "started_at": self.started_at,
            "target_path": str(self.target_path),
            "target_sha256": sha256_file(self.target_path),
            "frozen_family_weights": dict(FAMILY_WEIGHTS),
            "frozen_family_medians": dict(self.family_medians),
            "configuration": {
                "master_seed": self.master_seed,
                "seed_derivation": "master_seed + perceptual_rank",
                "probe_delta": PROBE_DELTA,
                "baseline_replicates": BASELINE_REPLICATES,
                "module_response_epsilon": MODULE_RESPONSE_EPSILON,
                "probe_control": "neutral parameter event compared with plus/minus events",
                "parameter_retention": "requested and real applied values both recorded",
                "local_radius": LOCAL_RADIUS,
                "minimum_dimensions": MIN_DIMENSIONS,
                "maximum_dimensions": MAX_DIMENSIONS,
                "population": POPULATION_SIZE,
                "pilot_generations": PILOT_GENERATIONS,
                "deepen_generations": DEEPEN_GENERATIONS,
                "pilot_evaluations_per_start": PILOT_EVALUATIONS,
                "deepen_evaluations_per_survivor": DEEPEN_EVALUATIONS,
                "initial_sigma_local": INITIAL_SIGMA,
                "invalid_penalty": INVALID_PENALTY,
                "probe_workers": self.probe_workers,
            },
            "attempted_start_ranks": [
                int(row["perceptual_rank"]) for row in self.starts + self.skipped_starts
            ],
            "starts_full": self.starts,
            "skipped_starts_full": self.skipped_starts,
            "history": self.history,
            "verification_attempts": self.verification_attempts,
            "human_judgment": "PENDING_OWNER",
        }

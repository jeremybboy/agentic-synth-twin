"""Preset retrieval and local one-note refinement through the real Surge XT CLAP."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import random
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .audition import MIDI_KEY, NOTE_FRAMES, SAMPLE_RATE, TAIL_FRAMES, VELOCITY, _write_json_atomic
from .direct_search import (
    PLAYABLE_NOTE_FRAMES,
    cma,
    compute_audio_objective,
    objective_definition,
    prepare_audio_reference,
    spectrogram_preview,
)
from .external_targets import ExternalTarget, ExternalTargetBank
from .perceptual_retrieval import (
    DESCRIPTOR_VERSION,
    PerceptualRetrievalError,
    describe_audio,
    descriptor_preview,
    descriptor_definition,
    load_descriptor,
    normalize_and_rank,
    raw_family_distances,
    save_descriptor,
)
from .synth_adapter import PresetRef, SurgeXTAdapter, SynthAdapterError, sha256_file


SCHEMA = "agentic-synth-twin/surge-one-note-match/v1"
SEARCH_SEED = 20_260_930
RANDOM_SEED = 20_260_931
DEFAULT_PRESET_LIMIT = 120
DEFAULT_GENERATIONS = 15
DEFAULT_POPULATION = 8
CACHE_WORKERS = min(16, max(8, 2 * (os.cpu_count() or 4)))
CACHE_RETRY_WORKERS = 4
INITIAL_SIGMA = 0.18
SUCCESS_GATE_PERCENT = 25.0
TARGET_PRESET = "Basses/Attacky.fxp"
SURGE_ARTIFACTS = {
    "release": "1.3.4",
    "plugin_url": "https://github.com/surge-synthesizer/releases-xt/releases/download/1.3.4/surge-xt-macos-1.3.4-pluginsonly.zip",
    "plugin_md5": "8afca4159d9b417c5e07ebc1a5e96ed3",
    "content_url": "https://github.com/surge-synthesizer/releases-xt/releases/download/1.3.4/surge-xt-portable-content-1.3.4.tar.gz",
    "content_md5": "089b26486e9680aa4fe5121a7fd4c8c4",
}
DETERMINISTIC_OVERRIDES = {
    3_365_237_974: 1.0,  # A Osc1 Retrigger
    1_568_286_615: 1.0,  # A Osc2 Retrigger
    4_066_302_552: 1.0,  # A Osc3 Retrigger
    2_854_703_797: 1.0,  # B Osc1 Retrigger
    1_057_752_438: 1.0,  # B Osc2 Retrigger
    3_555_768_375: 1.0,  # B Osc3 Retrigger
}

# Real, continuous Surge XT 1.3.4 CLAP parameters. Names/ranges are still
# checked against the loaded preset inventory before every run.
ACTIVE_PARAMETER_IDS = (
    627_352_114,   # A Osc 1 Shape
    627_352_115,   # A Osc 1 Width 1
    627_352_119,   # A Osc 1 Unison Detune
    4_092_842_705, # A Filter 1 Cutoff
    8_095_466,     # A Filter 1 Resonance
    2_005_836_581, # A Amp EG Attack
    3_946_337_085, # A Amp EG Decay
    3_817_344_714, # A Amp EG Release
)
EXPECTED_NAMES = {
    627_352_114: "A Osc 1 Shape",
    627_352_115: "A Osc 1 Width 1",
    627_352_119: "A Osc 1 Unison Detune",
    4_092_842_705: "A Filter 1 Cutoff",
    8_095_466: "A Filter 1 Resonance",
    2_005_836_581: "A Amp EG Attack",
    3_946_337_085: "A Amp EG Decay",
    3_817_344_714: "A Amp EG Release",
}
TARGET_NORMALIZED = (0.65, 0.30, 0.35, 0.58, 0.24, 0.08, 0.46, 0.33)


class SurgeMatchError(RuntimeError):
    """Raised when Milestone 9 evidence cannot be attributed or reproduced."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rounded(value: float) -> float:
    return round(float(value), 12)


def _parameter_map(inventory: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    return {int(item["id"]): item for item in inventory["parameters"]}


def discover_active_parameters(inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate and return the predeclared continuous local-search controls."""

    available = _parameter_map(inventory)
    result = []
    for parameter_id in ACTIVE_PARAMETER_IDS:
        source = available.get(parameter_id)
        if source is None:
            raise SurgeMatchError(f"Surge parameter is absent: {parameter_id}")
        if source["name"] != EXPECTED_NAMES[parameter_id]:
            raise SurgeMatchError(
                f"Surge parameter identity changed: {parameter_id} is {source['name']!r}"
            )
        if source["flags"] & 1 or float(source["max"]) <= float(source["min"]):
            raise SurgeMatchError(f"Surge parameter is not continuous: {source['name']}")
        result.append(
            {
                "id": parameter_id,
                "name": source["name"],
                "label": source["name"].removeprefix("A "),
                "module": source.get("module", ""),
                "min": float(source["min"]),
                "max": float(source["max"]),
                "default": float(source["default"]),
                "current": float(source["current"]),
                "approval": "SEARCH",
                "approval_basis": "real inventory plus bounded continuous-control screen",
            }
        )
    return result


def vector_to_real(
    normalized: Sequence[float], parameters: Sequence[Mapping[str, Any]]
) -> dict[str, float]:
    if len(normalized) != len(parameters):
        raise SurgeMatchError("normalized vector length does not match active parameters")
    values: dict[str, float] = {}
    for value, parameter in zip(normalized, parameters, strict=True):
        numeric = float(value)
        if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise SurgeMatchError("normalized parameter values must be within 0..1")
        minimum, maximum = float(parameter["min"]), float(parameter["max"])
        values[str(parameter["id"])] = _rounded(minimum + numeric * (maximum - minimum))
    return values


def vector_from_current(parameters: Sequence[Mapping[str, Any]]) -> list[float]:
    values = []
    for parameter in parameters:
        minimum, maximum = float(parameter["min"]), float(parameter["max"])
        values.append(_rounded((float(parameter["current"]) - minimum) / (maximum - minimum)))
    return values


@dataclass(frozen=True)
class SurgePresetCache:
    """Target-independent, hash-attributed real Surge preset renders."""

    identity_sha256: str
    directory: Path
    synth_plugin: Mapping[str, Any]
    selected_count: int
    entries: tuple[Mapping[str, Any], ...]
    failures: tuple[Mapping[str, str], ...]
    reused: bool


def _preset_cache_identity(
    adapter: SurgeXTAdapter,
    presets: Sequence[PresetRef],
    preset_limit: int | None,
    *,
    full_library: bool,
) -> tuple[str, dict[str, Any]]:
    plugin_binary = adapter.plugin_path / "Contents/MacOS/Surge XT"
    factory_content = [
        {
            "relative_path": path.relative_to(adapter.factory_data_path).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(adapter.factory_data_path.rglob("*"))
        if path.is_file()
    ]
    payload = {
        "schema": "agentic-synth-twin/surge-preset-cache/v2",
        "plugin_bundle": adapter.plugin_path.name,
        "plugin_binary_sha256": sha256_file(plugin_binary),
        "preset_limit": preset_limit,
        "full_library": full_library,
        "descriptor_version": DESCRIPTOR_VERSION,
        "factory_content": factory_content,
        "presets": [
            {
                "relative_path": preset.relative_path,
                "sha256": sha256_file(preset.path),
            }
            for preset in presets
        ],
        "audition": {
            "midi_key": MIDI_KEY,
            "velocity": VELOCITY,
            "sample_rate": SAMPLE_RATE,
            "note_frames": NOTE_FRAMES,
            "tail_frames": TAIL_FRAMES,
            "block_size": 64,
        },
        "deterministic_overrides": {
            str(key): value for key, value in sorted(adapter.deterministic_overrides.items())
        },
        "selection": (
            "entire sorted factory library; retrieval eligibility is independent of search eligibility"
            if full_library
            else "deterministic category-round-robin regression fixture"
        ),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return digest, payload


def _migrate_compatible_cache(
    *, cache_root: Path, directory: Path, identity: str, identity_payload: Mapping[str, Any]
) -> SurgePresetCache | None:
    """Bind a completed cache to an expanded identity without rerendering audio."""

    comparable_keys = (
        "plugin_bundle",
        "plugin_binary_sha256",
        "preset_limit",
        "full_library",
        "descriptor_version",
        "presets",
        "audition",
        "deterministic_overrides",
    )
    for candidate in sorted(cache_root.glob("*/cache.json")):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            previous = payload["identity"]
            if any(previous.get(key) != identity_payload.get(key) for key in comparable_keys):
                continue
            migrated = {
                **payload,
                "schema": "agentic-synth-twin/surge-preset-cache/v2",
                "identity_sha256": identity,
                "identity": dict(identity_payload),
                "migrated_without_rerender_from": payload["identity_sha256"],
            }
            directory.mkdir(parents=True, exist_ok=True)
            migrated_file = directory / "cache.json"
            _write_json_atomic(migrated, migrated_file)
            validated = _load_valid_preset_cache(migrated_file, identity)
            if validated is not None:
                return validated
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _load_valid_preset_cache(
    cache_file: Path, expected_identity: str
) -> SurgePresetCache | None:
    if not cache_file.is_file():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if payload.get("identity_sha256") != expected_identity:
            return None
        entries = payload["entries"]
        if not isinstance(entries, list) or len(entries) < 5:
            return None
        for entry in entries:
            state_path = Path(entry["state_path"])
            audio_path = Path(entry["audio_path"])
            descriptor_path = Path(entry["descriptor_path"])
            if (
                not state_path.is_file()
                or not audio_path.is_file()
                or not descriptor_path.is_file()
                or sha256_file(state_path) != entry["state_sha256"]
                or sha256_file(audio_path) != entry["wav_sha256"]
                or sha256_file(descriptor_path) != entry["descriptor_sha256"]
            ):
                return None
        return SurgePresetCache(
            identity_sha256=expected_identity,
            directory=cache_file.parent,
            synth_plugin=payload["synth_plugin"],
            selected_count=int(payload["selected_count"]),
            entries=tuple(entries),
            failures=tuple(payload.get("failures", [])),
            reused=True,
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def prepare_shared_preset_cache(
    *,
    adapter: SurgeXTAdapter,
    cache_root: str | Path,
    preset_limit: int,
    full_library: bool = False,
) -> SurgePresetCache:
    """Build or reuse real preset renders independently of any target audio."""

    presets = (
        adapter.factory_presets()
        if full_library
        else adapter.bounded_factory_presets(
            preset_limit, required_relative_path=TARGET_PRESET
        )
    )
    identity, identity_payload = _preset_cache_identity(
        adapter, presets, None if full_library else preset_limit, full_library=full_library
    )
    directory = Path(cache_root).resolve() / identity[:20]
    cache_file = directory / "cache.json"
    cached = _load_valid_preset_cache(cache_file, identity)
    if cached is not None:
        return cached
    migrated = _migrate_compatible_cache(
        cache_root=Path(cache_root).resolve(),
        directory=directory,
        identity=identity,
        identity_payload=identity_payload,
    )
    if migrated is not None:
        return migrated
    directory.mkdir(parents=True, exist_ok=True)
    state_directory = directory / "states"
    audio_directory = directory / "audio"
    descriptor_directory = directory / "descriptors"
    record_directory = directory / "records"
    state_directory.mkdir(exist_ok=True)
    audio_directory.mkdir(exist_ok=True)
    descriptor_directory.mkdir(exist_ok=True)
    record_directory.mkdir(exist_ok=True)

    def render_entry(item: tuple[int, PresetRef]) -> dict[str, Any]:
        ordinal, preset = item
        slug = f"preset-{ordinal:03d}"
        state_path = state_directory / f"{slug}.bin"
        wav_path = audio_directory / f"{slug}.wav"
        descriptor_path = descriptor_directory / f"{slug}.npz"
        record_path = record_directory / f"{slug}.json"
        if record_path.is_file():
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if (
                    record["preset_relative_path"] == preset.relative_path
                    and sha256_file(state_path) == record["state_sha256"]
                    and sha256_file(wav_path) == record["wav_sha256"]
                    and sha256_file(descriptor_path) == record["descriptor_sha256"]
                ):
                    return record
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                pass
        provenance = adapter.extract_preset_state(preset, state_path)
        rendered = adapter.render_verified(
            state_path=state_path,
            wav_path=wav_path,
            parameter_values={},
            midi_key=MIDI_KEY,
            velocity=VELOCITY,
            note_frames=NOTE_FRAMES,
        )
        if float(rendered["peak_float"]) <= 0:
            raise SurgeMatchError("preset render is silent")
        descriptor = describe_audio(wav_path)
        save_descriptor(descriptor, descriptor_path)
        inventory = adapter.inspect_state(state_path)
        try:
            search_parameters = discover_active_parameters(inventory)
            search_eligible = True
            search_ineligible_reason = None
        except SurgeMatchError as error:
            search_parameters = []
            search_eligible = False
            search_ineligible_reason = str(error)
        record = {
            "preset_name": preset.name,
            "preset_category": preset.category,
            "preset_relative_path": preset.relative_path,
            "state_path": str(state_path),
            "audio_path": str(wav_path),
            "retrieval_eligible": True,
            "search_eligible": search_eligible,
            "search_ineligible_reason": search_ineligible_reason,
            "active_parameter_ids": [item["id"] for item in search_parameters],
            "descriptor_version": DESCRIPTOR_VERSION,
            "descriptor_path": str(descriptor_path),
            "descriptor_sha256": sha256_file(descriptor_path),
            **provenance,
            "wav_sha256": rendered["wav_sha256"],
            "peak_float": rendered["peak_float"],
            "deterministic": rendered["byte_identical"],
        }
        _write_json_atomic(record, record_path)
        return record

    entries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    failed_items: list[tuple[int, PresetRef]] = []
    numbered_presets = list(enumerate(presets, start=1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=CACHE_WORKERS) as executor:
        futures = {
            executor.submit(render_entry, item): item[1]
            for item in numbered_presets
        }
        for completed_count, future in enumerate(
            concurrent.futures.as_completed(futures), start=1
        ):
            preset = futures[future]
            try:
                entries.append(future.result())
            except (
                SynthAdapterError,
                SurgeMatchError,
                PerceptualRetrievalError,
                OSError,
                ValueError,
            ) as error:
                failures.append(
                    {
                        "preset": preset.relative_path,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                failed_items.append(
                    next(item for item in numbered_presets if item[1] == preset)
                )
            if completed_count % 100 == 0 or completed_count == len(presets):
                print(
                    f"Surge preset cache: {completed_count}/{len(presets)} attempted · "
                    f"{len(entries)} retrieval eligible · {len(failures)} failed",
                    flush=True,
                )
    if failed_items:
        print(
            f"Surge preset cache: retrying {len(failed_items)} failures with "
            f"{CACHE_RETRY_WORKERS} workers",
            flush=True,
        )
        failures = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=CACHE_RETRY_WORKERS
        ) as executor:
            retry_futures = {
                executor.submit(render_entry, item): item[1] for item in failed_items
            }
            for future in concurrent.futures.as_completed(retry_futures):
                preset = retry_futures[future]
                try:
                    entries.append(future.result())
                except (
                    SynthAdapterError,
                    SurgeMatchError,
                    PerceptualRetrievalError,
                    OSError,
                    ValueError,
                ) as error:
                    failures.append(
                        {
                            "preset": preset.relative_path,
                            "error": f"{type(error).__name__}: {error}",
                        }
                    )
    entries.sort(key=lambda row: row["preset_relative_path"])
    failures.sort(key=lambda row: row["preset"])
    if len(entries) < 5:
        raise SurgeMatchError("fewer than five deterministic factory presets were cached")
    first_inventory = adapter.inspect_state(entries[0]["state_path"])
    synth_plugin = dict(first_inventory["plugin"])
    payload = {
        "schema": "agentic-synth-twin/surge-preset-cache/v2",
        "identity_sha256": identity,
        "identity": identity_payload,
        "synth_plugin": synth_plugin,
        "selected_count": len(presets),
        "indexed_count": len(entries),
        "retrieval_eligible_count": len(entries),
        "search_eligible_count": sum(bool(row["search_eligible"]) for row in entries),
        "failed_count": len(failures),
        "entries": entries,
        "failures": failures,
        "descriptor": descriptor_definition(),
    }
    _write_json_atomic(payload, cache_file)
    return SurgePresetCache(
        identity_sha256=identity,
        directory=directory,
        synth_plugin=synth_plugin,
        selected_count=len(presets),
        entries=tuple(entries),
        failures=tuple(failures),
        reused=False,
    )


class SurgeMatchRun:
    """Thread-safe preset retrieval plus ask/render/score/tell controller."""

    def __init__(
        self,
        *,
        adapter: SurgeXTAdapter,
        output_directory: str | Path,
        preset_limit: int = DEFAULT_PRESET_LIMIT,
        generations: int = DEFAULT_GENERATIONS,
        population_size: int = DEFAULT_POPULATION,
        seed: int = SEARCH_SEED,
        shared_cache: SurgePresetCache | None = None,
        random_control_enabled: bool = True,
    ) -> None:
        if generations < 1 or population_size < 2 or preset_limit < 5:
            raise SurgeMatchError("invalid preset or local-search budget")
        self.adapter = adapter
        self.output_directory = Path(output_directory).resolve()
        self.audio_directory = self.output_directory / "audio"
        self.state_directory = self.output_directory / "states"
        self.audio_directory.mkdir(parents=True, exist_ok=True)
        self.state_directory.mkdir(exist_ok=True)
        self.preset_limit = preset_limit
        self.generations = generations
        self.population_size = population_size
        self.budget = generations * population_size
        self.seed = seed
        self.shared_cache = shared_cache
        self.random_control_enabled = random_control_enabled
        self.run_id = f"m9-{uuid.uuid4().hex[:12]}"
        self.created_at = _now()
        self.status = "IDLE"
        self.error: str | None = None
        self.stopping_reason: str | None = None
        self.revealed = False
        self.history: list[dict[str, Any]] = []
        self.generation_summaries: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.best: dict[str, Any] | None = None
        self.final_verification: dict[str, Any] | None = None
        self.random_control: dict[str, Any] | None = None
        self.started_monotonic: float | None = None
        self.elapsed_before_start = 0.0
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._playable_lock = threading.Lock()
        self._pause_requested = False
        self._stop_requested = False
        self._prepare_target_and_index()
        self._persist()

    @property
    def target_path(self) -> Path:
        return self.audio_directory / "target.wav"

    @property
    def start_path(self) -> Path:
        return self.audio_directory / "base-preset.wav"

    @property
    def best_path(self) -> Path | None:
        return Path(self.best["audio_path"]) if self.best else None

    def _prepare_target_and_index(self) -> None:
        presets = self.adapter.bounded_factory_presets(
            self.preset_limit, required_relative_path=TARGET_PRESET
        )
        target_preset = next(p for p in presets if p.relative_path == TARGET_PRESET)
        target_state = self.state_directory / "hidden-target-base.bin"
        self.target_preset_provenance = self.adapter.extract_preset_state(
            target_preset, target_state
        )
        target_inventory = self.adapter.inspect_state(target_state)
        target_parameters = discover_active_parameters(target_inventory)
        target_values = vector_to_real(TARGET_NORMALIZED, target_parameters)
        self.target = self.adapter.render_verified(
            state_path=target_state,
            wav_path=self.target_path,
            parameter_values=target_values,
            midi_key=MIDI_KEY,
            velocity=VELOCITY,
            note_frames=NOTE_FRAMES,
        )
        if float(self.target["peak_float"]) <= 0:
            raise SurgeMatchError("hidden target render is silent")
        self.target["target_parameters_private"] = {
            "normalized": list(TARGET_NORMALIZED),
            "real": target_values,
            "base_preset": TARGET_PRESET,
        }
        self.target_identity = {
            "mode": "hidden-surge-regression",
            "target_id": "hidden-surge-regression",
            "title": "Hidden Surge Regression Target",
            "file_name": self.target_path.name,
            "sha256": self.target["wav_sha256"],
        }
        self.reference = prepare_audio_reference(self.target_path)
        if self.shared_cache is None:
            self.shared_cache = prepare_shared_preset_cache(
                adapter=self.adapter,
                cache_root=self.output_directory / "shared-preset-cache",
                preset_limit=self.preset_limit,
            )
        self._rank_cache_and_initialize()

    def _rank_cache_and_initialize(self) -> None:
        assert self.shared_cache is not None
        ranking: list[dict[str, Any]] = []
        for entry in self.shared_cache.entries:
            if not entry.get("search_eligible", False):
                continue
            objective = compute_audio_objective(self.reference, entry["audio_path"])
            ranking.append({**entry, **objective})
        ranking.sort(key=lambda row: (row["total_loss"], row["preset_relative_path"]))
        if len(ranking) < 5:
            raise SurgeMatchError("fewer than five search-eligible factory presets were indexed")
        for rank, row in enumerate(ranking, start=1):
            row["rank"] = rank
            if rank <= 10:
                destination = self.audio_directory / "presets" / f"rank-{rank:02d}.wav"
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(row["audio_path"], destination)
                if sha256_file(destination) != row["wav_sha256"]:
                    raise SurgeMatchError("copied preset WAV hash differs from shared cache")
                row["audio_url"] = f"/audio/presets/{destination.name}"
        self.preset_ranking = ranking
        self.legacy_preset_ranking = ranking
        self.perceptual_preset_ranking: list[dict[str, Any]] = []
        self.retrieval_family_medians: dict[str, float] = {}
        self.retrieval_sanity: dict[str, Any] = {}
        self.preset_failures = list(self.shared_cache.failures)
        self._initialize_selected_base(ranking[0])
        self._write_preset_index(
            entries=ranking,
            selection=(
                "deterministic category-round-robin regression fixture; "
                "exact authorized eight-parameter inventory required before ranking"
            ),
        )

    def _initialize_selected_base(self, base_preset: Mapping[str, Any]) -> None:
        assert self.shared_cache is not None
        self.base_preset = dict(base_preset)
        base_state = Path(self.base_preset["state_path"])
        self.base_state_path = self.state_directory / "selected-base.bin"
        self.base_state_path.write_bytes(base_state.read_bytes())
        base_inventory = self.adapter.inspect_state(self.base_state_path)
        self.synth_plugin = dict(base_inventory["plugin"])
        if self.synth_plugin != dict(self.shared_cache.synth_plugin):
            raise SurgeMatchError("shared preset cache plugin identity changed")
        self.parameters = discover_active_parameters(base_inventory)
        self.start_vector = vector_from_current(self.parameters)
        self.start = self.adapter.render_verified(
            state_path=self.base_state_path,
            wav_path=self.start_path,
            parameter_values=vector_to_real(self.start_vector, self.parameters),
            midi_key=MIDI_KEY,
            velocity=VELOCITY,
            note_frames=NOTE_FRAMES,
        )
        self.start.update(compute_audio_objective(self.reference, self.start_path))
        self.best = {
            "run_id": self.run_id,
            "target_id": self.target_identity["target_id"],
            "target_title": self.target_identity["title"],
            "target_file_name": self.target_identity["file_name"],
            "target_wav_sha256": self.target_identity["sha256"],
            "generation": 0,
            "candidate_index": 0,
            "total_evaluations": 0,
            "parameter_values_normalized": list(self.start_vector),
            "parameter_values_real": vector_to_real(self.start_vector, self.parameters),
            **{
                key: self.start[key]
                for key in (
                    "spectral_loss",
                    "envelope_loss",
                    "loudness_loss",
                    "total_loss",
                    "candidate_rms_dbfs",
                    "target_rms_dbfs",
                    "wav_sha256",
                    "peak_float",
                    "clipped_samples",
                )
            },
            "audio_path": str(self.start_path),
            "audio_url": "/audio/base-preset.wav",
            "is_new_best": False,
            "best_loss": self.start["total_loss"],
            "timestamp": self.created_at,
        }
        self._optimizer_state = {
            "mean": list(self.start_vector),
            "spread": [INITIAL_SIGMA] * len(self.start_vector),
            "sigma": INITIAL_SIGMA,
        }

    def _write_preset_index(
        self,
        *,
        entries: Sequence[Mapping[str, Any]],
        selection: str,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        assert self.shared_cache is not None
        index = {
            "schema": SCHEMA,
            "surge": self.synth_plugin,
            "target_wav_sha256": self.target["wav_sha256"],
            "target": self.target_identity,
            "preset_cache_identity_sha256": self.shared_cache.identity_sha256,
            "preset_cache_reused": self.shared_cache.reused,
            "scope": "factory presets only",
            "selected_count": self.shared_cache.selected_count,
            "indexed_count": len(entries),
            "failed_count": len(self.preset_failures),
            "retrieval_eligible_count": len(self.shared_cache.entries),
            "search_eligible_count": sum(
                bool(row.get("search_eligible")) for row in self.shared_cache.entries
            ),
            "selection": selection,
            "index_workers": CACHE_WORKERS,
            "audition": self._audition_contract(),
            "objective": objective_definition(),
            "entries": list(entries),
            "failures": self.preset_failures,
            **dict(extra or {}),
        }
        _write_json_atomic(index, self.output_directory / "preset-index.json")

    def _audition_contract(self) -> dict[str, Any]:
        return {
            "midi_key": MIDI_KEY,
            "velocity": VELOCITY,
            "sample_rate": SAMPLE_RATE,
            "block_size": 64,
            "note_frames": NOTE_FRAMES,
            "tail_frames": TAIL_FRAMES,
        }

    def _render_vector(self, vector: Sequence[float], path: Path) -> dict[str, Any]:
        rendered = self.adapter.render_note(
            state_path=self.base_state_path,
            wav_path=path,
            parameter_values=vector_to_real(vector, self.parameters),
            midi_key=MIDI_KEY,
            velocity=VELOCITY,
            note_frames=NOTE_FRAMES,
            allow_clipping=True,
        )
        return {
            "parameter_values_normalized": [_rounded(value) for value in vector],
            "parameter_values_real": vector_to_real(vector, self.parameters),
            "audio_path": str(path),
            "wav_sha256": rendered["wav_sha256"],
            "peak_float": rendered["peak_float"],
            "clipped_samples": rendered["clipped_samples"],
        }

    def start_search(self) -> dict[str, Any]:
        with self._condition:
            if self.status == "PAUSED":
                return self.resume_search()
            if self.status != "IDLE":
                raise SurgeMatchError(f"cannot start search from {self.status}")
            self.status = "SEARCHING"
            self.started_monotonic = time.monotonic()
            self._thread = threading.Thread(target=self._search_worker, daemon=True)
            self._thread.start()
            self._persist()
            return self.public_status()

    def pause_search(self) -> dict[str, Any]:
        with self._condition:
            if self.status not in {"SEARCHING", "PAUSING"}:
                raise SurgeMatchError(f"cannot pause search from {self.status}")
            self._pause_requested = True
            self.status = "PAUSING"
            self._persist()
            return self.public_status()

    def resume_search(self) -> dict[str, Any]:
        with self._condition:
            if self.status != "PAUSED":
                raise SurgeMatchError(f"cannot resume search from {self.status}")
            self._pause_requested = False
            self.status = "SEARCHING"
            self._condition.notify_all()
            self._persist()
            return self.public_status()

    def stop_search(self) -> dict[str, Any]:
        with self._condition:
            if self.status not in {"SEARCHING", "PAUSING", "PAUSED"}:
                raise SurgeMatchError(f"cannot stop search from {self.status}")
            self._stop_requested = True
            self._pause_requested = False
            self._condition.notify_all()
            self._persist()
            return self.public_status()

    def reveal_target(self) -> dict[str, Any]:
        with self._lock:
            if self.status not in {"COMPLETE", "STOPPED", "ERROR"}:
                raise SurgeMatchError("target state remains hidden during search")
            self.revealed = True
            self._persist()
            return self.public_status()

    def _wait_if_needed(self) -> bool:
        with self._condition:
            if self._stop_requested:
                return True
            if self._pause_requested:
                self.status = "PAUSED"
                self._persist()
                while self._pause_requested and not self._stop_requested:
                    self._condition.wait(timeout=0.5)
            return self._stop_requested

    def _search_worker(self) -> None:
        try:
            optimizer = cma.CMAEvolutionStrategy(
                self.start_vector,
                INITIAL_SIGMA,
                {
                    "bounds": [0.0, 1.0],
                    "seed": self.seed,
                    "popsize": self.population_size,
                    "maxiter": self.generations,
                    "verbose": -9,
                    "verb_disp": 0,
                    "verb_log": 0,
                },
            )
            for generation in range(1, self.generations + 1):
                if self._wait_if_needed():
                    break
                vectors = optimizer.ask()
                told, losses, generation_losses = [], [], []
                for candidate_index, raw in enumerate(vectors, start=1):
                    if len(self.history) >= self.budget or self._wait_if_needed():
                        break
                    vector = [min(1.0, max(0.0, float(value))) for value in raw]
                    record = self._evaluate(vector, generation, candidate_index)
                    told.append(list(raw))
                    losses.append(record["total_loss"])
                    generation_losses.append(record["total_loss"])
                if told:
                    optimizer.tell(told, losses)
                    spread = optimizer.sigma * np.sqrt(
                        np.maximum(np.diag(np.asarray(optimizer.C)), 0)
                    )
                    with self._lock:
                        self._optimizer_state = {
                            "mean": [_rounded(value) for value in optimizer.mean],
                            "spread": [_rounded(value) for value in spread],
                            "sigma": _rounded(optimizer.sigma),
                        }
                        self.generation_summaries.append(
                            {
                                "generation": generation,
                                "median_loss": _rounded(np.median(generation_losses)),
                                "best_loss": (
                                    self.best["total_loss"]
                                    if self.best is not None
                                    else self.start["total_loss"]
                                ),
                                "evaluations": len(self.history),
                            }
                        )
                        self._persist()
                if self._stop_requested or len(told) < len(vectors):
                    break
            with self._lock:
                self.status = "STOPPED" if self._stop_requested else "COMPLETE"
                self.stopping_reason = "user_stop" if self._stop_requested else "fixed_budget_exhausted"
                self._verify_final_best()
                self.random_control = (
                    self._run_random_control() if self.random_control_enabled else None
                )
                self._persist()
        except Exception as error:
            with self._lock:
                self.status = "ERROR"
                self.error = f"{type(error).__name__}: {error}"
                self.stopping_reason = "error"
                self._persist()

    def _evaluate(self, vector: Sequence[float], generation: int, candidate_index: int) -> dict[str, Any]:
        evaluation = len(self.history) + 1
        path = self.audio_directory / f"eval-{evaluation:04d}.wav"
        rendered = self._render_vector(vector, path)
        objective = compute_audio_objective(self.reference, path)
        invalid_reason = None
        if rendered["clipped_samples"]:
            invalid_reason = f"clipped_samples={rendered['clipped_samples']}"
            objective.update(
                {
                    "spectral_loss": 1.0,
                    "envelope_loss": 1.0,
                    "loudness_loss": 1.0,
                    "total_loss": 1.0,
                }
            )
        with self._lock:
            prior = self.best["total_loss"] if self.best else float(self.start["total_loss"])
            is_new_best = objective["total_loss"] < prior
            record = {
                "run_id": self.run_id,
                "target_id": self.target_identity["target_id"],
                "target_title": self.target_identity["title"],
                "target_file_name": self.target_identity["file_name"],
                "target_wav_sha256": self.target_identity["sha256"],
                "generation": generation,
                "candidate_index": candidate_index,
                "total_evaluations": evaluation,
                **rendered,
                **objective,
                "is_new_best": is_new_best,
                "best_loss": objective["total_loss"] if is_new_best else prior,
                "optimizer_mean": list(self._optimizer_state["mean"]),
                "optimizer_spread": list(self._optimizer_state["spread"]),
                "optimizer_sigma": self._optimizer_state["sigma"],
                "audio_url": f"/audio/{path.name}",
                "timestamp": _now(),
                "invalid_reason": invalid_reason,
            }
            self.history.append(record)
            self.current = record
            if is_new_best:
                self.best = dict(record)
            with (self.output_directory / "history.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._persist()
            return record

    def _verify_final_best(self) -> None:
        vector = self.best["parameter_values_normalized"] if self.best else self.start_vector
        expected_hash = self.best["wav_sha256"] if self.best else self.start["wav_sha256"]
        verified = self.adapter.render_verified(
            state_path=self.base_state_path,
            wav_path=self.audio_directory / "final-best-rerender.wav",
            parameter_values=vector_to_real(vector, self.parameters),
            midi_key=MIDI_KEY,
            velocity=VELOCITY,
            note_frames=NOTE_FRAMES,
        )
        if verified["wav_sha256"] != expected_hash:
            raise SurgeMatchError("final best independent rerender hash differs")
        self.final_verification = {
            **verified,
            "matches_search_best": True,
            "search_best_sha256": expected_hash,
        }

    def _run_random_control(self) -> dict[str, Any]:
        generator = random.Random(RANDOM_SEED)
        best: dict[str, Any] | None = None
        directory = self.audio_directory / "random-control"
        directory.mkdir(exist_ok=True)
        for evaluation in range(1, self.budget + 1):
            vector = [generator.random() for _ in self.parameters]
            path = directory / f"random-{evaluation:04d}.wav"
            rendered = self._render_vector(vector, path)
            objective = compute_audio_objective(self.reference, path)
            invalid_reason = None
            if rendered["clipped_samples"]:
                invalid_reason = f"clipped_samples={rendered['clipped_samples']}"
                objective.update(
                    {
                        "spectral_loss": 1.0,
                        "envelope_loss": 1.0,
                        "loudness_loss": 1.0,
                        "total_loss": 1.0,
                    }
                )
            row = {
                "evaluation": evaluation,
                "parameter_values_normalized": rendered["parameter_values_normalized"],
                "parameter_values_real": rendered["parameter_values_real"],
                **objective,
                "wav_sha256": rendered["wav_sha256"],
                "clipped_samples": rendered["clipped_samples"],
                "invalid_reason": invalid_reason,
            }
            if best is None or row["total_loss"] < best["total_loss"]:
                best = row
        assert best is not None
        return {
            "method": "bounded_local_random_search",
            "seed": RANDOM_SEED,
            "budget": self.budget,
            "same_target_base_parameters_bounds_objective": True,
            "best": best,
        }

    def elapsed_seconds(self) -> float:
        if self.started_monotonic is None:
            return self.elapsed_before_start
        return self.elapsed_before_start + time.monotonic() - self.started_monotonic

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            start_loss = float(self.start["total_loss"])
            best_loss = float(self.best["total_loss"]) if self.best else start_loss
            improvement = 100.0 * (start_loss - best_loss) / start_loss if start_loss else 0.0
            def public_retrieval_row(row: Mapping[str, Any]) -> dict[str, Any]:
                keys = (
                    "rank", "perceptual_rank", "legacy_rank", "preset_name",
                    "preset_category", "preset_relative_path", "retrieval_score",
                    "total_loss", "spectral_loss", "envelope_loss", "loudness_loss",
                    "retrieval_eligible", "search_eligible", "search_ineligible_reason",
                    "raw_distances", "normalized_distances", "weighted_contributions",
                    "audio_url",
                )
                return {key: row[key] for key in keys if key in row}

            payload = {
                "schema": SCHEMA,
                "run_id": self.run_id,
                "status": self.status,
                "error": self.error,
                "generation": self.current["generation"] if self.current else 0,
                "generations": self.generations,
                "evaluations": len(self.history),
                "budget": self.budget,
                "evaluations_remaining": max(0, self.budget - len(self.history)),
                "current_loss": self.current["total_loss"] if self.current else None,
                "best_loss": _rounded(best_loss),
                "starting_loss": _rounded(start_loss),
                "improvement_percent": _rounded(improvement),
                "elapsed_seconds": _rounded(self.elapsed_seconds()),
                "current": self.current,
                "best": self.best,
                "parameters": self.parameters,
                "fixed_filter": None,
                "optimizer": {
                    "name": "CMA-ES",
                    "implementation": "pycma",
                    "version": cma.__version__,
                    "seed": self.seed,
                    "population_size": self.population_size,
                    **self._optimizer_state,
                },
                "objective": objective_definition(),
                "generation_summaries": list(self.generation_summaries),
                "target_audio_url": "/audio/target.wav",
                "target": dict(self.target_identity),
                "starting_audio_url": "/audio/base-preset.wav",
                "best_audio_url": self.best["audio_url"] if self.best else None,
                "base_label": "BASE SURGE PRESET",
                "base_preset": {
                    key: self.base_preset[key]
                    for key in (
                        "preset_name", "preset_category", "preset_relative_path", "total_loss",
                        "perceptual_rank", "legacy_rank", "retrieval_score",
                    )
                    if key in self.base_preset
                },
                "preset_ranking": [
                    {
                        key: row[key]
                        for key in (
                            "rank", "preset_name", "preset_category", "preset_relative_path",
                            "total_loss", "spectral_loss", "envelope_loss", "loudness_loss", "audio_url",
                        )
                    }
                    for row in self.preset_ranking[:5]
                ],
                "legacy_preset_ranking": [
                    public_retrieval_row(row) for row in self.legacy_preset_ranking[:10]
                ],
                "perceptual_preset_ranking": [
                    public_retrieval_row(row) for row in self.perceptual_preset_ranking[:10]
                ],
                "retrieval_family_medians": dict(self.retrieval_family_medians),
                "retrieval_coverage": getattr(self, "retrieval_coverage", None),
                "retrieval_sanity": dict(self.retrieval_sanity),
                "synth": {
                    "name": self.synth_plugin["name"],
                    "version": self.synth_plugin["version"],
                    "id": self.synth_plugin["id"],
                },
                "can_reveal": self.status in {"COMPLETE", "STOPPED", "ERROR"},
                "target_revealed": self.revealed,
                "stopping_reason": self.stopping_reason,
                "final_verification": self.final_verification,
                "random_control": self.random_control,
                "random_control_enabled": self.random_control_enabled,
                "preset_cache": {
                    "identity_sha256": self.shared_cache.identity_sha256,
                    "reused": self.shared_cache.reused,
                    "indexed_count": len(self.shared_cache.entries),
                },
                "success_gate_percent": SUCCESS_GATE_PERCENT,
            }
            if self.revealed:
                payload["target_parameters"] = self.target["target_parameters_private"]
            return payload

    def private_evidence(self) -> dict[str, Any]:
        best_vector = (
            self.best["parameter_values_normalized"]
            if self.best is not None
            else self.start_vector
        )
        base_values = vector_to_real(self.start_vector, self.parameters)
        final_values = vector_to_real(best_vector, self.parameters)
        changed_parameters = [
            {
                "id": parameter["id"],
                "name": parameter["name"],
                "base_value": base_values[str(parameter["id"])],
                "optimized_value": final_values[str(parameter["id"])],
            }
            for parameter in self.parameters
            if not math.isclose(
                base_values[str(parameter["id"])],
                final_values[str(parameter["id"])],
                abs_tol=1e-12,
            )
        ]
        return {
            **self.public_status(),
            "created_at": self.created_at,
            "plugin": {
                **self.synth_plugin,
                "path": str(self.adapter.plugin_path),
                "sha256": sha256_file(self.adapter.plugin_path / "Contents/MacOS/Surge XT"),
            },
            "preset_library": {
                "release_artifacts": SURGE_ARTIFACTS,
                "path": str(self.adapter.factory_data_path),
                "scope": "factory presets only",
                "selected": self.shared_cache.selected_count,
                "indexed": len(self.shared_cache.entries),
                "failures": self.preset_failures,
                "cache_identity_sha256": self.shared_cache.identity_sha256,
                "cache_directory": str(self.shared_cache.directory),
                "cache_reused": self.shared_cache.reused,
            },
            "audition": self._audition_contract(),
            "hidden_target": self.target,
            "hidden_target_preset_provenance": self.target_preset_provenance,
            "selected_base": self.base_preset,
            "base_render": self.start,
            "final_result": {
                "base_synth": self.synth_plugin,
                "base_preset": self.base_preset["preset_relative_path"],
                "base_preset_state_sha256": sha256_file(self.base_state_path),
                "target_audio_sha256": self.target["wav_sha256"],
                "changed_parameters": changed_parameters,
                "objective_before": self.start["total_loss"],
                "objective_after": self.best["total_loss"] if self.best else self.start["total_loss"],
                "final_audio_sha256": self.best["wav_sha256"] if self.best else self.start["wav_sha256"],
                "final_state": "base native state plus exact changed parameter values",
            },
            "active_parameter_policy": {
                "count": len(self.parameters),
                "categorical_parameters": "excluded",
                "approved": self.parameters,
            },
            "predeclared_success": {
                "surge_integrity": "deterministic state and audio rerender",
                "preset_retrieval": "ranked bounded factory index under identical audition",
                "local_optimization": f">={SUCCESS_GATE_PERCENT}% total-objective improvement",
                "human_listening": "PENDING_OWNER_AUDITION",
            },
            "limitations": [
                "The objective is a transparent proxy, not human perceptual equivalence.",
                "Only C3 at one velocity and duration is optimized and validated.",
                "Playing other notes is exploratory listening, not multi-note validation.",
                "Factory categories are retained as metadata, not acoustic truth.",
            ],
        }

    def _persist(self) -> None:
        if self.started_monotonic is not None and self.status in {"COMPLETE", "STOPPED", "ERROR"}:
            self.elapsed_before_start = time.monotonic() - self.started_monotonic
            self.started_monotonic = None
        _write_json_atomic(self.private_evidence(), self.output_directory / "run.json")

    def history_public(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self.history]

    def render_playable_note(
        self,
        *,
        source: str,
        midi_key: int,
        velocity: int,
        normalized: Sequence[float] | None = None,
    ) -> Path:
        if not 21 <= midi_key <= 108:
            raise SurgeMatchError("playable MIDI key must be in the piano range 21..108")
        if not 1 <= velocity <= 127:
            raise SurgeMatchError("playable velocity must be in 1..127")
        state_path = self.base_state_path
        if source == "custom":
            if normalized is None or len(normalized) != len(self.parameters):
                raise SurgeMatchError("custom patch requires one value per active parameter")
            vector = [float(value) for value in normalized]
        elif source == "target":
            state_path = Path(self.target_preset_provenance["state_path"])
            vector = list(TARGET_NORMALIZED)
        elif source == "starting":
            vector = list(self.start_vector)
        elif source == "best":
            vector = list(self.best["parameter_values_normalized"] if self.best else self.start_vector)
        else:
            raise SurgeMatchError("playable source must be target, starting, best, or custom")
        values = vector_to_real(vector, self.parameters)
        signature = uuid.uuid5(
            uuid.NAMESPACE_URL,
            json.dumps([source, vector, midi_key, velocity, PLAYABLE_NOTE_FRAMES]),
        ).hex[:20]
        directory = self.audio_directory / "playable"
        directory.mkdir(exist_ok=True)
        path = directory / f"{source}-n{midi_key}-v{velocity}-{signature}.wav"
        with self._playable_lock:
            if not path.is_file():
                self.adapter.render_note(
                    state_path=state_path,
                    wav_path=path,
                    parameter_values=values,
                    midi_key=midi_key,
                    velocity=velocity,
                    note_frames=PLAYABLE_NOTE_FRAMES,
                )
        return path

    def audio_path_for_url(self, url_path: str) -> Path:
        relative = Path(url_path.removeprefix("/audio/"))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise SurgeMatchError("invalid audio path")
        path = (self.audio_directory / relative).resolve()
        if self.audio_directory not in path.parents or not path.is_file():
            raise SurgeMatchError("audio file does not exist")
        return path

    def spectra_public(self) -> dict[str, Any]:
        target = spectrogram_preview(self.target_path)
        best = spectrogram_preview(self.best_path or self.start_path)
        maximum = max(
            max(max(row) for row in target["values"]),
            max(max(row) for row in best["values"]),
        )
        return {"target": target, "best": best, "shared_max": maximum}

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)


class ExternalSurgeMatchRun(SurgeMatchRun):
    """One target-specific real Surge run initialized from a shared preset cache."""

    def __init__(
        self,
        *,
        target: ExternalTarget,
        shared_cache: SurgePresetCache,
        **kwargs: Any,
    ) -> None:
        self.external_target = target
        super().__init__(
            shared_cache=shared_cache,
            random_control_enabled=False,
            **kwargs,
        )

    def _prepare_target_and_index(self) -> None:
        shutil.copyfile(self.external_target.path, self.target_path)
        if sha256_file(self.target_path) != self.external_target.sha256:
            raise SurgeMatchError("external target copy differs from canonical fixture")
        self.target = {
            "mode": "external-procedural-reference",
            "target_id": self.external_target.target_id,
            "title": self.external_target.title,
            "file_name": self.external_target.file_name,
            "wav_path": str(self.target_path),
            "wav_sha256": self.external_target.sha256,
            "source_wav_sha256": self.external_target.sha256,
            "surge_used": False,
            "third_party_samples_used": False,
            "preset_library_used": False,
        }
        self.target_identity = {
            "mode": "external-procedural-reference",
            **self.external_target.public_record(),
        }
        self.target_preset_provenance = {
            "mode": "external-procedural-reference",
            "state_path": None,
            "wav_sha256": self.external_target.sha256,
        }
        self.reference = prepare_audio_reference(self.target_path)
        self._rank_cache_and_initialize()

    def _rank_cache_and_initialize(self) -> None:
        """Audit legacy and perceptual retrieval before selecting a CMA base."""

        assert self.shared_cache is not None
        target_descriptor = describe_audio(self.target_path)
        self.target_retrieval_descriptor = target_descriptor
        perceptual_inputs: list[dict[str, Any]] = []
        legacy: list[dict[str, Any]] = []
        for entry in self.shared_cache.entries:
            descriptor = load_descriptor(entry["descriptor_path"])
            perceptual_inputs.append(
                {**entry, "raw_distances": raw_family_distances(target_descriptor, descriptor)}
            )
            legacy.append(
                {**entry, **compute_audio_objective(self.reference, entry["audio_path"])}
            )
        perceptual, medians = normalize_and_rank(perceptual_inputs)
        legacy.sort(key=lambda row: (row["total_loss"], row["preset_relative_path"]))
        legacy_rank = {}
        legacy_by_path = {}
        for rank, row in enumerate(legacy, start=1):
            row["legacy_rank"] = rank
            row["rank"] = rank
            legacy_rank[row["preset_relative_path"]] = rank
            legacy_by_path[row["preset_relative_path"]] = row
        for row in perceptual:
            row["legacy_rank"] = legacy_rank[row["preset_relative_path"]]
            row["rank"] = row["perceptual_rank"]
            old = legacy_by_path[row["preset_relative_path"]]
            row.update(
                {
                    key: old[key]
                    for key in (
                        "total_loss",
                        "spectral_loss",
                        "envelope_loss",
                        "loudness_loss",
                        "candidate_rms_dbfs",
                        "target_rms_dbfs",
                    )
                }
            )
        if len(perceptual) < 10:
            raise SurgeMatchError("fewer than ten retrieval-eligible presets were indexed")

        def expose_audio(rows: Sequence[dict[str, Any]], prefix: str) -> None:
            for row in rows[:10]:
                rank = int(row["rank"])
                destination = self.audio_directory / "presets" / f"{prefix}-{rank:02d}.wav"
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(row["audio_path"], destination)
                if sha256_file(destination) != row["wav_sha256"]:
                    raise SurgeMatchError("copied retrieval WAV hash differs from shared cache")
                row["audio_url"] = f"/audio/presets/{destination.name}"

        expose_audio(perceptual, "perceptual")
        expose_audio(legacy, "legacy")
        self.perceptual_preset_ranking = perceptual
        self.legacy_preset_ranking = legacy
        self.preset_ranking = perceptual
        self.retrieval_family_medians = medians
        self.preset_failures = list(self.shared_cache.failures)
        eligible_base = next(
            (row for row in perceptual if row.get("search_eligible")), None
        )
        if eligible_base is None:
            raise SurgeMatchError("full library has no search-eligible CMA base")
        self.retrieval_sanity = {
            "self_distance_contract": "unit-tested exact zero before float16 cache quantization",
            "full_library_self_rank_audit": "PENDING_EXPLICIT_AUDIT",
            "near_state_top_10_audit": "PENDING_EXPLICIT_AUDIT",
            "synthetic_directionality": "AUTOMATED_TEST",
        }
        scores = [float(row["retrieval_score"]) for row in perceptual]
        median_score = float(np.median(scores))
        self.retrieval_coverage = {
            "factory_total": self.shared_cache.selected_count,
            "retrieval_eligible": len(self.shared_cache.entries),
            "search_eligible": sum(
                bool(row.get("search_eligible")) for row in self.shared_cache.entries
            ),
            "failed": len(self.shared_cache.failures),
            "best_to_median_ratio": (
                float(scores[0] / median_score) if median_score > 0 else None
            ),
            "top_10_spread": float(scores[9] - scores[0]),
        }
        self._initialize_selected_base(eligible_base)
        self._write_preset_index(
            entries=perceptual,
            selection=(
                "entire sorted retrieval-eligible Surge XT 1.3.4 factory library; "
                "CMA base is the highest-ranked search-eligible perceptual result"
            ),
            extra={
                "descriptor": descriptor_definition(),
                "family_medians": medians,
                "retrieval_coverage": self.retrieval_coverage,
                "retrieval_sanity": self.retrieval_sanity,
                "legacy_top_10": legacy[:10],
                "perceptual_top_10": perceptual[:10],
                "cma_base_perceptual_rank": eligible_base["perceptual_rank"],
            },
        )

    def retrieval_compare_public(self, *, family: str, rank: int) -> dict[str, Any]:
        if family not in {"legacy", "perceptual"}:
            raise SurgeMatchError("retrieval family must be legacy or perceptual")
        rows = (
            self.legacy_preset_ranking
            if family == "legacy"
            else self.perceptual_preset_ranking
        )
        if not 1 <= rank <= min(10, len(rows)):
            raise SurgeMatchError("retrieval comparison rank must be in the visible Top 10")
        row = rows[rank - 1]
        candidate_descriptor = load_descriptor(row["descriptor_path"])
        return {
            "family": family,
            "rank": rank,
            "candidate": {
                "preset_name": row["preset_name"],
                "preset_category": row["preset_category"],
                "preset_relative_path": row["preset_relative_path"],
                "audio_url": row["audio_url"],
                "raw_distances": row.get("raw_distances"),
                "normalized_distances": row.get("normalized_distances"),
                "weighted_contributions": row.get("weighted_contributions"),
                "retrieval_score": row.get("retrieval_score"),
                "legacy_loss": row["total_loss"],
                "retrieval_eligible": row["retrieval_eligible"],
                "search_eligible": row["search_eligible"],
            },
            "target": descriptor_preview(
                self.target_path, self.target_retrieval_descriptor
            ),
            "selected": descriptor_preview(row["audio_path"], candidate_descriptor),
            "descriptor": descriptor_definition(),
        }

    def reveal_target(self) -> dict[str, Any]:
        raise SurgeMatchError("external targets have no hidden Surge parameters")

    def public_status(self) -> dict[str, Any]:
        payload = super().public_status()
        payload.update(
            {
                "external_target_mode": True,
                "target_audio_url": (
                    f"/audio/target.wav?target={self.external_target.target_id}"
                    f"&sha={self.external_target.sha256[:12]}"
                ),
                "target_provenance": (
                    "External procedural reference · not generated by Surge XT"
                ),
                "can_reveal": False,
                "target_revealed": False,
                "success_gate_percent": None,
            }
        )
        payload.pop("target_parameters", None)
        return payload

    def private_evidence(self) -> dict[str, Any]:
        payload = super().private_evidence()
        payload.pop("hidden_target", None)
        payload.pop("hidden_target_preset_provenance", None)
        payload["external_target"] = dict(self.target)
        payload["predeclared_success"] = {
            "implementation": "target-specific retrieval and verified real-Surge rerender",
            "local_optimization": "best encountered loss must not exceed Base",
            "human_listening": "PENDING_OWNER_AUDITION",
        }
        payload["limitations"] = [
            "The objective is a transparent proxy, not human perceptual equivalence.",
            "Only C3 at one velocity and duration is optimized and validated.",
            "The target is an external procedural reference, not an instrument identity claim.",
            "Playing other notes is exploratory listening, not multi-note validation.",
        ]
        return payload

    def render_playable_note(self, **kwargs: Any) -> Path:
        if kwargs.get("source") == "target":
            raise SurgeMatchError("an external WAV is not a playable synth state")
        return super().render_playable_note(**kwargs)


class ExternalTargetController:
    """Own target selection while reusing one immutable real-preset render cache."""

    def __init__(
        self,
        *,
        adapter: SurgeXTAdapter,
        target_bank: ExternalTargetBank,
        output_directory: str | Path,
        preset_limit: int = DEFAULT_PRESET_LIMIT,
        generations: int = DEFAULT_GENERATIONS,
        population_size: int = DEFAULT_POPULATION,
        seed: int = SEARCH_SEED,
    ) -> None:
        self.adapter = adapter
        self.target_bank = target_bank
        self.output_directory = Path(output_directory).resolve()
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.preset_limit = preset_limit
        self.generations = generations
        self.population_size = population_size
        self.seed = seed
        self._lock = threading.RLock()
        self.human_audit_path = self.output_directory / "human-retrieval-audit.json"
        self._human_audit = self._load_human_audit()
        self.shared_cache = prepare_shared_preset_cache(
            adapter=adapter,
            cache_root=self.output_directory / "shared-preset-cache",
            preset_limit=preset_limit,
            full_library=True,
        )
        self._runs: list[ExternalSurgeMatchRun] = []
        self._latest_by_target: dict[str, dict[str, Any]] = {}
        self.active_run = self._new_run(self.target_bank.targets[0])

    def _load_human_audit(self) -> dict[str, dict[str, str]]:
        if not self.human_audit_path.is_file():
            return {}
        try:
            payload = json.loads(self.human_audit_path.read_text(encoding="utf-8"))
            return dict(payload.get("targets", {}))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise SurgeMatchError("human retrieval audit file is invalid")

    def set_human_audit(
        self, *, comparison: str, plausible_neighborhood: str
    ) -> dict[str, Any]:
        comparisons = {
            "NEW BETTER", "ROUGHLY SAME", "LEGACY BETTER", "NEITHER CLOSE", "PENDING"
        }
        plausible = {"YES", "NO", "UNCLEAR"}
        if comparison not in comparisons or plausible_neighborhood not in plausible:
            raise SurgeMatchError("human retrieval audit uses an unsupported status")
        with self._lock:
            target_id = self.active_run.external_target.target_id
            self._human_audit[target_id] = {
                "comparison": comparison,
                "plausible_neighborhood": plausible_neighborhood,
                "recorded_at": _now(),
            }
            _write_json_atomic(
                {
                    "schema": "agentic-synth-twin/human-retrieval-audit/v1",
                    "targets": self._human_audit,
                },
                self.human_audit_path,
            )
            return self.public_status()

    @property
    def status(self) -> str:
        return self.active_run.status

    def _new_run(self, target: ExternalTarget) -> ExternalSurgeMatchRun:
        run_directory = (
            self.output_directory
            / "targets"
            / target.target_id
            / f"run-{uuid.uuid4().hex[:12]}"
        )
        run = ExternalSurgeMatchRun(
            adapter=self.adapter,
            target=target,
            shared_cache=self.shared_cache,
            output_directory=run_directory,
            preset_limit=self.preset_limit,
            generations=self.generations,
            population_size=self.population_size,
            seed=self.seed,
        )
        self._runs.append(run)
        return run

    def targets_public(self) -> dict[str, Any]:
        with self._lock:
            records = []
            for target in self.target_bank.targets:
                record = target.public_record()
                summary = self._latest_by_target.get(target.target_id)
                if target.target_id == self.active_run.external_target.target_id:
                    summary = self.active_run.public_status()
                record["status"] = summary["status"] if summary else None
                record["best_loss"] = summary["best_loss"] if summary else None
                records.append(record)
            return {
                "schema": "agentic-synth-twin/external-target-bank/v1",
                "bank_identity_sha256": self.target_bank.identity_sha256,
                "selected_target_id": self.active_run.external_target.target_id,
                "targets": records,
            }

    def select_target(self, target_id: str) -> dict[str, Any]:
        with self._lock:
            if self.active_run.status in {"SEARCHING", "PAUSING", "PAUSED"}:
                raise SurgeMatchError("stop the active search before selecting another target")
            self._latest_by_target[
                self.active_run.external_target.target_id
            ] = self.active_run.public_status()
            target = self.target_bank.target(target_id)
            self.active_run = self._new_run(target)
            return self.public_status()

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            payload = self.active_run.public_status()
            payload["targets"] = self.target_bank.public_records()
            payload["selected_target_id"] = self.active_run.external_target.target_id
            payload["target_bank_identity_sha256"] = self.target_bank.identity_sha256
            payload["preset_cache"]["reused_for_target_switch"] = len(self._runs) > 1
            payload["human_retrieval_audit"] = getattr(self, "_human_audit", {}).get(
                self.active_run.external_target.target_id,
                {"comparison": "PENDING", "plausible_neighborhood": "UNCLEAR"},
            )
            return payload

    def history_public(self) -> list[dict[str, Any]]:
        with self._lock:
            return self.active_run.history_public()

    def spectra_public(self) -> dict[str, Any]:
        with self._lock:
            return self.active_run.spectra_public()

    def retrieval_compare_public(self, *, family: str, rank: int) -> dict[str, Any]:
        with self._lock:
            return self.active_run.retrieval_compare_public(family=family, rank=rank)

    def audio_path_for_url(self, url_path: str) -> Path:
        with self._lock:
            return self.active_run.audio_path_for_url(url_path)

    def render_playable_note(self, **kwargs: Any) -> Path:
        with self._lock:
            return self.active_run.render_playable_note(**kwargs)

    def start_search(self) -> dict[str, Any]:
        with self._lock:
            return self.active_run.start_search()

    def pause_search(self) -> dict[str, Any]:
        with self._lock:
            return self.active_run.pause_search()

    def resume_search(self) -> dict[str, Any]:
        with self._lock:
            return self.active_run.resume_search()

    def stop_search(self) -> dict[str, Any]:
        with self._lock:
            return self.active_run.stop_search()

    def reveal_target(self) -> dict[str, Any]:
        with self._lock:
            return self.active_run.reveal_target()

    def wait(self, timeout: float | None = None) -> None:
        with self._lock:
            run = self.active_run
        run.wait(timeout=timeout)


def create_surge_match_run(
    *, adapter: SurgeXTAdapter, output_directory: str | Path, **kwargs: Any
) -> SurgeMatchRun:
    return SurgeMatchRun(adapter=adapter, output_directory=output_directory, **kwargs)


def create_external_target_controller(
    *,
    adapter: SurgeXTAdapter,
    target_directory: str | Path,
    output_directory: str | Path,
    **kwargs: Any,
) -> ExternalTargetController:
    return ExternalTargetController(
        adapter=adapter,
        target_bank=ExternalTargetBank(target_directory),
        output_directory=output_directory,
        **kwargs,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Surge XT one-note preset retrieval and local match")
    parser.add_argument("command", choices=("serve", "run"))
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--renderer", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preset-limit", type=int, default=DEFAULT_PRESET_LIMIT)
    parser.add_argument("--generations", type=int, default=DEFAULT_GENERATIONS)
    parser.add_argument("--population", type=int, default=DEFAULT_POPULATION)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8879)
    parser.add_argument(
        "--targets",
        default=str(
            Path(__file__).resolve().parents[2] / "examples/targets/external-v1"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    adapter = SurgeXTAdapter(
        plugin_path=args.plugin,
        factory_data_path=args.data,
        renderer_path=args.renderer,
        probe_path=args.probe,
        deterministic_overrides=DETERMINISTIC_OVERRIDES,
    )
    if args.command == "run":
        run = create_surge_match_run(
            adapter=adapter,
            output_directory=args.output,
            preset_limit=args.preset_limit,
            generations=args.generations,
            population_size=args.population,
        )
        run.start_search()
        run.wait()
        print(json.dumps(run.public_status(), indent=2, ensure_ascii=False))
        return 0 if run.status == "COMPLETE" else 1
    run = create_external_target_controller(
        adapter=adapter,
        target_directory=args.targets,
        output_directory=args.output,
        preset_limit=args.preset_limit,
        generations=args.generations,
        population_size=args.population,
    )
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SurgeMatchError("Surge cockpit must bind to loopback")
    from .search_cockpit import create_cockpit_server

    server = create_cockpit_server(run, host=args.host, port=args.port)
    print(f"Surge XT external-target cockpit: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if run.status in {"SEARCHING", "PAUSING", "PAUSED"}:
            run.stop_search()
            run.wait(timeout=30)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

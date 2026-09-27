"""Deterministic, traceable synthetic dataset generation on the real CLAP synth."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .audition import (
    BITS_PER_SAMPLE,
    BLOCK_SIZE,
    CHANNELS,
    MIDI_KEY,
    NOTE_FRAMES,
    NOTE_NAME,
    SAMPLE_RATE,
    TAIL_FRAMES,
    TOTAL_FRAMES,
    VELOCITY,
    _write_json_atomic,
)
from .dsp import DSP_SCHEMA, analyze_wav
from .synth_state import load_canonical_state


DATASET_SCHEMA = "agentic-synth-twin/synthetic-dataset/v1"
DATASET_SEED = 20_260_927
DATASET_SAMPLE_COUNT = 256
FILTER_TYPE_ID = 14_255
CUTOFF_ID = 17
ATTACK_ID = 2_874
SELECTED_PARAMETER_IDS = (FILTER_TYPE_ID, CUTOFF_ID, ATTACK_ID)
CLAP_PARAM_IS_STEPPED = 1 << 0


class DatasetError(RuntimeError):
    """Raised when a synthetic example violates the dataset contract."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_parameter(state: Mapping[str, Any], parameter_id: int) -> Mapping[str, Any]:
    for parameter in state["parameters"]:
        if parameter["id"] == parameter_id:
            return parameter
    raise DatasetError(f"parameter id {parameter_id} is not in the real inventory")


def _rounded(value: float) -> float:
    return round(float(value), 12)


def plan_parameter_states(
    state: Mapping[str, Any],
    *,
    sample_count: int = DATASET_SAMPLE_COUNT,
    seed: int = DATASET_SEED,
) -> list[dict[str, Any]]:
    """Plan one baseline plus seeded, balanced/stratified parameter states."""

    if sample_count < 2:
        raise DatasetError("dataset requires the baseline and at least one sampled state")
    selected = {
        parameter_id: _find_parameter(state, parameter_id)
        for parameter_id in SELECTED_PARAMETER_IDS
    }
    filter_parameter = selected[FILTER_TYPE_ID]
    if not filter_parameter["flags"] & CLAP_PARAM_IS_STEPPED:
        raise DatasetError("Filter Type must remain a real stepped parameter")
    for parameter_id in (CUTOFF_ID, ATTACK_ID):
        if selected[parameter_id]["flags"] & CLAP_PARAM_IS_STEPPED:
            raise DatasetError("continuous dataset parameter unexpectedly became stepped")

    filter_min = int(filter_parameter["min"])
    filter_max = int(filter_parameter["max"])
    if filter_min != filter_parameter["min"] or filter_max != filter_parameter["max"]:
        raise DatasetError("Filter Type discovered range must have integer endpoints")
    filter_values = list(range(filter_min, filter_max + 1))
    baseline_filter = int(filter_parameter["current"])
    categories = [filter_values[index % len(filter_values)] for index in range(sample_count)]
    try:
        categories.remove(baseline_filter)
    except ValueError as error:
        raise DatasetError("canonical Filter Type is outside its discovered range") from error

    generator = np.random.Generator(np.random.PCG64(seed))
    generator.shuffle(categories)
    sampled_count = sample_count - 1
    continuous_values: dict[int, np.ndarray] = {}
    for parameter_id in (CUTOFF_ID, ATTACK_ID):
        parameter = selected[parameter_id]
        strata = generator.permutation(sampled_count)
        jitter = generator.random(sampled_count)
        unit = (strata + jitter) / sampled_count
        continuous_values[parameter_id] = parameter["min"] + unit * (
            parameter["max"] - parameter["min"]
        )

    plan = [
        {
            "sample_id": "sample-0000",
            "is_canonical_baseline": True,
            "parameter_values": {
                str(parameter_id): selected[parameter_id]["current"]
                for parameter_id in SELECTED_PARAMETER_IDS
            },
        }
    ]
    for index in range(sampled_count):
        plan.append(
            {
                "sample_id": f"sample-{index + 1:04d}",
                "is_canonical_baseline": False,
                "parameter_values": {
                    str(FILTER_TYPE_ID): categories[index],
                    str(CUTOFF_ID): _rounded(continuous_values[CUTOFF_ID][index]),
                    str(ATTACK_ID): _rounded(continuous_values[ATTACK_ID][index]),
                },
            }
        )
    return plan


def _run_renderer(
    *,
    renderer: Path,
    plugin: Path,
    state_path: Path,
    wav_path: Path,
    parameter_values: Mapping[str, int | float],
) -> dict[str, Any]:
    command = [str(renderer), str(plugin), str(state_path), str(wav_path)]
    for parameter_id in SELECTED_PARAMETER_IDS:
        command.extend([str(parameter_id), str(parameter_values[str(parameter_id)])])
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise DatasetError(
            f"renderer failed with exit {error.returncode}: {error.stderr.strip()}"
        ) from error
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise DatasetError("renderer stdout was not strict JSON") from error


def _validate_renderer_metadata(
    metadata: Mapping[str, Any], parameter_values: Mapping[str, int | float]
) -> None:
    expected = {
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "bits_per_sample": BITS_PER_SAMPLE,
        "midi_key": MIDI_KEY,
        "velocity": VELOCITY,
        "note_frames": NOTE_FRAMES,
        "tail_frames": TAIL_FRAMES,
        "total_frames": TOTAL_FRAMES,
        "block_size": BLOCK_SIZE,
        "clipped_samples": 0,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise DatasetError(
                f"renderer {key} mismatch: expected {value}, got {metadata.get(key)}"
            )
    changes = metadata.get("parameter_changes")
    if not isinstance(changes, list) or len(changes) != len(SELECTED_PARAMETER_IDS):
        raise DatasetError("renderer did not report every dataset parameter change")
    for change, parameter_id in zip(changes, SELECTED_PARAMETER_IDS, strict=True):
        requested = float(parameter_values[str(parameter_id)])
        if change.get("id") != parameter_id:
            raise DatasetError("renderer reported dataset parameters out of order")
        if not math.isclose(float(change.get("requested")), requested, abs_tol=1e-12):
            raise DatasetError("renderer reported the wrong requested parameter value")
        if not math.isclose(float(change.get("applied")), requested, abs_tol=1e-12):
            raise DatasetError("real synth did not retain a dataset parameter value")


def _render_verified_sample(
    *,
    renderer: Path,
    plugin: Path,
    state_path: Path,
    wav_path: Path,
    parameter_values: Mapping[str, int | float],
) -> tuple[dict[str, Any], dict[str, Any]]:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=wav_path.parent, prefix=".dataset-render."
    ) as directory:
        temporary = Path(directory)
        first_wav = temporary / "first.wav"
        second_wav = temporary / "second.wav"
        first = _run_renderer(
            renderer=renderer,
            plugin=plugin,
            state_path=state_path,
            wav_path=first_wav,
            parameter_values=parameter_values,
        )
        second = _run_renderer(
            renderer=renderer,
            plugin=plugin,
            state_path=state_path,
            wav_path=second_wav,
            parameter_values=parameter_values,
        )
        _validate_renderer_metadata(first, parameter_values)
        _validate_renderer_metadata(second, parameter_values)
        if first != second:
            raise DatasetError("two identical dataset renders returned different metadata")
        if first_wav.read_bytes() != second_wav.read_bytes():
            raise DatasetError("two identical dataset renders produced different WAV bytes")
        os.replace(first_wav, wav_path)
    return analyze_wav(wav_path), first


def _load_selection_evidence(
    calibration_results_path: Path, dsp_measurements_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    calibration = json.loads(calibration_results_path.read_text(encoding="utf-8"))
    if calibration.get("schema") != "agentic-synth-twin/calibration-results/v1":
        raise DatasetError("calibration result schema is not supported")
    if calibration["session"].get("status") != "completed":
        raise DatasetError("calibration must be complete before dataset generation")
    human_yes_ids = {
        probe["parameter_id"]
        for probe in calibration["probes"]
        if probe["meaningful_difference"]
    }
    if not set(SELECTED_PARAMETER_IDS).issubset(human_yes_ids):
        raise DatasetError("every dataset parameter must have a human YES calibration")

    dsp = json.loads(dsp_measurements_path.read_text(encoding="utf-8"))
    if dsp.get("schema") != DSP_SCHEMA:
        raise DatasetError("DSP measurement schema is not supported")
    if dsp["source"].get("calibration_results_sha256") != _sha256(
        calibration_results_path
    ):
        raise DatasetError("DSP evidence is not bound to the selected calibration")
    measured_ids = {item["parameter_id"] for item in dsp["measurements"]}
    if not set(SELECTED_PARAMETER_IDS).issubset(measured_ids):
        raise DatasetError("every dataset parameter must have prior DSP evidence")
    return calibration, dsp


def generate_dataset(
    *,
    canonical_state_path: str | Path,
    accepted_manifest_path: str | Path,
    accepted_wav_path: str | Path,
    calibration_results_path: str | Path,
    dsp_measurements_path: str | Path,
    plugin_path: str | Path,
    renderer_path: str | Path,
    audio_directory: str | Path,
    output_path: str | Path,
    sample_count: int = DATASET_SAMPLE_COUNT,
    seed: int = DATASET_SEED,
) -> dict[str, Any]:
    """Render, verify, measure, and record a bounded synthetic dataset."""

    canonical_path = Path(canonical_state_path)
    accepted_manifest_file = Path(accepted_manifest_path)
    accepted_wav = Path(accepted_wav_path)
    calibration_file = Path(calibration_results_path)
    dsp_file = Path(dsp_measurements_path)
    plugin = Path(plugin_path)
    renderer = Path(renderer_path)
    audio_root = Path(audio_directory)
    if not plugin.is_dir():
        raise DatasetError(f"plugin bundle not found: {plugin}")
    if not renderer.is_file() or not os.access(renderer, os.X_OK):
        raise DatasetError(f"renderer is not executable: {renderer}")

    state = load_canonical_state(canonical_path)
    calibration, dsp = _load_selection_evidence(calibration_file, dsp_file)
    accepted_manifest = json.loads(accepted_manifest_file.read_text(encoding="utf-8"))
    accepted_sha = _sha256(accepted_wav)
    if accepted_manifest["wav"].get("sha256") != accepted_sha:
        raise DatasetError("accepted WAV does not match its Milestone 3 manifest")
    plan = plan_parameter_states(state, sample_count=sample_count, seed=seed)
    selected_parameters = [
        dict(_find_parameter(state, parameter_id))
        for parameter_id in SELECTED_PARAMETER_IDS
    ]

    state_bytes = base64.b64decode(state["opaque_state"]["data"], validate=True)
    audio_root.mkdir(parents=True, exist_ok=True)
    samples = []
    state_path = audio_root / ".canonical-state.bin"
    state_path.write_bytes(state_bytes)
    try:
        for index, planned in enumerate(plan, start=1):
            wav_path = audio_root / f"{planned['sample_id']}.wav"
            wav, renderer_metadata = _render_verified_sample(
                renderer=renderer,
                plugin=plugin,
                state_path=state_path,
                wav_path=wav_path,
                parameter_values=planned["parameter_values"],
            )
            if planned["is_canonical_baseline"] and wav["sha256"] != accepted_sha:
                raise DatasetError("dataset baseline does not reproduce Milestone 3")
            samples.append(
                {
                    **planned,
                    "wav": wav,
                    "render_verification": {
                        "renders": 2,
                        "byte_identical": True,
                        "clipped_samples": renderer_metadata["clipped_samples"],
                        "peak_float": renderer_metadata["peak_float"],
                        "parameter_changes": renderer_metadata["parameter_changes"],
                    },
                }
            )
            print(f"rendered {index}/{sample_count}: {planned['sample_id']}", flush=True)
    finally:
        state_path.unlink(missing_ok=True)

    category_counts = {
        str(value): sum(
            1
            for sample in samples
            if sample["parameter_values"][str(FILTER_TYPE_ID)] == value
        )
        for value in range(
            int(_find_parameter(state, FILTER_TYPE_ID)["min"]),
            int(_find_parameter(state, FILTER_TYPE_ID)["max"]) + 1,
        )
    }
    evidence = {
        "schema": DATASET_SCHEMA,
        "source": {
            "plugin": state["plugin"],
            "canonical_state_filename": canonical_path.name,
            "canonical_state_sha256": _sha256(canonical_path),
            "opaque_state_sha256": state["opaque_state"]["sha256"],
            "accepted_manifest_filename": accepted_manifest_file.name,
            "accepted_manifest_sha256": _sha256(accepted_manifest_file),
            "accepted_wav_filename": accepted_wav.name,
            "accepted_wav_sha256": accepted_sha,
            "calibration_results_filename": calibration_file.name,
            "calibration_results_sha256": _sha256(calibration_file),
            "calibration_session_id": calibration["session"]["id"],
            "dsp_measurements_filename": dsp_file.name,
            "dsp_measurements_sha256": _sha256(dsp_file),
            "dsp_schema": dsp["schema"],
        },
        "audition": {
            "note_name": NOTE_NAME,
            "midi_key": MIDI_KEY,
            "velocity": VELOCITY,
            "note_duration_seconds": NOTE_FRAMES / SAMPLE_RATE,
            "release_tail_seconds": TAIL_FRAMES / SAMPLE_RATE,
            "sample_rate": SAMPLE_RATE,
            "block_size": BLOCK_SIZE,
        },
        "sampling": {
            "seed": seed,
            "numpy_version": np.__version__,
            "method": "canonical baseline plus seeded stratified continuous samples and balanced stepped categories",
            "sample_count": sample_count,
            "selected_parameter_ids": list(SELECTED_PARAMETER_IDS),
            "selected_parameters": selected_parameters,
            "filter_type_counts": category_counts,
            "human_selection_boundary": (
                "three user-authorized parameters selected from the five Milestone 4 YES results"
            ),
        },
        "storage": {
            "committed": "manifest, exact parameter values, WAV hashes, render checks, and DSP features",
            "local_only": "WAV files under ignored work/dataset/audio",
        },
        "summary": {
            "sample_count": len(samples),
            "canonical_baseline_count": 1,
            "clipped_sample_count": sum(
                1
                for sample in samples
                if sample["render_verification"]["clipped_samples"] != 0
            ),
            "unique_wav_sha256_count": len(
                {sample["wav"]["sha256"] for sample in samples}
            ),
        },
        "samples": samples,
    }
    _write_json_atomic(evidence, Path(output_path))
    return evidence


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the bounded real-synth synthetic dataset"
    )
    parser.add_argument("--state", required=True)
    parser.add_argument("--accepted-manifest", required=True)
    parser.add_argument("--accepted-wav", required=True)
    parser.add_argument("--calibration-results", required=True)
    parser.add_argument("--dsp-measurements", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--renderer", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=DATASET_SAMPLE_COUNT)
    parser.add_argument("--seed", type=int, default=DATASET_SEED)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    evidence = generate_dataset(
        canonical_state_path=args.state,
        accepted_manifest_path=args.accepted_manifest,
        accepted_wav_path=args.accepted_wav,
        calibration_results_path=args.calibration_results,
        dsp_measurements_path=args.dsp_measurements,
        plugin_path=args.plugin,
        renderer_path=args.renderer,
        audio_directory=args.audio,
        output_path=args.output,
        sample_count=args.count,
        seed=args.seed,
    )
    print(
        f"wrote {evidence['summary']['sample_count']} dataset rows: {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

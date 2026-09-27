"""Terminal-only human A/B calibration for one real CLAP parameter."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    render_verified_wav,
)
from .synth_state import load_canonical_state


PROBE_SCHEMA = "agentic-synth-twin/human-ab-probe/v1"


class ProbeError(RuntimeError):
    """Raised when an A/B probe cannot be generated or recorded safely."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_parameter(state: Mapping[str, Any], parameter_id: int) -> Mapping[str, Any]:
    for parameter in state["parameters"]:
        if parameter["id"] == parameter_id:
            return parameter
    raise ProbeError(f"parameter id {parameter_id} is not in the real inventory")


def _validate_renderer_metadata(
    metadata: Mapping[str, Any],
    *,
    parameter_id: int | None,
    parameter_value: float | None,
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
            raise ProbeError(
                f"renderer {key} mismatch: expected {value}, got {metadata.get(key)}"
            )
    change = metadata.get("parameter_change")
    if parameter_id is None:
        if change is not None:
            raise ProbeError("baseline render unexpectedly changed a parameter")
        return
    if not isinstance(change, Mapping):
        raise ProbeError("variant render did not report its parameter change")
    if change.get("id") != parameter_id:
        raise ProbeError("variant renderer reported the wrong parameter id")
    if not math.isclose(float(change.get("requested")), float(parameter_value)):
        raise ProbeError("variant renderer reported the wrong requested value")
    if not math.isclose(float(change.get("applied")), float(parameter_value)):
        raise ProbeError("real synth did not retain the requested parameter value")


def generate_probe(
    *,
    canonical_state_path: str | Path,
    accepted_manifest_path: str | Path,
    accepted_wav_path: str | Path,
    plugin_path: str | Path,
    renderer_path: str | Path,
    output_directory: str | Path,
    parameter_id: int,
    parameter_value: float,
) -> dict[str, Any]:
    """Generate deterministic A/B WAVs from canonical state and one mutation."""

    canonical_path = Path(canonical_state_path)
    accepted_manifest_file = Path(accepted_manifest_path)
    accepted_wav = Path(accepted_wav_path)
    plugin = Path(plugin_path)
    renderer = Path(renderer_path)
    output = Path(output_directory)

    state = load_canonical_state(canonical_path)
    parameter = _find_parameter(state, parameter_id)
    if not parameter["automatable"]:
        raise ProbeError(f"parameter id {parameter_id} is not automatable")
    minimum = float(parameter["min"])
    maximum = float(parameter["max"])
    current = float(parameter["current"])
    if not math.isfinite(parameter_value) or not minimum <= parameter_value <= maximum:
        raise ProbeError(
            f"parameter value {parameter_value} is outside discovered range "
            f"[{minimum}, {maximum}]"
        )
    if math.isclose(parameter_value, current):
        raise ProbeError("variant value must differ from the canonical baseline")
    if not plugin.is_dir():
        raise ProbeError(f"plugin bundle not found: {plugin}")
    if not renderer.is_file() or not os.access(renderer, os.X_OK):
        raise ProbeError(f"renderer is not executable: {renderer}")

    accepted_manifest = json.loads(accepted_manifest_file.read_text(encoding="utf-8"))
    expected_accepted_sha = accepted_manifest["wav"]["sha256"]
    actual_accepted_sha = _sha256(accepted_wav)
    if actual_accepted_sha != expected_accepted_sha:
        raise ProbeError("accepted baseline WAV does not match its Milestone 3 manifest")

    opaque = state["opaque_state"]
    state_bytes = base64.b64decode(opaque["data"], validate=True)
    output.mkdir(parents=True, exist_ok=True)
    baseline_wav = output / "A-baseline.wav"
    variant_wav = output / "B-variant.wav"
    manifest_path = output / "ab-probe.json"

    baseline_evidence, baseline_renderer = render_verified_wav(
        renderer=renderer,
        plugin=plugin,
        state_bytes=state_bytes,
        wav_path=baseline_wav,
    )
    variant_evidence, variant_renderer = render_verified_wav(
        renderer=renderer,
        plugin=plugin,
        state_bytes=state_bytes,
        wav_path=variant_wav,
        parameter_id=parameter_id,
        parameter_value=parameter_value,
    )
    _validate_renderer_metadata(
        baseline_renderer, parameter_id=None, parameter_value=None
    )
    _validate_renderer_metadata(
        variant_renderer,
        parameter_id=parameter_id,
        parameter_value=parameter_value,
    )
    for evidence, metadata in (
        (baseline_evidence, baseline_renderer),
        (variant_evidence, variant_renderer),
    ):
        evidence["peak_float"] = metadata["peak_float"]
        evidence["clipped_samples"] = metadata["clipped_samples"]
    if baseline_evidence["sha256"] != expected_accepted_sha:
        raise ProbeError("new baseline render does not reproduce accepted Milestone 3 WAV")
    if variant_evidence["sha256"] == baseline_evidence["sha256"]:
        raise ProbeError("parameter mutation produced a byte-identical WAV")

    manifest = {
        "schema": PROBE_SCHEMA,
        "status": "awaiting_human_judgment",
        "plugin": {
            "clap_id": state["plugin"]["clap_id"],
            "name": state["plugin"]["name"],
            "version": state["plugin"]["version"],
            "source_commit": state["plugin"]["source_commit"],
        },
        "canonical_state": {
            "filename": canonical_path.name,
            "json_sha256": _sha256(canonical_path),
            "opaque_state_sha256": opaque["sha256"],
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
        "question": "Did B create a meaningful audible difference from A?",
        "parameter": {
            "id": parameter["id"],
            "name": parameter["name"],
            "module": parameter["module"],
            "minimum": parameter["min"],
            "maximum": parameter["max"],
            "baseline_value": parameter["current"],
            "variant_value": parameter_value,
            "variant_applied": variant_renderer["parameter_change"]["applied"],
        },
        "A": {
            "role": "accepted_canonical_baseline",
            "wav": baseline_evidence,
            "matches_milestone_3": True,
        },
        "B": {
            "role": "one_parameter_variant",
            "wav": variant_evidence,
        },
        "determinism": {
            "renders_per_side": 2,
            "A_byte_identical": True,
            "B_byte_identical": True,
        },
        "human_judgment": None,
    }
    _write_json_atomic(manifest, manifest_path)
    return manifest


def record_judgment(
    *, manifest_path: str | Path, meaningful_difference: bool, notes: str | None = None
) -> dict[str, Any]:
    """Record the human answer without converting it into automated evidence."""

    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != PROBE_SCHEMA:
        raise ProbeError("probe manifest schema is not supported")
    if manifest.get("status") != "awaiting_human_judgment":
        raise ProbeError("probe manifest is not awaiting a human judgment")
    if manifest.get("human_judgment") is not None:
        raise ProbeError("probe already contains a human judgment")
    cleaned_notes = notes.strip() if notes else None
    manifest["status"] = "completed"
    manifest["human_judgment"] = {
        "meaningful_difference": meaningful_difference,
        "notes": cleaned_notes or None,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "authority": "human_listener",
    }
    _write_json_atomic(manifest, path)
    return manifest


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or judge a terminal A/B probe"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    generate.add_argument("--state", required=True)
    generate.add_argument("--accepted-manifest", required=True)
    generate.add_argument("--accepted-wav", required=True)
    generate.add_argument("--plugin", required=True)
    generate.add_argument("--renderer", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument("--parameter-id", required=True, type=int)
    generate.add_argument("--parameter-value", required=True, type=float)

    record = subparsers.add_parser("record")
    record.add_argument("--manifest", required=True)
    record.add_argument("--answer", required=True, choices=("yes", "no"))
    record.add_argument("--notes")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "generate":
        manifest = generate_probe(
            canonical_state_path=args.state,
            accepted_manifest_path=args.accepted_manifest,
            accepted_wav_path=args.accepted_wav,
            plugin_path=args.plugin,
            renderer_path=args.renderer,
            output_directory=args.output,
            parameter_id=args.parameter_id,
            parameter_value=args.parameter_value,
        )
        print(
            f"generated A/B probe for {manifest['parameter']['name']}: "
            f"{manifest['parameter']['baseline_value']} -> "
            f"{manifest['parameter']['variant_value']}"
        )
        return 0
    manifest = record_judgment(
        manifest_path=args.manifest,
        meaningful_difference=args.answer == "yes",
        notes=args.notes,
    )
    print(
        "recorded human judgment: "
        f"meaningful_difference={manifest['human_judgment']['meaningful_difference']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

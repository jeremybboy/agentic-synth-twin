"""Deterministic, bounded DSP descriptors for verified audition WAV files."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import wave
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .ab_probe import generate_probe
from .audition import NOTE_FRAMES, _write_json_atomic


DSP_SCHEMA = "agentic-synth-twin/dsp-measurements/v1"
FFT_SIZE = 2048
HOP_SIZE = 512
ROLLOFF_FRACTION = 0.85
ENVELOPE_WINDOW_SECONDS = 0.010
RELEASE_REFERENCE_SECONDS = 0.050


class DSPError(RuntimeError):
    """Raised when audio cannot support the declared descriptor contract."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _round(value: float | np.floating[Any], digits: int = 9) -> float:
    return round(float(value), digits)


def _dbfs(amplitude: float) -> float:
    if amplitude <= 0:
        raise DSPError("dBFS is undefined for silence")
    return 20.0 * math.log10(amplitude)


def _moving_rms(frame_power: np.ndarray, window_frames: int) -> np.ndarray:
    if window_frames < 1:
        raise DSPError("envelope window must contain at least one frame")
    left = window_frames // 2
    right = window_frames - 1 - left
    padded = np.pad(frame_power, (left, right), mode="edge")
    cumulative = np.concatenate(([0.0], np.cumsum(padded, dtype=np.float64)))
    mean_power = (cumulative[window_frames:] - cumulative[:-window_frames]) / window_frames
    return np.sqrt(np.maximum(mean_power, 0.0))


def _first_at_or_above(values: np.ndarray, threshold: float) -> int | None:
    indices = np.flatnonzero(values >= threshold)
    return int(indices[0]) if indices.size else None


def _first_at_or_below(values: np.ndarray, threshold: float) -> int | None:
    indices = np.flatnonzero(values <= threshold)
    return int(indices[0]) if indices.size else None


def _spectral_descriptors(samples: np.ndarray, sample_rate: int) -> tuple[float, float]:
    frame_total = samples.shape[0]
    frame_count = max(
        1,
        math.ceil(max(0, frame_total - FFT_SIZE) / HOP_SIZE) + 1,
    )
    padded_frames = (frame_count - 1) * HOP_SIZE + FFT_SIZE
    padded = np.pad(samples, ((0, padded_frames - frame_total), (0, 0)))
    window = np.hanning(FFT_SIZE)
    accumulated_power = np.zeros(FFT_SIZE // 2 + 1, dtype=np.float64)
    for index in range(frame_count):
        start = index * HOP_SIZE
        frame = padded[start : start + FFT_SIZE] * window[:, np.newaxis]
        spectrum = np.fft.rfft(frame, axis=0)
        accumulated_power += np.sum(np.square(np.abs(spectrum)), axis=1)
    total_power = float(np.sum(accumulated_power))
    if total_power <= 0:
        raise DSPError("spectral descriptors are undefined for silence")
    frequencies = np.fft.rfftfreq(FFT_SIZE, d=1.0 / sample_rate)
    centroid = float(np.sum(frequencies * accumulated_power) / total_power)
    cumulative = np.cumsum(accumulated_power)
    rolloff_index = int(
        min(
            np.searchsorted(cumulative, ROLLOFF_FRACTION * total_power, side="left"),
            frequencies.size - 1,
        )
    )
    return centroid, float(frequencies[rolloff_index])


def _envelope_descriptors(
    samples: np.ndarray, sample_rate: int, note_off_frame: int
) -> dict[str, Any]:
    window_frames = max(1, round(ENVELOPE_WINDOW_SECONDS * sample_rate))
    frame_power = np.mean(np.square(samples), axis=1)
    envelope = _moving_rms(frame_power, window_frames)
    note_end = min(note_off_frame, envelope.size)
    if note_end <= 0:
        raise DSPError("note-off frame must follow note onset")
    note_envelope = envelope[:note_end]
    peak = float(np.max(note_envelope))
    if peak <= 0:
        raise DSPError("envelope descriptors are undefined for silence")
    attack_10 = _first_at_or_above(note_envelope, peak * 0.10)
    attack_90 = _first_at_or_above(note_envelope, peak * 0.90)
    if attack_10 is None or attack_90 is None or attack_90 < attack_10:
        raise DSPError("could not measure attack thresholds")
    attack_seconds = (attack_90 - attack_10) / sample_rate

    release_seconds: float | None = None
    release_censored = True
    if note_off_frame < envelope.size:
        reference_frames = max(1, round(RELEASE_REFERENCE_SECONDS * sample_rate))
        reference_start = max(0, note_off_frame - reference_frames)
        reference = float(np.median(envelope[reference_start:note_off_frame]))
        post = envelope[note_off_frame:]
        if reference > 0 and post.size:
            release_90 = _first_at_or_below(post, reference * 0.90)
            if release_90 is not None:
                release_10_relative = _first_at_or_below(
                    post[release_90:], reference * 0.10
                )
                if release_10_relative is not None:
                    release_seconds = release_10_relative / sample_rate
                    release_censored = False
    return {
        "attack_10_to_90_seconds": _round(attack_seconds),
        "release_90_to_10_seconds": (
            None if release_seconds is None else _round(release_seconds)
        ),
        "release_censored_by_tail": release_censored,
    }


def analyze_wav(
    wav_path: str | Path, *, note_off_frame: int = NOTE_FRAMES
) -> dict[str, Any]:
    """Measure level, spectrum, and envelope from one PCM16 WAV."""

    path = Path(wav_path)
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frame_count = source.getnframes()
        compression = source.getcomptype()
        pcm = source.readframes(frame_count)
    if channels not in (1, 2):
        raise DSPError(f"expected mono or stereo WAV, got {channels} channels")
    if sample_width != 2 or compression != "NONE":
        raise DSPError("expected uncompressed 16-bit PCM WAV")
    interleaved = np.frombuffer(pcm, dtype="<i2")
    if interleaved.size != frame_count * channels:
        raise DSPError("WAV sample count does not match its header")
    samples = interleaved.reshape(frame_count, channels).astype(np.float64) / 32768.0
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(np.square(samples))))
    if peak <= 0 or rms <= 0:
        raise DSPError("cannot analyze a silent WAV")
    centroid, rolloff = _spectral_descriptors(samples, sample_rate)
    features = {
        "rms_dbfs": _round(_dbfs(rms)),
        "peak_dbfs": _round(_dbfs(peak)),
        "spectral_centroid_hz": _round(centroid),
        "spectral_rolloff_85_hz": _round(rolloff),
        **_envelope_descriptors(samples, sample_rate, note_off_frame),
    }
    return {
        "filename": path.name,
        "sha256": _sha256(path),
        "sample_rate": sample_rate,
        "channels": channels,
        "frames": frame_count,
        "duration_seconds": _round(frame_count / sample_rate),
        "features": features,
    }


def _feature_deltas(
    baseline: Mapping[str, Any], variant: Mapping[str, Any]
) -> dict[str, float | None]:
    deltas: dict[str, float | None] = {}
    for key in (
        "rms_dbfs",
        "peak_dbfs",
        "spectral_centroid_hz",
        "spectral_rolloff_85_hz",
        "attack_10_to_90_seconds",
        "release_90_to_10_seconds",
    ):
        left = baseline[key]
        right = variant[key]
        deltas[f"{key}_B_minus_A"] = (
            None if left is None or right is None else _round(float(right) - float(left))
        )
    return deltas


def reproduce_and_measure(
    *,
    results_path: str | Path,
    canonical_state_path: str | Path,
    accepted_manifest_path: str | Path,
    accepted_wav_path: str | Path,
    plugin_path: str | Path,
    renderer_path: str | Path,
    work_directory: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Reproduce every completed probe and measure its A/B WAVs."""

    results_file = Path(results_path)
    results = json.loads(results_file.read_text(encoding="utf-8"))
    if results.get("schema") != "agentic-synth-twin/calibration-results/v1":
        raise DSPError("calibration result schema is not supported")
    if results["session"].get("status") != "completed":
        raise DSPError("calibration session must be completed before measurement")
    output_directory = Path(work_directory)
    measurements = []
    render_contract: dict[str, Any] | None = None
    for probe in results["probes"]:
        ordinal = probe["ordinal"]
        parameter_id = probe["parameter_id"]
        probe_directory = output_directory / f"{ordinal:02d}-parameter-{parameter_id}"
        manifest = generate_probe(
            canonical_state_path=canonical_state_path,
            accepted_manifest_path=accepted_manifest_path,
            accepted_wav_path=accepted_wav_path,
            plugin_path=plugin_path,
            renderer_path=renderer_path,
            output_directory=probe_directory,
            parameter_id=parameter_id,
            parameter_value=probe["variant_value"],
        )
        parameter = manifest["parameter"]
        if parameter["name"] != probe["parameter_name"]:
            raise DSPError("probe parameter name does not match completed calibration")
        if parameter["baseline_value"] != probe["baseline_value"]:
            raise DSPError("probe baseline does not match completed calibration")
        current_contract = {
            "plugin": manifest["plugin"],
            "canonical_state": manifest["canonical_state"],
            "audition": manifest["audition"],
        }
        if render_contract is None:
            render_contract = current_contract
        elif current_contract != render_contract:
            raise DSPError("probe render contract changed within the measurement run")
        baseline = analyze_wav(probe_directory / "A-baseline.wav")
        variant = analyze_wav(probe_directory / "B-variant.wav")
        if baseline["sha256"] != manifest["A"]["wav"]["sha256"]:
            raise DSPError("baseline WAV hash changed between render and measurement")
        if variant["sha256"] != manifest["B"]["wav"]["sha256"]:
            raise DSPError("variant WAV hash changed between render and measurement")
        measurements.append(
            {
                "ordinal": ordinal,
                "parameter_id": parameter_id,
                "parameter_name": parameter["name"],
                "baseline_value": parameter["baseline_value"],
                "variant_value": parameter["variant_value"],
                "variant_applied": parameter["variant_applied"],
                "human_meaningful_difference": bool(probe["meaningful_difference"]),
                "render_verification": {
                    **manifest["determinism"],
                    "A_matches_milestone_3": manifest["A"][
                        "matches_milestone_3"
                    ],
                    "A_clipped_samples": manifest["A"]["wav"]["clipped_samples"],
                    "B_clipped_samples": manifest["B"]["wav"]["clipped_samples"],
                },
                "A": baseline,
                "B": variant,
                "deltas": _feature_deltas(
                    baseline["features"], variant["features"]
                ),
            }
        )

    if render_contract is None:
        raise DSPError("completed calibration contains no probes")
    yes_parameters = [
        measurement["parameter_name"]
        for measurement in measurements
        if measurement["human_meaningful_difference"]
    ]
    evidence = {
        "schema": DSP_SCHEMA,
        "source": {
            "calibration_results_filename": results_file.name,
            "calibration_results_sha256": _sha256(results_file),
            "session_id": results["session"]["id"],
            "accepted_manifest_filename": Path(accepted_manifest_path).name,
            "accepted_manifest_sha256": _sha256(Path(accepted_manifest_path)),
            "accepted_wav_filename": Path(accepted_wav_path).name,
            "accepted_wav_sha256": _sha256(Path(accepted_wav_path)),
            **render_contract,
        },
        "analyzer": {
            "python_version": platform.python_version(),
            "numpy_version": np.__version__,
            "channel_aggregation": (
                "mean-square across PCM channels for level/envelope; "
                "power spectra summed across channels"
            ),
            "level": "whole-file RMS and absolute peak in dBFS",
            "spectrum": "summed Hann-windowed power spectra",
            "fft_size": FFT_SIZE,
            "hop_size": HOP_SIZE,
            "rolloff_fraction": ROLLOFF_FRACTION,
            "envelope": "10 ms moving RMS",
            "attack": "first 10% to first 90% of note-region envelope peak",
            "release": "90% to 10% of 50 ms pre-note-off median; null when tail-censored",
        },
        "summary": {
            "measurement_count": len(measurements),
            "human_yes_count": len(yes_parameters),
            "human_yes_parameters": yes_parameters,
        },
        "measurements": measurements,
    }
    _write_json_atomic(evidence, Path(output_path))
    return evidence


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reproduce completed A/B probes and extract bounded DSP features"
    )
    parser.add_argument("--results", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--accepted-manifest", required=True)
    parser.add_argument("--accepted-wav", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--renderer", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    evidence = reproduce_and_measure(
        results_path=args.results,
        canonical_state_path=args.state,
        accepted_manifest_path=args.accepted_manifest,
        accepted_wav_path=args.accepted_wav,
        plugin_path=args.plugin,
        renderer_path=args.renderer,
        work_directory=args.work,
        output_path=args.output,
    )
    print(
        f"wrote {evidence['summary']['measurement_count']} DSP comparisons: "
        f"{args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic real-synth audition rendering and evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Mapping, Sequence

from .synth_state import load_canonical_state


AUDITION_SCHEMA = "agentic-synth-twin/deterministic-audition/v1"
SAMPLE_RATE = 44_100
CHANNELS = 2
BITS_PER_SAMPLE = 16
MIDI_KEY = 48
NOTE_NAME = "C3"
VELOCITY = 100
NOTE_FRAMES = SAMPLE_RATE * 2
TAIL_FRAMES = SAMPLE_RATE // 2
TOTAL_FRAMES = NOTE_FRAMES + TAIL_FRAMES
BLOCK_SIZE = 64


class AuditionError(RuntimeError):
    """Raised when a deterministic render cannot be proven."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json_atomic(value: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary_path = temporary.name
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            Path(temporary_path).unlink(missing_ok=True)


def _run_renderer(
    renderer: Path, plugin: Path, state_bytes: bytes, wav_path: Path
) -> dict[str, Any]:
    state_path = wav_path.with_suffix(".state.bin")
    state_path.write_bytes(state_bytes)
    try:
        completed = subprocess.run(
            [str(renderer), str(plugin), str(state_path), str(wav_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise AuditionError(
            f"renderer failed with exit {error.returncode}: {error.stderr.strip()}"
        ) from error
    finally:
        state_path.unlink(missing_ok=True)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AuditionError("renderer stdout was not strict JSON") from error


def _inspect_wav(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.getnframes()
        pcm = wav.readframes(frames)
    expected = (CHANNELS, BITS_PER_SAMPLE // 8, SAMPLE_RATE, TOTAL_FRAMES)
    actual = (channels, sample_width, sample_rate, frames)
    if actual != expected:
        raise AuditionError(f"unexpected WAV structure: expected {expected}, got {actual}")
    if not any(pcm):
        raise AuditionError("rendered WAV contains only silence")
    return {
        "filename": path.name,
        "sha256": _sha256_file(path),
        "pcm_sha256": _sha256_bytes(pcm),
        "byte_size": path.stat().st_size,
        "channels": channels,
        "bits_per_sample": sample_width * 8,
        "sample_rate": sample_rate,
        "frames": frames,
        "duration_seconds": frames / sample_rate,
        "non_silent": True,
    }


def render_audition(
    *,
    canonical_state_path: str | Path,
    plugin_path: str | Path,
    renderer_path: str | Path,
    wav_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Render twice from exact state, require byte identity, and write evidence."""

    canonical_path = Path(canonical_state_path)
    plugin = Path(plugin_path)
    renderer = Path(renderer_path)
    output_wav = Path(wav_path)
    output_manifest = Path(manifest_path)
    state = load_canonical_state(canonical_path)
    if not plugin.is_dir():
        raise AuditionError(f"plugin bundle not found: {plugin}")
    if not renderer.is_file() or not os.access(renderer, os.X_OK):
        raise AuditionError(f"renderer is not executable: {renderer}")

    opaque = state["opaque_state"]
    state_bytes = base64.b64decode(opaque["data"], validate=True)
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output_wav.parent, prefix=".audition-render."
    ) as directory:
        temporary = Path(directory)
        first_wav = temporary / "first.wav"
        second_wav = temporary / "second.wav"
        first_result = _run_renderer(renderer, plugin, state_bytes, first_wav)
        second_result = _run_renderer(renderer, plugin, state_bytes, second_wav)
        first_bytes = first_wav.read_bytes()
        second_bytes = second_wav.read_bytes()
        if first_bytes != second_bytes:
            raise AuditionError("two identical renders produced different WAV bytes")
        if first_result != second_result:
            raise AuditionError("two identical renders produced different renderer metadata")
        os.replace(first_wav, output_wav)

    wav_evidence = _inspect_wav(output_wav)
    expected_renderer_values = {
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "bits_per_sample": BITS_PER_SAMPLE,
        "midi_key": MIDI_KEY,
        "velocity": VELOCITY,
        "note_frames": NOTE_FRAMES,
        "tail_frames": TAIL_FRAMES,
        "total_frames": TOTAL_FRAMES,
        "block_size": BLOCK_SIZE,
    }
    for key, expected in expected_renderer_values.items():
        if first_result.get(key) != expected:
            raise AuditionError(
                f"renderer {key} mismatch: expected {expected}, got {first_result.get(key)}"
            )
    if first_result.get("clipped_samples") != 0:
        raise AuditionError("render contains samples outside the PCM range")
    wav_evidence["peak_float"] = first_result["peak_float"]
    wav_evidence["clipped_samples"] = first_result["clipped_samples"]

    manifest = {
        "schema": AUDITION_SCHEMA,
        "plugin": {
            "clap_id": state["plugin"]["clap_id"],
            "name": state["plugin"]["name"],
            "version": state["plugin"]["version"],
            "source_commit": state["plugin"]["source_commit"],
        },
        "canonical_state": {
            "filename": canonical_path.name,
            "json_sha256": _sha256_file(canonical_path),
            "opaque_state_sha256": opaque["sha256"],
        },
        "audition": {
            "note_name": NOTE_NAME,
            "midi_key": MIDI_KEY,
            "velocity": VELOCITY,
            "velocity_normalized": VELOCITY / 127,
            "note_duration_seconds": NOTE_FRAMES / SAMPLE_RATE,
            "release_tail_seconds": TAIL_FRAMES / SAMPLE_RATE,
            "sample_rate": SAMPLE_RATE,
            "block_size": BLOCK_SIZE,
        },
        "wav": wav_evidence,
        "determinism": {
            "verification_runs": 2,
            "byte_identical": True,
        },
        "known_limitations": {
            "velocity_affects_sound": False,
            "velocity_note": (
                "The pinned clap-saw-demo note handler ignores event velocity; "
                "velocity 100 is recorded but is not sonically effective."
            ),
        },
    }
    _write_json_atomic(manifest, output_manifest)
    return manifest


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render deterministic C3 audition")
    parser.add_argument("--state", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--renderer", required=True)
    parser.add_argument("--wav", required=True)
    parser.add_argument("--manifest", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest = render_audition(
        canonical_state_path=args.state,
        plugin_path=args.plugin,
        renderer_path=args.renderer,
        wav_path=args.wav,
        manifest_path=args.manifest,
    )
    print(
        f"wrote deterministic audition: {args.wav} "
        f"({manifest['wav']['sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

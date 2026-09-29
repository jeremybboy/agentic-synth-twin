"""Reusable local CLAP synth boundary for exact state and rendered-note work."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audition import (
    BITS_PER_SAMPLE,
    BLOCK_SIZE,
    CHANNELS,
    SAMPLE_RATE,
    TAIL_FRAMES,
)


class SynthAdapterError(RuntimeError):
    """Raised when a synth adapter cannot prove an exact requested operation."""


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class PresetRef:
    name: str
    category: str
    path: Path
    relative_path: str


class ClapSynthAdapter:
    """Small process-isolated adapter around the repository CLAP tools."""

    def __init__(
        self,
        *,
        plugin_path: str | Path,
        renderer_path: str | Path,
        probe_path: str | Path,
        data_home: str | Path | None = None,
        deterministic_overrides: Mapping[int, float] | None = None,
    ) -> None:
        self.plugin_path = Path(plugin_path).resolve()
        self.renderer_path = Path(renderer_path).resolve()
        self.probe_path = Path(probe_path).resolve()
        self.data_home = Path(data_home).resolve() if data_home else None
        self.deterministic_overrides = {
            int(key): float(value)
            for key, value in (deterministic_overrides or {}).items()
        }
        if not self.plugin_path.is_dir():
            raise SynthAdapterError(f"CLAP bundle not found: {self.plugin_path}")
        for executable in (self.renderer_path, self.probe_path):
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise SynthAdapterError(f"required helper is not executable: {executable}")
        if self.data_home is not None and not self.data_home.is_dir():
            raise SynthAdapterError(f"synth data directory not found: {self.data_home}")

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self.data_home is not None:
            environment["SURGE_DATA_HOME"] = str(self.data_home)
        return environment

    def inspect_state(self, state_path: str | Path) -> dict[str, Any]:
        state = Path(state_path).resolve()
        if not state.is_file() or state.stat().st_size == 0:
            raise SynthAdapterError(f"state file is absent or empty: {state}")
        command = [
            str(self.probe_path),
            str(self.plugin_path),
            "--state",
            str(state),
            "--inspect-only",
        ]
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=self._environment(),
            )
        except subprocess.CalledProcessError as error:
            raise SynthAdapterError(
                f"CLAP state inspection failed ({error.returncode}): {error.stderr.strip()}"
            ) from error
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise SynthAdapterError("CLAP probe stdout was not strict JSON") from error
        if not result.get("plugin", {}).get("id") or not result.get("parameters"):
            raise SynthAdapterError("CLAP probe returned incomplete plugin inventory")
        return result

    def render_note(
        self,
        *,
        state_path: str | Path,
        wav_path: str | Path,
        parameter_values: Mapping[int | str, float],
        midi_key: int,
        velocity: int,
        note_frames: int,
        allow_clipping: bool = False,
        allow_parameter_coercion: bool = False,
    ) -> dict[str, Any]:
        if not 0 <= midi_key <= 127:
            raise SynthAdapterError("MIDI key must be in 0..127")
        if not 1 <= velocity <= 127:
            raise SynthAdapterError("velocity must be in 1..127")
        if not SAMPLE_RATE // 20 <= note_frames <= SAMPLE_RATE * 8:
            raise SynthAdapterError("note length is outside the renderer contract")
        values = {int(key): float(value) for key, value in parameter_values.items()}
        for parameter_id, value in self.deterministic_overrides.items():
            values.setdefault(parameter_id, value)
        if any(not math.isfinite(value) for value in values.values()):
            raise SynthAdapterError("parameter values must be finite")
        output = Path(wav_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(self.renderer_path),
            str(self.plugin_path),
            str(Path(state_path).resolve()),
            str(output),
            "--midi-key",
            str(midi_key),
            "--velocity",
            str(velocity),
            "--note-frames",
            str(note_frames),
        ]
        if allow_parameter_coercion:
            command.extend(["--parameter-retention", "record"])
        for parameter_id, value in values.items():
            command.extend([str(parameter_id), str(value)])
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=self._environment(),
            )
        except subprocess.CalledProcessError as error:
            raise SynthAdapterError(
                f"CLAP render failed ({error.returncode}): {error.stderr.strip()}"
            ) from error
        try:
            metadata = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise SynthAdapterError("CLAP renderer stdout was not strict JSON") from error
        expected = {
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "bits_per_sample": BITS_PER_SAMPLE,
            "midi_key": midi_key,
            "velocity": velocity,
            "note_frames": note_frames,
            "tail_frames": TAIL_FRAMES,
            "total_frames": note_frames + TAIL_FRAMES,
            "block_size": BLOCK_SIZE,
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise SynthAdapterError(
                    f"renderer {key} mismatch: expected {value}, got {metadata.get(key)}"
                )
        clipped_samples = metadata.get("clipped_samples")
        if not isinstance(clipped_samples, int) or clipped_samples < 0:
            raise SynthAdapterError("renderer returned invalid clipped-sample evidence")
        if clipped_samples and not allow_clipping:
            raise SynthAdapterError(
                f"renderer clipped {clipped_samples} samples under a no-clipping contract"
            )
        changes = metadata.get("parameter_changes")
        if not isinstance(changes, list) or len(changes) != len(values):
            raise SynthAdapterError("renderer did not report every requested parameter")
        applied_values: dict[str, float] = {}
        coerced_parameters: list[dict[str, float | int]] = []
        for change, (parameter_id, requested) in zip(changes, values.items(), strict=True):
            if change.get("id") != parameter_id:
                raise SynthAdapterError("renderer reported parameters out of order")
            if not math.isclose(float(change.get("requested")), requested, abs_tol=1e-6):
                raise SynthAdapterError("renderer reported a different requested value")
            applied = float(change.get("applied"))
            if not math.isfinite(applied):
                raise SynthAdapterError("renderer reported a non-finite applied value")
            if not allow_parameter_coercion and not math.isclose(
                applied, requested, abs_tol=1e-6
            ):
                raise SynthAdapterError("real synth did not retain a requested parameter")
            applied_values[str(parameter_id)] = applied
            if not math.isclose(applied, requested, abs_tol=1e-6):
                coerced_parameters.append(
                    {"id": parameter_id, "requested": requested, "applied": applied}
                )
        if not output.is_file() or output.stat().st_size == 0:
            raise SynthAdapterError("real synth did not produce a WAV")
        return {
            **metadata,
            "wav_path": str(output),
            "wav_sha256": sha256_file(output),
            "state_sha256": sha256_file(state_path),
            "requested_values": {str(key): value for key, value in values.items()},
            "applied_values": applied_values,
            "coerced_parameters": coerced_parameters,
            "parameter_retention_mode": (
                "record" if allow_parameter_coercion else "exact"
            ),
        }

    def render_verified(self, **kwargs: Any) -> dict[str, Any]:
        output = Path(kwargs["wav_path"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent, prefix=".verify-") as directory:
            first_path = Path(directory) / "first.wav"
            second_path = Path(directory) / "second.wav"
            first = self.render_note(**{**kwargs, "wav_path": first_path})
            second = self.render_note(**{**kwargs, "wav_path": second_path})
            if first["wav_sha256"] != second["wav_sha256"]:
                raise SynthAdapterError("independent identical real-synth renders differ")
            shutil.move(first_path, output)
        return {
            **first,
            "wav_path": str(output),
            "verification_renders": 2,
            "byte_identical": True,
        }


class SurgeXTAdapter(ClapSynthAdapter):
    """Surge XT 1.3.x factory-preset adapter using exact native FXP state."""

    def __init__(self, *, factory_data_path: str | Path, **kwargs: Any) -> None:
        self.factory_data_path = Path(factory_data_path).resolve()
        self.factory_preset_root = self.factory_data_path / "patches_factory"
        if not self.factory_preset_root.is_dir():
            raise SynthAdapterError(
                f"Surge factory preset directory not found: {self.factory_preset_root}"
            )
        super().__init__(data_home=self.factory_data_path, **kwargs)

    def factory_presets(self) -> list[PresetRef]:
        presets = []
        for path in sorted(self.factory_preset_root.rglob("*.fxp")):
            relative = path.relative_to(self.factory_preset_root)
            category = relative.parts[0] if len(relative.parts) > 1 else "Uncategorized"
            presets.append(
                PresetRef(
                    name=path.stem,
                    category=category,
                    path=path,
                    relative_path=relative.as_posix(),
                )
            )
        if not presets:
            raise SynthAdapterError("Surge factory preset library is empty")
        return presets

    def bounded_factory_presets(
        self, limit: int, *, required_relative_path: str | None = None
    ) -> list[PresetRef]:
        if limit < 1:
            raise SynthAdapterError("preset limit must be positive")
        groups: dict[str, list[PresetRef]] = {}
        all_presets = self.factory_presets()
        for preset in all_presets:
            groups.setdefault(preset.category, []).append(preset)
        categories = sorted(groups)
        selected: list[PresetRef] = []
        index = 0
        while len(selected) < min(limit, len(all_presets)):
            added = False
            for category in categories:
                items = groups[category]
                if index < len(items):
                    selected.append(items[index])
                    added = True
                    if len(selected) >= limit:
                        break
            if not added:
                break
            index += 1
        if required_relative_path:
            required = next(
                (
                    preset
                    for preset in all_presets
                    if preset.relative_path == required_relative_path
                ),
                None,
            )
            if required is None:
                raise SynthAdapterError(
                    f"required factory preset not found: {required_relative_path}"
                )
            if required not in selected:
                selected[-1] = required
        return sorted(selected, key=lambda item: item.relative_path)

    def extract_preset_state(
        self, preset: PresetRef | str | Path, destination: str | Path
    ) -> dict[str, Any]:
        path = preset.path if isinstance(preset, PresetRef) else Path(preset)
        payload = path.read_bytes()
        offset = payload.find(b"sub3")
        if offset < 0:
            raise SynthAdapterError(f"Surge FXP contains no native state marker: {path}")
        state = payload[offset:]
        if len(state) < 64 or b"<?xml" not in state[:128]:
            raise SynthAdapterError(f"Surge FXP native state is malformed: {path}")
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(state)
        return {
            "preset_path": str(path.resolve()),
            "preset_sha256": sha256_file(path),
            "state_path": str(output.resolve()),
            "state_sha256": sha256_file(output),
            "fxp_state_offset": offset,
        }

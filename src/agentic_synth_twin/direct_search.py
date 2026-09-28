"""Direct black-box optimization against deterministic real-synth audio."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import random
import subprocess
import tempfile
import threading
import time
import uuid
import wave
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Could not import matplotlib.pyplot.*")
    import cma

from .audition import (
    BITS_PER_SAMPLE,
    BLOCK_SIZE,
    CHANNELS,
    MIDI_KEY,
    NOTE_FRAMES,
    SAMPLE_RATE,
    TAIL_FRAMES,
    TOTAL_FRAMES,
    VELOCITY,
    _write_json_atomic,
)
from .synth_state import load_canonical_state


SEARCH_SCHEMA = "agentic-synth-twin/direct-real-synth-search/v1"
SEARCH_SEED = 20_260_928
RANDOM_SEARCH_SEED = 20_260_929
DEFAULT_GENERATIONS = 20
DEFAULT_POPULATION_SIZE = 8
DEFAULT_BUDGET = DEFAULT_GENERATIONS * DEFAULT_POPULATION_SIZE
FILTER_TYPE_ID = 14_255

ACTIVE_PARAMETER_IDS = (8_675_309, 2_391, 17, 2_874)
ACTIVE_PARAMETER_LABELS = {
    8_675_309: "Oscillator Detuning",
    2_391: "Unison Spread",
    17: "Cutoff in Keys",
    2_874: "Amplitude Attack",
}
TARGET_NORMALIZED = (0.72, 0.68, 0.28, 0.62)

STFT_RESOLUTIONS = (
    {"fft_size": 256, "hop_size": 64},
    {"fft_size": 1_024, "hop_size": 256},
    {"fft_size": 4_096, "hop_size": 1_024},
)
ENVELOPE_WINDOW = 512
ENVELOPE_HOP = 128
LOG_MAGNITUDE_SCALE = 100.0
SPECTRAL_SCALE = 0.05
ENVELOPE_SCALE = 1.5
LOUDNESS_SCALE_DB = 24.0
OBJECTIVE_WEIGHTS = {
    "spectral": 0.65,
    "envelope": 0.25,
    "loudness": 0.10,
}


class DirectSearchError(RuntimeError):
    """Raised when direct-search evidence or execution violates its contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rounded(value: float) -> float:
    return round(float(value), 12)


def _find_parameter(state: Mapping[str, Any], parameter_id: int) -> Mapping[str, Any]:
    for parameter in state["parameters"]:
        if parameter["id"] == parameter_id:
            return parameter
    raise DirectSearchError(f"parameter id {parameter_id} is absent from real inventory")


def discover_search_parameters(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the four continuous real-plugin parameters in fixed search order."""

    parameters = []
    for parameter_id in ACTIVE_PARAMETER_IDS:
        source = _find_parameter(state, parameter_id)
        if source["flags"] & 1:
            raise DirectSearchError(
                f"continuous search parameter unexpectedly stepped: {parameter_id}"
            )
        if source["max"] <= source["min"]:
            raise DirectSearchError(f"invalid parameter range: {parameter_id}")
        parameters.append(
            {
                "id": parameter_id,
                "name": source["name"],
                "label": ACTIVE_PARAMETER_LABELS[parameter_id],
                "module": source["module"],
                "min": source["min"],
                "max": source["max"],
                "default": source["default"],
                "current": source["current"],
            }
        )
    filter_type = _find_parameter(state, FILTER_TYPE_ID)
    return parameters + [
        {
            "id": FILTER_TYPE_ID,
            "name": filter_type["name"],
            "fixed": True,
            "value": filter_type["current"],
        }
    ]


def normalize_value(value: float, parameter: Mapping[str, Any]) -> float:
    minimum, maximum = float(parameter["min"]), float(parameter["max"])
    numeric = float(value)
    if numeric < minimum - 1e-12 or numeric > maximum + 1e-12:
        raise DirectSearchError(f"value {numeric} is outside [{minimum}, {maximum}]")
    return (numeric - minimum) / (maximum - minimum)


def denormalize_value(value: float, parameter: Mapping[str, Any]) -> float:
    numeric = float(value)
    if numeric < -1e-12 or numeric > 1.0 + 1e-12:
        raise DirectSearchError(f"normalized value {numeric} is outside [0, 1]")
    numeric = min(1.0, max(0.0, numeric))
    return float(parameter["min"]) + numeric * (
        float(parameter["max"]) - float(parameter["min"])
    )


def vector_to_real(
    normalized: Sequence[float], parameters: Sequence[Mapping[str, Any]]
) -> dict[str, float]:
    if len(normalized) != len(ACTIVE_PARAMETER_IDS) or len(parameters) < len(
        ACTIVE_PARAMETER_IDS
    ):
        raise DirectSearchError("search vector must have four parameter values")
    return {
        str(parameter["id"]): _rounded(denormalize_value(value, parameter))
        for value, parameter in zip(
            normalized, parameters[: len(ACTIVE_PARAMETER_IDS)], strict=True
        )
    }


def vector_from_current(parameters: Sequence[Mapping[str, Any]]) -> list[float]:
    return [
        _rounded(normalize_value(parameter["current"], parameter))
        for parameter in parameters[: len(ACTIVE_PARAMETER_IDS)]
    ]


def _read_wav(path: str | Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != CHANNELS:
            raise DirectSearchError("objective requires the deterministic stereo WAV")
        if source.getsampwidth() != BITS_PER_SAMPLE // 8:
            raise DirectSearchError("objective requires deterministic 16-bit PCM")
        if source.getframerate() != SAMPLE_RATE:
            raise DirectSearchError("objective sample rate does not match audition")
        if source.getnframes() != TOTAL_FRAMES:
            raise DirectSearchError("objective duration does not match audition")
        pcm = np.frombuffer(source.readframes(TOTAL_FRAMES), dtype="<i2")
    return pcm.reshape(-1, CHANNELS).astype(np.float64) / 32768.0


def _framed(signal: np.ndarray, size: int, hop: int) -> np.ndarray:
    if signal.ndim != 1:
        raise DirectSearchError("framing expects mono samples")
    if len(signal) < size:
        signal = np.pad(signal, (0, size - len(signal)))
    remainder = (len(signal) - size) % hop
    if remainder:
        signal = np.pad(signal, (0, hop - remainder))
    shape = (1 + (len(signal) - size) // hop, size)
    strides = (signal.strides[0] * hop, signal.strides[0])
    return np.lib.stride_tricks.as_strided(
        signal, shape=shape, strides=strides, writeable=False
    )


def _log_spectrogram(signal: np.ndarray, fft_size: int, hop_size: int) -> np.ndarray:
    frames = _framed(signal, fft_size, hop_size)
    window = np.hanning(fft_size)
    magnitude = np.abs(np.fft.rfft(frames * window, axis=1)) / max(window.sum(), 1.0)
    return np.log1p(LOG_MAGNITUDE_SCALE * magnitude)


def _envelope(signal: np.ndarray) -> np.ndarray:
    frames = _framed(signal, ENVELOPE_WINDOW, ENVELOPE_HOP)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    return np.log1p(LOG_MAGNITUDE_SCALE * rms)


def _rms_dbfs(stereo: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(stereo * stereo)))
    return 20.0 * math.log10(max(rms, 1e-12))


@dataclass(frozen=True)
class AudioReference:
    """Precomputed target representation used by every objective evaluation."""

    stereo: np.ndarray
    mono: np.ndarray
    spectra: tuple[np.ndarray, ...]
    envelope: np.ndarray
    rms_dbfs: float


def prepare_audio_reference(path: str | Path) -> AudioReference:
    stereo = _read_wav(path)
    mono = np.mean(stereo, axis=1)
    spectra = tuple(
        _log_spectrogram(mono, item["fft_size"], item["hop_size"])
        for item in STFT_RESOLUTIONS
    )
    return AudioReference(
        stereo=stereo,
        mono=mono,
        spectra=spectra,
        envelope=_envelope(mono),
        rms_dbfs=_rms_dbfs(stereo),
    )


def compute_audio_objective(
    target: AudioReference | str | Path,
    candidate_path: str | Path,
) -> dict[str, float]:
    """Compute the fixed transparent multi-resolution audio objective."""

    reference = target if isinstance(target, AudioReference) else prepare_audio_reference(target)
    candidate = prepare_audio_reference(candidate_path)
    spectral_differences = [
        float(np.mean(np.abs(left - right)))
        for left, right in zip(reference.spectra, candidate.spectra, strict=True)
    ]
    spectral_loss = min(1.0, float(np.mean(spectral_differences)) / SPECTRAL_SCALE)
    envelope_loss = min(
        1.0,
        float(np.mean(np.abs(reference.envelope - candidate.envelope)))
        / ENVELOPE_SCALE,
    )
    loudness_loss = min(
        1.0, abs(reference.rms_dbfs - candidate.rms_dbfs) / LOUDNESS_SCALE_DB
    )
    total = (
        OBJECTIVE_WEIGHTS["spectral"] * spectral_loss
        + OBJECTIVE_WEIGHTS["envelope"] * envelope_loss
        + OBJECTIVE_WEIGHTS["loudness"] * loudness_loss
    )
    return {
        "spectral_loss": _rounded(spectral_loss),
        "envelope_loss": _rounded(envelope_loss),
        "loudness_loss": _rounded(loudness_loss),
        "total_loss": _rounded(total),
        "candidate_rms_dbfs": _rounded(candidate.rms_dbfs),
        "target_rms_dbfs": _rounded(reference.rms_dbfs),
    }


def spectrogram_preview(path: str | Path) -> dict[str, Any]:
    """Return a compact, fixed-scale-ready scientific spectrogram preview."""

    reference = prepare_audio_reference(path)
    matrix = reference.spectra[1]
    time_bins = min(96, matrix.shape[0])
    frequency_bins = min(72, matrix.shape[1])
    time_indices = np.linspace(0, matrix.shape[0] - 1, time_bins).astype(int)
    frequency_indices = np.linspace(0, matrix.shape[1] - 1, frequency_bins).astype(int)
    reduced = matrix[np.ix_(time_indices, frequency_indices)].T
    return {
        "values": [[round(float(value), 5) for value in row] for row in reduced],
        "time_seconds": TOTAL_FRAMES / SAMPLE_RATE,
        "max_frequency_hz": SAMPLE_RATE / 2,
    }


def _run_renderer(
    *,
    renderer: Path,
    plugin: Path,
    state_path: Path,
    wav_path: Path,
    parameter_values: Mapping[str, float],
) -> dict[str, Any]:
    command = [str(renderer), str(plugin), str(state_path), str(wav_path)]
    for parameter_id in ACTIVE_PARAMETER_IDS:
        command.extend([str(parameter_id), str(parameter_values[str(parameter_id)])])
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise DirectSearchError(
            f"real-synth renderer failed ({error.returncode}): {error.stderr.strip()}"
        ) from error
    try:
        metadata = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise DirectSearchError("renderer stdout was not strict JSON") from error
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
            raise DirectSearchError(
                f"renderer {key} mismatch: expected {value}, got {metadata.get(key)}"
            )
    changes = metadata.get("parameter_changes")
    if not isinstance(changes, list) or len(changes) != len(ACTIVE_PARAMETER_IDS):
        raise DirectSearchError("renderer did not report all active parameters")
    for change, parameter_id in zip(changes, ACTIVE_PARAMETER_IDS, strict=True):
        requested = float(parameter_values[str(parameter_id)])
        if change.get("id") != parameter_id:
            raise DirectSearchError("renderer reported active parameters out of order")
        if not math.isclose(float(change.get("requested")), requested, abs_tol=1e-10):
            raise DirectSearchError("renderer reported a different requested value")
        if not math.isclose(float(change.get("applied")), requested, abs_tol=1e-10):
            raise DirectSearchError("real synth did not retain an active parameter")
    if not wav_path.is_file() or wav_path.stat().st_size == 0:
        raise DirectSearchError("real synth did not produce a WAV")
    return metadata


def render_candidate(
    *,
    renderer: str | Path,
    plugin: str | Path,
    state_path: str | Path,
    wav_path: str | Path,
    normalized: Sequence[float],
    parameters: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    values = vector_to_real(normalized, parameters)
    output = Path(wav_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = _run_renderer(
        renderer=Path(renderer),
        plugin=Path(plugin),
        state_path=Path(state_path),
        wav_path=output,
        parameter_values=values,
    )
    return {
        "parameter_values_normalized": [_rounded(value) for value in normalized],
        "parameter_values_real": values,
        "wav_path": str(output.resolve()),
        "wav_sha256": _sha256(output),
        "peak_float": metadata["peak_float"],
        "clipped_samples": metadata["clipped_samples"],
    }


def _render_verified(
    *,
    renderer: Path,
    plugin: Path,
    state_path: Path,
    wav_path: Path,
    normalized: Sequence[float],
    parameters: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(dir=wav_path.parent, prefix=".verify-") as directory:
        first_path = Path(directory) / "first.wav"
        second_path = Path(directory) / "second.wav"
        first = render_candidate(
            renderer=renderer,
            plugin=plugin,
            state_path=state_path,
            wav_path=first_path,
            normalized=normalized,
            parameters=parameters,
        )
        second = render_candidate(
            renderer=renderer,
            plugin=plugin,
            state_path=state_path,
            wav_path=second_path,
            normalized=normalized,
            parameters=parameters,
        )
        if first["wav_sha256"] != second["wav_sha256"]:
            raise DirectSearchError("independent identical real-synth renders differ")
        os.replace(first_path, wav_path)
    return {
        **first,
        "wav_path": str(wav_path.resolve()),
        "verification_renders": 2,
        "byte_identical": True,
    }


def create_search_run(
    *,
    canonical_state_path: str | Path,
    plugin_path: str | Path,
    renderer_path: str | Path,
    output_directory: str | Path,
    seed: int = SEARCH_SEED,
    generations: int = DEFAULT_GENERATIONS,
    population_size: int = DEFAULT_POPULATION_SIZE,
) -> "SearchRun":
    return SearchRun(
        canonical_state_path=Path(canonical_state_path),
        plugin_path=Path(plugin_path),
        renderer_path=Path(renderer_path),
        output_directory=Path(output_directory),
        seed=seed,
        generations=generations,
        population_size=population_size,
    )


class SearchRun:
    """Thread-safe, persisted ask/render/score/tell search controller."""

    def __init__(
        self,
        *,
        canonical_state_path: Path,
        plugin_path: Path,
        renderer_path: Path,
        output_directory: Path,
        seed: int,
        generations: int,
        population_size: int,
    ):
        if generations < 1 or population_size < 2:
            raise DirectSearchError("search requires positive generations and population")
        if not plugin_path.is_dir():
            raise DirectSearchError(f"plugin bundle not found: {plugin_path}")
        if not renderer_path.is_file() or not os.access(renderer_path, os.X_OK):
            raise DirectSearchError(f"renderer is not executable: {renderer_path}")
        self.canonical_state_path = canonical_state_path.resolve()
        self.plugin_path = plugin_path.resolve()
        self.renderer_path = renderer_path.resolve()
        self.output_directory = output_directory.resolve()
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.audio_directory = self.output_directory / "audio"
        self.audio_directory.mkdir(exist_ok=True)
        self.state = load_canonical_state(self.canonical_state_path)
        discovered = discover_search_parameters(self.state)
        self.parameters = discovered[: len(ACTIVE_PARAMETER_IDS)]
        self.fixed_filter = discovered[-1]
        self.start_vector = vector_from_current(self.parameters)
        self.target_vector = list(TARGET_NORMALIZED)
        self.seed = seed
        self.generations = generations
        self.population_size = population_size
        self.budget = generations * population_size
        self.run_id = f"m8a-{uuid.uuid4().hex[:12]}"
        self.created_at = _now()
        self.status = "IDLE"
        self.error: str | None = None
        self.stopping_reason: str | None = None
        self.history: list[dict[str, Any]] = []
        self.generation_summaries: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.best: dict[str, Any] | None = None
        self.start: dict[str, Any] | None = None
        self.target: dict[str, Any] | None = None
        self.final_verification: dict[str, Any] | None = None
        self.revealed = False
        self.started_monotonic: float | None = None
        self.elapsed_before_start = 0.0
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._pause_requested = False
        self._stop_requested = False
        self._optimizer_state = {
            "mean": list(self.start_vector),
            "spread": [0.22] * len(self.start_vector),
            "sigma": 0.22,
        }
        self._prepare_fixed_audio()
        self._persist()

    @property
    def state_path(self) -> Path:
        return self.output_directory / "canonical-state.bin"

    @property
    def target_path(self) -> Path:
        return self.audio_directory / "target.wav"

    @property
    def start_path(self) -> Path:
        return self.audio_directory / "starting-patch.wav"

    @property
    def best_path(self) -> Path | None:
        if self.best is None:
            return None
        return Path(self.best["audio_path"])

    def _prepare_fixed_audio(self) -> None:
        state_bytes = base64.b64decode(
            self.state["opaque_state"]["data"], validate=True
        )
        self.state_path.write_bytes(state_bytes)
        self.target = _render_verified(
            renderer=self.renderer_path,
            plugin=self.plugin_path,
            state_path=self.state_path,
            wav_path=self.target_path,
            normalized=self.target_vector,
            parameters=self.parameters,
        )
        self.start = _render_verified(
            renderer=self.renderer_path,
            plugin=self.plugin_path,
            state_path=self.state_path,
            wav_path=self.start_path,
            normalized=self.start_vector,
            parameters=self.parameters,
        )
        self.reference = prepare_audio_reference(self.target_path)
        start_objective = compute_audio_objective(self.reference, self.start_path)
        self.start.update(start_objective)
        if self.target["peak_float"] <= 0.0:
            raise DirectSearchError("hidden target render is silent")
        if self.target["wav_sha256"] == self.start["wav_sha256"]:
            raise DirectSearchError("hidden target is identical to starting patch")
        self.target["target_parameters_private"] = {
            "normalized": list(self.target_vector),
            "real": vector_to_real(self.target_vector, self.parameters),
        }

    def start_search(self) -> dict[str, Any]:
        with self._condition:
            if self.status == "PAUSED":
                return self.resume_search()
            if self.status != "IDLE":
                raise DirectSearchError(f"cannot start search from {self.status}")
            self.status = "SEARCHING"
            self.started_monotonic = time.monotonic()
            self._thread = threading.Thread(
                target=self._search_worker, name=f"search-{self.run_id}", daemon=True
            )
            self._thread.start()
            self._persist()
            return self.public_status()

    def pause_search(self) -> dict[str, Any]:
        with self._condition:
            if self.status not in {"SEARCHING", "PAUSING"}:
                raise DirectSearchError(f"cannot pause search from {self.status}")
            self._pause_requested = True
            self.status = "PAUSING"
            self._persist()
            return self.public_status()

    def resume_search(self) -> dict[str, Any]:
        with self._condition:
            if self.status != "PAUSED":
                raise DirectSearchError(f"cannot resume search from {self.status}")
            self._pause_requested = False
            self.status = "SEARCHING"
            self._condition.notify_all()
            self._persist()
            return self.public_status()

    def stop_search(self) -> dict[str, Any]:
        with self._condition:
            if self.status not in {"SEARCHING", "PAUSING", "PAUSED"}:
                raise DirectSearchError(f"cannot stop search from {self.status}")
            self._stop_requested = True
            self._pause_requested = False
            if self.status == "PAUSED":
                self._condition.notify_all()
            self._persist()
            return self.public_status()

    def reveal_target(self) -> dict[str, Any]:
        with self._lock:
            if self.status not in {"COMPLETE", "STOPPED", "ERROR"}:
                raise DirectSearchError("target parameters remain hidden during search")
            self.revealed = True
            self._persist()
            return self.public_status()

    def _wait_if_paused_or_stopped(self) -> bool:
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
                0.22,
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
                if self._wait_if_paused_or_stopped():
                    break
                vectors = optimizer.ask()
                evaluated_vectors: list[list[float]] = []
                losses: list[float] = []
                generation_losses: list[float] = []
                for candidate_index, vector in enumerate(vectors, start=1):
                    if len(self.history) >= self.budget:
                        break
                    if self._wait_if_paused_or_stopped():
                        break
                    clipped = [min(1.0, max(0.0, float(value))) for value in vector]
                    record = self._evaluate(
                        clipped,
                        generation=generation,
                        candidate_index=candidate_index,
                    )
                    evaluated_vectors.append(list(vector))
                    losses.append(record["total_loss"])
                    generation_losses.append(record["total_loss"])
                if evaluated_vectors:
                    optimizer.tell(evaluated_vectors, losses)
                    covariance = np.asarray(optimizer.C)
                    spread = optimizer.sigma * np.sqrt(np.maximum(np.diag(covariance), 0))
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
                                "best_loss": self.best["total_loss"] if self.best else None,
                                "evaluations": len(self.history),
                            }
                        )
                        self._persist()
                if self._stop_requested or len(evaluated_vectors) < len(vectors):
                    break
            with self._lock:
                if self._stop_requested:
                    self.status = "STOPPED"
                    self.stopping_reason = "user_stop"
                else:
                    self.status = "COMPLETE"
                    self.stopping_reason = "fixed_budget_exhausted"
                self._verify_final_best()
                self._persist()
        except Exception as error:  # preserve the complete run on worker failure
            with self._lock:
                self.status = "ERROR"
                self.error = f"{type(error).__name__}: {error}"
                self.stopping_reason = "error"
                self._persist()

    def _evaluate(
        self, normalized: Sequence[float], *, generation: int, candidate_index: int
    ) -> dict[str, Any]:
        evaluation = len(self.history) + 1
        wav_path = self.audio_directory / f"eval-{evaluation:04d}.wav"
        rendered = render_candidate(
            renderer=self.renderer_path,
            plugin=self.plugin_path,
            state_path=self.state_path,
            wav_path=wav_path,
            normalized=normalized,
            parameters=self.parameters,
        )
        objective = compute_audio_objective(self.reference, wav_path)
        with self._lock:
            previous_best = self.best["total_loss"] if self.best else math.inf
            is_new_best = objective["total_loss"] < previous_best
            record = {
                "run_id": self.run_id,
                "generation": generation,
                "candidate_index": candidate_index,
                "total_evaluations": evaluation,
                "parameter_values_normalized": rendered[
                    "parameter_values_normalized"
                ],
                "parameter_values_real": rendered["parameter_values_real"],
                **objective,
                "is_new_best": is_new_best,
                "best_loss": (
                    objective["total_loss"] if is_new_best else previous_best
                ),
                "optimizer_mean": list(self._optimizer_state["mean"]),
                "optimizer_spread": list(self._optimizer_state["spread"]),
                "optimizer_sigma": self._optimizer_state["sigma"],
                "audio_path": str(wav_path.resolve()),
                "audio_url": f"/audio/eval-{evaluation:04d}.wav",
                "wav_sha256": rendered["wav_sha256"],
                "peak_float": rendered["peak_float"],
                "clipped_samples": rendered["clipped_samples"],
                "timestamp": _now(),
            }
            self.history.append(record)
            self.current = record
            if is_new_best:
                self.best = dict(record)
            self._append_history(record)
            self._persist()
            return record

    def _verify_final_best(self) -> None:
        if self.best is None:
            return
        verification_path = self.audio_directory / "final-best-rerender.wav"
        verified = _render_verified(
            renderer=self.renderer_path,
            plugin=self.plugin_path,
            state_path=self.state_path,
            wav_path=verification_path,
            normalized=self.best["parameter_values_normalized"],
            parameters=self.parameters,
        )
        matches = verified["wav_sha256"] == self.best["wav_sha256"]
        if not matches:
            raise DirectSearchError("final-best independent rerender hash differs")
        self.final_verification = {
            **verified,
            "matches_search_best": True,
            "search_best_sha256": self.best["wav_sha256"],
        }

    def _append_history(self, record: Mapping[str, Any]) -> None:
        path = self.output_directory / "history.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def elapsed_seconds(self) -> float:
        if self.started_monotonic is None:
            return self.elapsed_before_start
        if self.status in {"COMPLETE", "STOPPED", "ERROR"}:
            return self.elapsed_before_start
        return self.elapsed_before_start + time.monotonic() - self.started_monotonic

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            elapsed = self.elapsed_seconds()
            completed = len(self.history)
            start_loss = self.start["total_loss"] if self.start else None
            best_loss = self.best["total_loss"] if self.best else start_loss
            improvement = (
                0.0
                if not start_loss or best_loss is None
                else 100.0 * (start_loss - best_loss) / start_loss
            )
            generation = self.current["generation"] if self.current else 0
            payload: dict[str, Any] = {
                "schema": SEARCH_SCHEMA,
                "run_id": self.run_id,
                "status": self.status,
                "error": self.error,
                "generation": generation,
                "generations": self.generations,
                "evaluations": completed,
                "budget": self.budget,
                "evaluations_remaining": max(0, self.budget - completed),
                "current_loss": self.current["total_loss"] if self.current else None,
                "best_loss": best_loss,
                "starting_loss": start_loss,
                "improvement_percent": _rounded(improvement),
                "elapsed_seconds": _rounded(elapsed),
                "current": self.current,
                "best": self.best,
                "parameters": self.parameters,
                "fixed_filter": self.fixed_filter,
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
                "starting_audio_url": "/audio/starting-patch.wav",
                "best_audio_url": self.best["audio_url"] if self.best else None,
                "can_reveal": self.status in {"COMPLETE", "STOPPED", "ERROR"},
                "target_revealed": self.revealed,
                "stopping_reason": self.stopping_reason,
                "final_verification": self.final_verification,
            }
            if self.revealed:
                payload["target_parameters"] = self.target[
                    "target_parameters_private"
                ]
            return payload

    def private_evidence(self) -> dict[str, Any]:
        public = self.public_status()
        return {
            **public,
            "created_at": self.created_at,
            "canonical_state": {
                "path": str(self.canonical_state_path),
                "sha256": _sha256(self.canonical_state_path),
                "opaque_state_sha256": self.state["opaque_state"]["sha256"],
            },
            "plugin": self.state["plugin"],
            "audition": {
                "sample_rate": SAMPLE_RATE,
                "block_size": BLOCK_SIZE,
                "midi_key": MIDI_KEY,
                "velocity": VELOCITY,
                "note_frames": NOTE_FRAMES,
                "tail_frames": TAIL_FRAMES,
            },
            "hidden_target": self.target,
            "starting_patch": self.start,
            "history_count": len(self.history),
        }

    def _persist(self) -> None:
        if self.started_monotonic is not None and self.status in {
            "COMPLETE",
            "STOPPED",
            "ERROR",
        }:
            self.elapsed_before_start = time.monotonic() - self.started_monotonic
            self.started_monotonic = None
        _write_json_atomic(self.private_evidence(), self.output_directory / "run.json")

    def history_public(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self.history]

    def audio_path_for_url(self, url_path: str) -> Path:
        name = url_path.removeprefix("/audio/")
        if not name or Path(name).name != name:
            raise DirectSearchError("invalid audio path")
        path = self.audio_directory / name
        if not path.is_file():
            raise DirectSearchError("audio file does not exist")
        return path

    def spectra_public(self) -> dict[str, Any]:
        target = spectrogram_preview(self.target_path)
        best_path = self.best_path or self.start_path
        best = spectrogram_preview(best_path)
        maximum = max(
            max(max(row) for row in target["values"]),
            max(max(row) for row in best["values"]),
        )
        return {"target": target, "best": best, "shared_max": maximum}

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)


def objective_definition() -> dict[str, Any]:
    return {
        "formula": "0.65*spectral + 0.25*envelope + 0.10*loudness",
        "weights": OBJECTIVE_WEIGHTS,
        "spectral": {
            "representation": "multi-resolution Hann log-magnitude STFT",
            "resolutions": list(STFT_RESOLUTIONS),
            "log_magnitude_scale": LOG_MAGNITUDE_SCALE,
            "normalization_scale": SPECTRAL_SCALE,
        },
        "envelope": {
            "representation": "full frame-level log RMS envelope",
            "window": ENVELOPE_WINDOW,
            "hop": ENVELOPE_HOP,
            "normalization_scale": ENVELOPE_SCALE,
        },
        "loudness": {
            "representation": "absolute whole-file RMS dBFS difference",
            "normalization_db": LOUDNESS_SCALE_DB,
        },
    }


def benchmark_random_search(
    run: SearchRun,
    *,
    budget: int | None = None,
    seed: int = RANDOM_SEARCH_SEED,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run the equal-budget bounded random-search scientific control."""

    evaluation_budget = budget if budget is not None else run.budget
    generator = random.Random(seed)
    output = run.output_directory / "random-search"
    audio = output / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    best: dict[str, Any] | None = None
    rows = []
    started = time.monotonic()
    for index in range(1, evaluation_budget + 1):
        normalized = [generator.random() for _ in ACTIVE_PARAMETER_IDS]
        wav_path = audio / f"random-{index:04d}.wav"
        rendered = render_candidate(
            renderer=run.renderer_path,
            plugin=run.plugin_path,
            state_path=run.state_path,
            wav_path=wav_path,
            normalized=normalized,
            parameters=run.parameters,
        )
        objective = compute_audio_objective(run.reference, wav_path)
        row = {
            "evaluation": index,
            "parameter_values_normalized": rendered["parameter_values_normalized"],
            "parameter_values_real": rendered["parameter_values_real"],
            **objective,
            "wav_sha256": rendered["wav_sha256"],
        }
        if best is None or row["total_loss"] < best["total_loss"]:
            best = dict(row)
        rows.append(row)
        if progress:
            progress(index, evaluation_budget)
    result = {
        "schema": SEARCH_SCHEMA,
        "control": "bounded_random_search",
        "seed": seed,
        "budget": evaluation_budget,
        "starting_loss": run.start["total_loss"],
        "best": best,
        "improvement_percent": _rounded(
            100.0
            * (run.start["total_loss"] - best["total_loss"])
            / run.start["total_loss"]
        ),
        "runtime_seconds": _rounded(time.monotonic() - started),
        "history": rows,
    }
    _write_json_atomic(result, output / "result.json")
    return result


def build_compact_evidence(
    run: SearchRun, random_result: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if run.status not in {"COMPLETE", "STOPPED", "ERROR"}:
        raise DirectSearchError("compact evidence requires a finished search")
    status = run.public_status()
    best = run.best
    optimization_pass = bool(status["improvement_percent"] >= 30.0)
    adaptive_pass = bool(
        random_result is not None
        and best is not None
        and best["total_loss"] <= random_result["best"]["total_loss"]
    )
    compact_best = None
    if best is not None:
        compact_best = {
            key: value
            for key, value in best.items()
            if key not in {"audio_path", "audio_url", "timestamp", "run_id"}
        }
    compact_rerender = None
    if run.final_verification is not None:
        compact_rerender = {
            key: value
            for key, value in run.final_verification.items()
            if key != "wav_path"
        }
    return {
        "schema": SEARCH_SCHEMA,
        "result": {
            "engine_integrity": bool(
                run.target.get("byte_identical")
                and run.start.get("byte_identical")
                and run.final_verification
                and run.final_verification.get("matches_search_best")
                and all(row["clipped_samples"] == 0 for row in run.history)
            ),
            "optimization_pass": optimization_pass,
            "adaptive_search_pass": adaptive_pass,
            "audible_human_check": "PENDING_OWNER_AUDITION",
            "overall_numeric_pass": optimization_pass and adaptive_pass,
        },
        "run": {
            "run_id": run.run_id,
            "seed": run.seed,
            "status": run.status,
            "stopping_reason": run.stopping_reason,
            "evaluations": len(run.history),
            "generations": status["generation"],
            "population_size": run.population_size,
            "runtime_seconds": status["elapsed_seconds"],
            "starting_loss": status["starting_loss"],
            "final_best_loss": status["best_loss"],
            "improvement_percent": status["improvement_percent"],
        },
        "active_parameters": run.parameters,
        "fixed_filter": run.fixed_filter,
        "target": {
            "generation_procedure": "fixed interior normalized vector rendered twice from canonical state",
            "parameter_values_normalized": run.target_vector,
            "parameter_values_real": vector_to_real(run.target_vector, run.parameters),
            "wav_sha256": run.target["wav_sha256"],
            "deterministic": run.target["byte_identical"],
        },
        "starting_patch": {
            "parameter_values_normalized": run.start_vector,
            "parameter_values_real": vector_to_real(run.start_vector, run.parameters),
            "wav_sha256": run.start["wav_sha256"],
            "loss": run.start["total_loss"],
        },
        "final_best": compact_best,
        "final_rerender": compact_rerender,
        "objective": objective_definition(),
        "optimizer": status["optimizer"],
        "random_search": (
            None
            if random_result is None
            else {
                "seed": random_result["seed"],
                "budget": random_result["budget"],
                "best_loss": random_result["best"]["total_loss"],
                "improvement_percent": random_result["improvement_percent"],
                "runtime_seconds": random_result["runtime_seconds"],
            }
        ),
        "limitations": [
            "Audio-domain score is not human perceptual equivalence.",
            "One deterministic hidden target does not establish broad generalization.",
            "The 2-D cockpit map shows selected axes, not the full four-dimensional space.",
            "Milestone 7 remains separate failed-surrogate evidence; it is not used here.",
        ],
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Direct real-synth search engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "benchmark"):
        child = subparsers.add_parser(name)
        child.add_argument("--state", required=True)
        child.add_argument("--plugin", required=True)
        child.add_argument("--renderer", required=True)
        child.add_argument("--output", required=True)
        child.add_argument("--generations", type=int, default=DEFAULT_GENERATIONS)
        child.add_argument("--population", type=int, default=DEFAULT_POPULATION_SIZE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    run = create_search_run(
        canonical_state_path=args.state,
        plugin_path=args.plugin,
        renderer_path=args.renderer,
        output_directory=args.output,
        generations=args.generations,
        population_size=args.population,
    )
    run.start_search()
    run.wait()
    if run.status == "ERROR":
        raise DirectSearchError(run.error or "search failed")
    random_result = None
    if args.command == "benchmark":
        random_result = benchmark_random_search(
            run,
            progress=lambda index, total: print(
                f"random control {index}/{total}", flush=True
            ),
        )
    evidence = build_compact_evidence(run, random_result)
    path = run.output_directory / "compact-evidence.json"
    _write_json_atomic(evidence, path)
    print(json.dumps(evidence["result"], indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

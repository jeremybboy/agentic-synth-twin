"""Transparent descriptors for the full-library one-note timbre audit.

The representation is deliberately small and deterministic.  It is not a
learned perceptual model and its frozen weights are an experimental hypothesis.
"""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .audition import BITS_PER_SAMPLE, CHANNELS, SAMPLE_RATE, TOTAL_FRAMES


DESCRIPTOR_VERSION = "perceptual-retrieval-v1"
ANALYSIS_RMS = 0.1
F0_HZ = 130.8127826502993
MEL_BANDS = 96
MEL_LOW_HZ = 30.0
MEL_HIGH_HZ = 16_000.0
MEL_RESOLUTIONS = ((512, 128), (2_048, 512), (8_192, 2_048))
FLATNESS_FFT = 2_048
FLATNESS_HOP = 512
ENVELOPE_WINDOW = 512
ENVELOPE_HOP = 128
HARMONIC_FFT = 8_192
HARMONIC_HOP = 2_048
HARMONIC_TOLERANCE_CENTS = 30.0
INHARMONICITY_TOLERANCE_CENTS = 50.0
FAMILY_WEIGHTS = {
    "log_mel": 0.55,
    "envelope": 0.25,
    "harmonic": 0.15,
    "flatness": 0.05,
}


class PerceptualRetrievalError(RuntimeError):
    """Raised when an audio item violates the fixed retrieval contract."""


def _framed(signal: np.ndarray, size: int, hop: int) -> np.ndarray:
    if signal.ndim != 1:
        raise PerceptualRetrievalError("descriptor framing expects mono audio")
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


def _read_analysis_audio(path: str | Path) -> tuple[np.ndarray, float]:
    with wave.open(str(path), "rb") as source:
        contract = (
            source.getnchannels(),
            source.getsampwidth(),
            source.getframerate(),
            source.getnframes(),
        )
        expected = (CHANNELS, BITS_PER_SAMPLE // 8, SAMPLE_RATE, TOTAL_FRAMES)
        if contract != expected:
            raise PerceptualRetrievalError(
                f"retrieval WAV contract mismatch: expected {expected}, got {contract}"
            )
        pcm = np.frombuffer(source.readframes(TOTAL_FRAMES), dtype="<i2")
    stereo = pcm.reshape(-1, CHANNELS).astype(np.float64) / 32768.0
    absolute_rms = float(np.sqrt(np.mean(stereo * stereo)))
    if absolute_rms <= 1e-9:
        raise PerceptualRetrievalError("retrieval audio is silent")
    mono = np.mean(stereo, axis=1)
    mono_rms = float(np.sqrt(np.mean(mono * mono)))
    if mono_rms <= 1e-9:
        raise PerceptualRetrievalError("retrieval mono fold-down is silent")
    return mono * (ANALYSIS_RMS / mono_rms), absolute_rms


def _hz_to_mel(value: np.ndarray | float) -> np.ndarray | float:
    return 2_595.0 * np.log10(1.0 + np.asarray(value) / 700.0)


def _mel_to_hz(value: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(value) / 2_595.0) - 1.0)


@lru_cache(maxsize=None)
def _mel_filter_bank(fft_size: int) -> np.ndarray:
    frequencies = np.fft.rfftfreq(fft_size, 1.0 / SAMPLE_RATE)
    edges = _mel_to_hz(
        np.linspace(_hz_to_mel(MEL_LOW_HZ), _hz_to_mel(MEL_HIGH_HZ), MEL_BANDS + 2)
    )
    bank = np.zeros((MEL_BANDS, len(frequencies)), dtype=np.float64)
    for index in range(MEL_BANDS):
        left, center, right = edges[index : index + 3]
        rising = (frequencies - left) / max(center - left, 1e-12)
        falling = (right - frequencies) / max(right - center, 1e-12)
        bank[index] = np.maximum(0.0, np.minimum(rising, falling))
        bank[index] /= max(float(np.sum(bank[index])), 1e-12)
    return bank


def _power_spectrogram(signal: np.ndarray, fft_size: int, hop: int) -> np.ndarray:
    frames = _framed(signal, fft_size, hop)
    window = np.hanning(fft_size)
    spectrum = np.fft.rfft(frames * window, axis=1)
    return (np.abs(spectrum) / max(float(window.sum()), 1.0)) ** 2


def _log_mel(signal: np.ndarray, fft_size: int, hop: int) -> np.ndarray:
    power = _power_spectrogram(signal, fft_size, hop)
    mel_power = power @ _mel_filter_bank(fft_size).T
    return np.log10(np.maximum(mel_power, 1e-12)).astype(np.float32)


def _envelope_features(signal: np.ndarray) -> tuple[np.ndarray, float, float, bool, bool]:
    frames = _framed(signal, ENVELOPE_WINDOW, ENVELOPE_HOP)
    envelope = np.sqrt(np.mean(frames * frames, axis=1))
    envelope /= max(float(np.max(envelope)), 1e-12)
    peak_index = int(np.argmax(envelope))
    before = envelope[: peak_index + 1]
    above_10 = np.flatnonzero(before >= 0.10)
    above_90 = np.flatnonzero(before >= 0.90)
    attack_censored = not (len(above_10) and len(above_90))
    if attack_censored:
        attack_seconds = TOTAL_FRAMES / SAMPLE_RATE
    else:
        attack_seconds = max(0, int(above_90[0]) - int(above_10[0])) * ENVELOPE_HOP / SAMPLE_RATE
    after = envelope[peak_index:]
    below_90 = np.flatnonzero(after <= 0.90)
    below_40 = np.flatnonzero(after <= 0.40)
    decay_censored = not (len(below_90) and len(below_40) and below_40[-1] >= below_90[0])
    if decay_censored:
        decay_seconds = (len(envelope) - 1 - peak_index) * ENVELOPE_HOP / SAMPLE_RATE
    else:
        start = int(below_90[0])
        end_candidates = below_40[below_40 >= start]
        if not len(end_candidates):
            decay_censored = True
            decay_seconds = (len(envelope) - 1 - peak_index) * ENVELOPE_HOP / SAMPLE_RATE
        else:
            decay_seconds = (int(end_candidates[0]) - start) * ENVELOPE_HOP / SAMPLE_RATE
    return envelope.astype(np.float32), attack_seconds, decay_seconds, attack_censored, decay_censored


def _harmonic_features(signal: np.ndarray) -> tuple[np.ndarray, float, float]:
    power = np.mean(_power_spectrogram(signal, HARMONIC_FFT, HARMONIC_HOP), axis=0)
    frequencies = np.fft.rfftfreq(HARMONIC_FFT, 1.0 / SAMPLE_RATE)
    valid = (frequencies >= MEL_LOW_HZ) & (frequencies <= MEL_HIGH_HZ)
    total_power = max(float(np.sum(power[valid])), 1e-18)
    harmonic_energy: list[float] = []
    deviations: list[float] = []
    weights: list[float] = []
    harmonic_number = 1
    while harmonic_number * F0_HZ <= MEL_HIGH_HZ:
        expected = harmonic_number * F0_HZ
        ratio = 2.0 ** (HARMONIC_TOLERANCE_CENTS / 1_200.0)
        band = np.flatnonzero((frequencies >= expected / ratio) & (frequencies <= expected * ratio))
        if not len(band):
            band = np.asarray([int(np.argmin(np.abs(frequencies - expected)))])
        energy = float(np.sum(power[band]))
        harmonic_energy.append(energy)
        wide_ratio = 2.0 ** (INHARMONICITY_TOLERANCE_CENTS / 1_200.0)
        wide = np.flatnonzero((frequencies >= expected / wide_ratio) & (frequencies <= expected * wide_ratio))
        peak = int(wide[np.argmax(power[wide])]) if len(wide) else int(band[0])
        deviations.append(abs(float(frequencies[peak]) - expected) / F0_HZ)
        weights.append(max(float(power[peak]), 1e-18))
        harmonic_number += 1
    energy_array = np.asarray(harmonic_energy, dtype=np.float64)
    harmonic_total = float(np.sum(energy_array))
    profile = np.sqrt(energy_array / max(harmonic_total, 1e-18)).astype(np.float32)
    noise_ratio = max(0.0, min(1.0, 1.0 - harmonic_total / total_power))
    inharmonicity = float(np.average(deviations, weights=weights))
    return profile, noise_ratio, inharmonicity


def _flatness_features(signal: np.ndarray) -> np.ndarray:
    power = _power_spectrogram(signal, FLATNESS_FFT, FLATNESS_HOP)
    frequencies = np.fft.rfftfreq(FLATNESS_FFT, 1.0 / SAMPLE_RATE)
    band = power[:, (frequencies >= MEL_LOW_HZ) & (frequencies <= MEL_HIGH_HZ)]
    geometric = np.exp(np.mean(np.log(np.maximum(band, 1e-18)), axis=1))
    arithmetic = np.mean(band, axis=1)
    flatness = geometric / np.maximum(arithmetic, 1e-18)
    return np.quantile(flatness, (0.10, 0.50, 0.90)).astype(np.float32)


@dataclass(frozen=True)
class PerceptualDescriptor:
    log_mel: tuple[np.ndarray, ...]
    envelope: np.ndarray
    attack_seconds: float
    decay_seconds: float
    attack_right_censored: bool
    decay_right_censored: bool
    harmonic_profile: np.ndarray
    noise_ratio: float
    inharmonicity: float
    flatness_quantiles: np.ndarray
    absolute_rms: float


def describe_audio(path: str | Path) -> PerceptualDescriptor:
    signal, absolute_rms = _read_analysis_audio(path)
    envelope, attack, decay, attack_censored, decay_censored = _envelope_features(signal)
    harmonic, noise, inharmonicity = _harmonic_features(signal)
    return PerceptualDescriptor(
        log_mel=tuple(_log_mel(signal, fft, hop) for fft, hop in MEL_RESOLUTIONS),
        envelope=envelope,
        attack_seconds=attack,
        decay_seconds=decay,
        attack_right_censored=attack_censored,
        decay_right_censored=decay_censored,
        harmonic_profile=harmonic,
        noise_ratio=noise,
        inharmonicity=inharmonicity,
        flatness_quantiles=_flatness_features(signal),
        absolute_rms=absolute_rms,
    )


def save_descriptor(descriptor: PerceptualDescriptor, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        version=np.asarray(DESCRIPTOR_VERSION),
        log_mel_0=descriptor.log_mel[0].astype(np.float16),
        log_mel_1=descriptor.log_mel[1].astype(np.float16),
        log_mel_2=descriptor.log_mel[2].astype(np.float16),
        envelope=descriptor.envelope.astype(np.float16),
        attack_seconds=np.asarray(descriptor.attack_seconds),
        decay_seconds=np.asarray(descriptor.decay_seconds),
        attack_right_censored=np.asarray(descriptor.attack_right_censored),
        decay_right_censored=np.asarray(descriptor.decay_right_censored),
        harmonic_profile=descriptor.harmonic_profile.astype(np.float32),
        noise_ratio=np.asarray(descriptor.noise_ratio),
        inharmonicity=np.asarray(descriptor.inharmonicity),
        flatness_quantiles=descriptor.flatness_quantiles.astype(np.float32),
        absolute_rms=np.asarray(descriptor.absolute_rms),
    )


def load_descriptor(path: str | Path) -> PerceptualDescriptor:
    with np.load(path, allow_pickle=False) as payload:
        if str(payload["version"]) != DESCRIPTOR_VERSION:
            raise PerceptualRetrievalError("descriptor cache version changed")
        return PerceptualDescriptor(
            log_mel=tuple(payload[f"log_mel_{index}"].astype(np.float32) for index in range(3)),
            envelope=payload["envelope"].astype(np.float32),
            attack_seconds=float(payload["attack_seconds"]),
            decay_seconds=float(payload["decay_seconds"]),
            attack_right_censored=bool(payload["attack_right_censored"]),
            decay_right_censored=bool(payload["decay_right_censored"]),
            harmonic_profile=payload["harmonic_profile"].astype(np.float32),
            noise_ratio=float(payload["noise_ratio"]),
            inharmonicity=float(payload["inharmonicity"]),
            flatness_quantiles=payload["flatness_quantiles"].astype(np.float32),
            absolute_rms=float(payload["absolute_rms"]),
        )


def raw_family_distances(
    target: PerceptualDescriptor, candidate: PerceptualDescriptor
) -> dict[str, float]:
    mel_parts = [
        float(np.mean(np.abs(left - right)))
        for left, right in zip(target.log_mel, candidate.log_mel, strict=True)
    ]
    envelope_curve = float(np.mean(np.abs(target.envelope - candidate.envelope)))
    attack_difference = abs(target.attack_seconds - candidate.attack_seconds)
    decay_difference = abs(target.decay_seconds - candidate.decay_seconds)
    envelope = 0.60 * envelope_curve + 0.20 * attack_difference + 0.20 * decay_difference
    harmonic_profile = float(
        np.mean(np.abs(target.harmonic_profile - candidate.harmonic_profile))
    )
    harmonic = (
        0.70 * harmonic_profile
        + 0.20 * abs(target.noise_ratio - candidate.noise_ratio)
        + 0.10 * abs(target.inharmonicity - candidate.inharmonicity)
    )
    flatness = float(
        np.mean(np.abs(target.flatness_quantiles - candidate.flatness_quantiles))
    )
    return {
        "log_mel": float(np.mean(mel_parts)),
        "envelope": envelope,
        "harmonic": harmonic,
        "flatness": flatness,
        "log_mel_512": mel_parts[0],
        "log_mel_2048": mel_parts[1],
        "log_mel_8192": mel_parts[2],
        "envelope_curve": envelope_curve,
        "attack_seconds_difference": attack_difference,
        "decay_seconds_difference": decay_difference,
        "harmonic_profile_difference": harmonic_profile,
        "noise_ratio_difference": abs(target.noise_ratio - candidate.noise_ratio),
        "inharmonicity_difference": abs(target.inharmonicity - candidate.inharmonicity),
        "absolute_rms_difference": abs(target.absolute_rms - candidate.absolute_rms),
    }


def normalize_and_rank(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    if not rows:
        raise PerceptualRetrievalError("cannot rank an empty retrieval library")
    medians = {
        family: float(np.median([float(row["raw_distances"][family]) for row in rows]))
        for family in FAMILY_WEIGHTS
    }
    if any(not math.isfinite(value) or value <= 0.0 for value in medians.values()):
        raise PerceptualRetrievalError("a retrieval-family median is not positive and finite")
    ranked: list[dict[str, Any]] = []
    for source in rows:
        raw = dict(source["raw_distances"])
        normalized = {family: raw[family] / medians[family] for family in FAMILY_WEIGHTS}
        contributions = {
            family: FAMILY_WEIGHTS[family] * normalized[family] for family in FAMILY_WEIGHTS
        }
        ranked.append(
            {
                **source,
                "normalized_distances": normalized,
                "weighted_contributions": contributions,
                "retrieval_score": float(sum(contributions.values())),
            }
        )
    ranked.sort(key=lambda row: (row["retrieval_score"], row["preset_relative_path"]))
    for rank, row in enumerate(ranked, start=1):
        row["perceptual_rank"] = rank
    return ranked, medians


def score_with_frozen_medians(
    target: PerceptualDescriptor,
    candidate: PerceptualDescriptor,
    medians: Mapping[str, float],
) -> dict[str, Any]:
    """Score one candidate with the exact frozen full-library normalization."""

    missing = set(FAMILY_WEIGHTS) - set(medians)
    if missing:
        raise PerceptualRetrievalError(
            f"frozen family medians are incomplete: {sorted(missing)}"
        )
    denominators = {family: float(medians[family]) for family in FAMILY_WEIGHTS}
    if any(
        not math.isfinite(value) or value <= 0.0
        for value in denominators.values()
    ):
        raise PerceptualRetrievalError(
            "frozen family medians must be positive and finite"
        )
    raw = raw_family_distances(target, candidate)
    normalized = {
        family: float(raw[family] / denominators[family])
        for family in FAMILY_WEIGHTS
    }
    contributions = {
        family: float(FAMILY_WEIGHTS[family] * normalized[family])
        for family in FAMILY_WEIGHTS
    }
    return {
        "raw_distances": raw,
        "normalized_distances": normalized,
        "weighted_contributions": contributions,
        "retrieval_score": float(sum(contributions.values())),
    }


def descriptor_definition() -> dict[str, Any]:
    return {
        "version": DESCRIPTOR_VERSION,
        "preprocessing": {
            "stereo_to_mono": "arithmetic mean",
            "alignment": f"complete fixed {TOTAL_FRAMES / SAMPLE_RATE:.1f}-second audition",
            "analysis_rms": ANALYSIS_RMS,
            "playback_audio_modified": False,
            "absolute_rms": "diagnostic only",
        },
        "families": {
            "log_mel": {
                "weight": FAMILY_WEIGHTS["log_mel"],
                "resolutions": [
                    {"fft": fft, "hop": hop} for fft, hop in MEL_RESOLUTIONS
                ],
                "bands": MEL_BANDS,
                "range_hz": [MEL_LOW_HZ, MEL_HIGH_HZ],
                "distance": "mean absolute log10-mel difference per resolution, then mean",
                "capped": False,
            },
            "envelope": {
                "weight": FAMILY_WEIGHTS["envelope"],
                "rms_window": ENVELOPE_WINDOW,
                "hop": ENVELOPE_HOP,
                "formula": "0.60 * normalized-curve MAE + 0.20 * attack seconds difference + 0.20 * decay seconds difference",
                "attack": "10-to-90 percent; duration limit when right-censored",
                "decay": "post-peak 90-to-40 percent; remaining duration when right-censored",
                "units": "mixed: dimensionless normalized amplitude and seconds",
                "capped": False,
            },
            "harmonic": {
                "weight": FAMILY_WEIGHTS["harmonic"],
                "f0_hz": F0_HZ,
                "harmonic_tolerance_cents": HARMONIC_TOLERANCE_CENTS,
                "inharmonicity_tolerance_cents": INHARMONICITY_TOLERANCE_CENTS,
                "formula": "0.70 * harmonic-profile MAE + 0.20 * noise-ratio difference + 0.10 * inharmonicity difference",
                "capped": False,
            },
            "flatness": {
                "weight": FAMILY_WEIGHTS["flatness"],
                "fft": FLATNESS_FFT,
                "hop": FLATNESS_HOP,
                "quantiles": [0.10, 0.50, 0.90],
                "distance": "mean absolute quantile difference",
                "capped": False,
            },
        },
        "normalization": "per target and family, divide by complete retrieval-library median raw distance",
    }


def descriptor_preview(
    audio_path: str | Path, descriptor: PerceptualDescriptor
) -> dict[str, Any]:
    """Return compact, shared-scale-ready arrays for the retrieval audit UI."""

    signal, _ = _read_analysis_audio(audio_path)
    waveform_indices = np.linspace(0, len(signal) - 1, 256).astype(int)
    envelope_indices = np.linspace(0, len(descriptor.envelope) - 1, 192).astype(int)
    matrix = descriptor.log_mel[1]
    time_indices = np.linspace(0, matrix.shape[0] - 1, min(128, matrix.shape[0])).astype(int)
    return {
        "waveform": [round(float(value), 6) for value in signal[waveform_indices]],
        "envelope": [
            round(float(value), 6) for value in descriptor.envelope[envelope_indices]
        ],
        "log_mel": [
            [round(float(value), 5) for value in row]
            for row in matrix[time_indices].T
        ],
    }

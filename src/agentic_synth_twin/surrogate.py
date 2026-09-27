"""Milestone 7: leakage-safe surrogate training on the frozen synth dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np
import scipy
import sklearn
import threadpoolctl
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import StratifiedShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .audition import _write_json_atomic
from .dataset import (
    ATTACK_ID,
    CUTOFF_ID,
    DATASET_SAMPLE_COUNT,
    DATASET_SCHEMA,
    FILTER_TYPE_ID,
    _render_verified_sample,
)
from .synth_state import load_canonical_state


CONFIG_SCHEMA = "agentic-synth-twin/surrogate-config/v1"
SPLIT_SCHEMA = "agentic-synth-twin/surrogate-split/v1"
FREEZE_SCHEMA = "agentic-synth-twin/surrogate-freeze/v1"
RESULTS_SCHEMA = "agentic-synth-twin/surrogate-results/v1"
PREDICTIONS_SCHEMA = "agentic-synth-twin/surrogate-predictions/v1"
RERENDER_SCHEMA = "agentic-synth-twin/surrogate-rerender-check/v1"
MODEL_SCHEMA = "agentic-synth-twin/surrogate-model/v1"
STATUS_SCHEMA = "agentic-synth-twin/surrogate-status/v1"

SPLIT_SEED = 20_260_928
MODEL_SEED = 20_260_929
FROZEN_DATASET_SHA256 = "9942500229339874cf231e244300cdfe2e70811957119f904646b63290c2c3ae"
FILTER_VALUES = tuple(range(6))
INPUT_KEYS = ("filter_type", "cutoff_keys", "attack_seconds")
TARGET_KEYS = (
    "rms_dbfs",
    "peak_dbfs",
    "spectral_centroid_hz",
    "spectral_rolloff_85_hz",
    "attack_10_to_90_seconds",
    "release_90_to_10_seconds",
)
TARGET_LABELS = {
    "rms_dbfs": "RMS dBFS",
    "peak_dbfs": "Peak dBFS",
    "spectral_centroid_hz": "Spectral centroid",
    "spectral_rolloff_85_hz": "Spectral rolloff (85%)",
    "attack_10_to_90_seconds": "Attack duration",
    "release_90_to_10_seconds": "Release duration",
}
TARGET_UNITS = {
    "rms_dbfs": "dBFS",
    "peak_dbfs": "dBFS",
    "spectral_centroid_hz": "Hz",
    "spectral_rolloff_85_hz": "Hz",
    "attack_10_to_90_seconds": "s",
    "release_90_to_10_seconds": "s",
}
SELECTION_TARGETS = tuple(key for key in TARGET_KEYS if key != "release_90_to_10_seconds")
LEARNING_CURVE_SIZES = (32, 64, 96, 128)


class SurrogateError(RuntimeError):
    """Raised when training would violate the Milestone 7 evidence contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def default_config() -> dict[str, Any]:
    """Return the predeclared modeling and success policy."""

    return {
        "schema": CONFIG_SCHEMA,
        "split_seed": SPLIT_SEED,
        "model_seed": MODEL_SEED,
        "input_contract": {
            "filter_type": {
                "parameter_id": FILTER_TYPE_ID,
                "treatment": "categorical one-hot; numeric distance is not assumed",
                "categories": list(FILTER_VALUES),
            },
            "cutoff_keys": {"parameter_id": CUTOFF_ID, "treatment": "continuous"},
            "attack_seconds": {"parameter_id": ATTACK_ID, "treatment": "continuous"},
        },
        "targets": list(TARGET_KEYS),
        "selection_targets": list(SELECTION_TARGETS),
        "split": {"train": 166, "validation": 38, "test": 52},
        "selection_rule": (
            "lowest validation mean normalized MAE across the five predeclared "
            "selection targets; ties follow linear then random_forest"
        ),
        "candidate_order": ["mean", "linear", "random_forest"],
        "selectable_models": ["linear", "random_forest"],
        "models": {
            "mean": {"strategy": "mean"},
            "linear": {"alpha": 1.0},
            "random_forest": {
                "n_estimators": 256,
                "max_depth": 12,
                "min_samples_leaf": 2,
                "max_features": 1.0,
                "random_state": MODEL_SEED,
                "n_jobs": -1,
            },
        },
        "learning_curve_sizes": list(LEARNING_CURVE_SIZES),
        "success_thresholds": {
            "useful_target_mae_improvement_over_mean": 0.30,
            "rms_mae_dbfs_max": 2.0,
            "spectral_centroid_spearman_min": 0.80,
        },
        "release_low_information_rule": {
            "training_standard_deviation_seconds_max": 0.010,
            "training_range_seconds_max": 0.050,
            "effect": "report and model Release, but exclude it from model selection",
        },
        "structured_holdout": {
            "label": "upper cutoff and attack region",
            "cutoff_keys_min": 85.0,
            "attack_seconds_min": 2.0 / 3.0,
            "purpose": "bounded diagnostic only; not part of primary model selection",
        },
        "test_policy": (
            "test targets are not materialized for model development; selected family, "
            "preprocessing, hyperparameters, thresholds, final fitted artifact, and their "
            "hashes are persisted before the single final test evaluation"
        ),
    }


def load_dataset(dataset_path: str | Path) -> dict[str, Any]:
    """Load and validate the frozen Milestone 6 manifest without modifying examples."""

    path = Path(dataset_path)
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("schema") != DATASET_SCHEMA:
        raise SurrogateError("Milestone 6 dataset schema is not supported")
    samples = dataset.get("samples")
    if not isinstance(samples, list) or len(samples) != DATASET_SAMPLE_COUNT:
        raise SurrogateError("Milestone 7 requires the frozen 256-example dataset")
    identifiers: set[str] = set()
    for sample in samples:
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or sample_id in identifiers:
            raise SurrogateError("dataset sample IDs must be unique strings")
        identifiers.add(sample_id)
        parameters = sample.get("parameter_values", {})
        filter_value = parameters.get(str(FILTER_TYPE_ID))
        if filter_value not in FILTER_VALUES or int(filter_value) != filter_value:
            raise SurrogateError("Filter Type must remain an observed categorical value 0-5")
        for parameter_id in (CUTOFF_ID, ATTACK_ID):
            if not isinstance(parameters.get(str(parameter_id)), (int, float)):
                raise SurrogateError("continuous synth inputs must remain numeric")
        features = sample.get("wav", {}).get("features", {})
        for target in TARGET_KEYS:
            value = features.get(target)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise SurrogateError(f"target {target} is missing or non-finite")
    return dataset


def create_split(dataset: Mapping[str, Any], *, seed: int = SPLIT_SEED) -> dict[str, Any]:
    """Create a deterministic Filter Type-stratified 166/38/52 split manifest."""

    samples = list(dataset["samples"])
    sample_ids = np.asarray([sample["sample_id"] for sample in samples], dtype=object)
    labels = np.asarray(
        [sample["parameter_values"][str(FILTER_TYPE_ID)] for sample in samples],
        dtype=int,
    )
    first = StratifiedShuffleSplit(n_splits=1, test_size=52, random_state=seed)
    development_indices, test_indices = next(first.split(sample_ids, labels))
    development_ids = sample_ids[development_indices]
    development_labels = labels[development_indices]
    second = StratifiedShuffleSplit(n_splits=1, test_size=38, random_state=seed + 1)
    train_relative, validation_relative = next(
        second.split(development_ids, development_labels)
    )
    groups = {
        "train": sorted(development_ids[train_relative].tolist()),
        "validation": sorted(development_ids[validation_relative].tolist()),
        "test": sorted(sample_ids[test_indices].tolist()),
    }
    if set(groups["train"]) & set(groups["validation"]):
        raise SurrogateError("train and validation overlap")
    if (set(groups["train"]) | set(groups["validation"])) & set(groups["test"]):
        raise SurrogateError("development and test overlap")
    if set().union(*(set(values) for values in groups.values())) != set(sample_ids):
        raise SurrogateError("split does not cover the complete dataset")
    by_id = {sample["sample_id"]: sample for sample in samples}
    category_counts = {
        split: {
            str(value): sum(
                by_id[sample_id]["parameter_values"][str(FILTER_TYPE_ID)] == value
                for sample_id in identifiers
            )
            for value in FILTER_VALUES
        }
        for split, identifiers in groups.items()
    }
    return {
        "schema": SPLIT_SCHEMA,
        "seed": seed,
        "dataset_sha256": "assigned-when-persisted",
        "policy": "Filter Type-stratified deterministic split created before fitting",
        "counts": {name: len(values) for name, values in groups.items()},
        "filter_type_counts": category_counts,
        "sample_ids": groups,
    }


def _rows_for_ids(
    dataset: Mapping[str, Any], sample_ids: Iterable[str]
) -> list[Mapping[str, Any]]:
    by_id = {sample["sample_id"]: sample for sample in dataset["samples"]}
    try:
        return [by_id[sample_id] for sample_id in sample_ids]
    except KeyError as error:
        raise SurrogateError(f"split references unknown sample: {error.args[0]}") from error


def prepare_features(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    """Return Filter Type, Cutoff, Attack; Filter Type is encoded later as categorical."""

    return np.asarray(
        [
            [
                sample["parameter_values"][str(FILTER_TYPE_ID)],
                sample["parameter_values"][str(CUTOFF_ID)],
                sample["parameter_values"][str(ATTACK_ID)],
            ]
            for sample in rows
        ],
        dtype=float,
    )


def prepare_targets(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    return np.asarray(
        [
            [float(sample["wav"]["features"][target]) for target in TARGET_KEYS]
            for sample in rows
        ],
        dtype=float,
    )


def _preprocessor(*, scale_continuous: bool) -> ColumnTransformer:
    continuous: StandardScaler | str = StandardScaler() if scale_continuous else "passthrough"
    return ColumnTransformer(
        [
            (
                "filter_type",
                OneHotEncoder(
                    categories=[list(FILTER_VALUES)],
                    handle_unknown="ignore",
                    sparse_output=False,
                ),
                [0],
            ),
            ("continuous", continuous, [1, 2]),
        ],
        verbose_feature_names_out=False,
    )


def build_model(name: str, config: Mapping[str, Any]) -> Pipeline:
    """Build one predeclared candidate with explicit categorical preprocessing."""

    parameters = dict(config["models"][name])
    if name == "mean":
        estimator = DummyRegressor(**parameters)
        scale = False
    elif name == "linear":
        estimator = Ridge(**parameters)
        scale = True
    elif name == "random_forest":
        estimator = RandomForestRegressor(**parameters)
        scale = False
    else:
        raise SurrogateError(f"unknown predeclared model: {name}")
    return Pipeline([("features", _preprocessor(scale_continuous=scale)), ("model", estimator)])


def fit_model(name: str, config: Mapping[str, Any], x: np.ndarray, y: np.ndarray) -> Pipeline:
    model = build_model(name, config)
    model.fit(x, y)
    return model


def predict(model: Pipeline, x: np.ndarray) -> np.ndarray:
    values = np.asarray(model.predict(x), dtype=float)
    if values.ndim == 1:
        values = values[:, np.newaxis]
    if values.shape[1] != len(TARGET_KEYS):
        raise SurrogateError("model prediction does not cover every DSP target")
    return values


def _rankdata(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=float)
    index = 0
    while index < values.size:
        end = index + 1
        while end < values.size and values[order[end]] == values[order[index]]:
            end += 1
        ranks[order[index:end]] = (index + end - 1) / 2.0 + 1.0
        index = end
    return ranks


def _spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    if left.size < 2 or np.ptp(left) == 0 or np.ptp(right) == 0:
        return None
    correlation = float(np.corrcoef(_rankdata(left), _rankdata(right))[0, 1])
    return None if not math.isfinite(correlation) else correlation


def target_scales(training_targets: np.ndarray) -> dict[str, dict[str, float]]:
    scales = {}
    for index, target in enumerate(TARGET_KEYS):
        values = training_targets[:, index]
        scales[target] = {
            "minimum": float(np.min(values)),
            "maximum": float(np.max(values)),
            "range": float(np.ptp(values)),
            "standard_deviation": float(np.std(values)),
        }
    return scales


def evaluate_model(
    actual: np.ndarray,
    predicted: np.ndarray,
    scales: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """Calculate target-wise metrics using training-only normalization scales."""

    target_metrics: dict[str, Any] = {}
    normalized = []
    for index, target in enumerate(TARGET_KEYS):
        mae = float(mean_absolute_error(actual[:, index], predicted[:, index]))
        target_range = float(scales[target]["range"])
        normalized_mae = None if target_range <= 0 else mae / target_range
        r2 = float(r2_score(actual[:, index], predicted[:, index]))
        spearman = _spearman(actual[:, index], predicted[:, index])
        target_metrics[target] = {
            "label": TARGET_LABELS[target],
            "unit": TARGET_UNITS[target],
            "mae": mae,
            "normalized_mae": normalized_mae,
            "r2": r2,
            "spearman": spearman,
        }
        if target in SELECTION_TARGETS and normalized_mae is not None:
            normalized.append(normalized_mae)
    return {
        "sample_count": int(actual.shape[0]),
        "selection_mean_normalized_mae": float(np.mean(normalized)),
        "targets": target_metrics,
    }


def _stratified_subset_indices(
    labels: np.ndarray, size: int, *, seed: int
) -> np.ndarray:
    if size >= labels.size:
        return np.arange(labels.size)
    splitter = StratifiedShuffleSplit(n_splits=1, train_size=size, random_state=seed)
    subset, _ = next(splitter.split(np.zeros(labels.size), labels))
    return np.sort(subset)


def generate_learning_curve(
    name: str,
    config: Mapping[str, Any],
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_validation: np.ndarray,
    y_validation: np.ndarray,
    scales: Mapping[str, Mapping[str, float]],
) -> list[dict[str, Any]]:
    points = []
    labels = x_train[:, 0].astype(int)
    sizes = [size for size in config["learning_curve_sizes"] if size < len(x_train)]
    sizes.append(len(x_train))
    for ordinal, size in enumerate(sizes):
        indices = _stratified_subset_indices(labels, size, seed=MODEL_SEED + ordinal)
        model = fit_model(name, config, x_train[indices], y_train[indices])
        training = evaluate_model(y_train[indices], predict(model, x_train[indices]), scales)
        validation = evaluate_model(y_validation, predict(model, x_validation), scales)
        points.append(
            {
                "training_samples": int(size),
                "training_mean_normalized_mae": training[
                    "selection_mean_normalized_mae"
                ],
                "validation_mean_normalized_mae": validation[
                    "selection_mean_normalized_mae"
                ],
            }
        )
    return points


def _prediction_records(
    rows: Sequence[Mapping[str, Any]],
    actual: np.ndarray,
    predicted: np.ndarray,
    *,
    split: str,
) -> list[dict[str, Any]]:
    records = []
    for row_index, sample in enumerate(rows):
        values = {
            target: {
                "actual": float(actual[row_index, target_index]),
                "predicted": float(predicted[row_index, target_index]),
                "residual": float(
                    predicted[row_index, target_index] - actual[row_index, target_index]
                ),
            }
            for target_index, target in enumerate(TARGET_KEYS)
        }
        records.append(
            {
                "sample_id": sample["sample_id"],
                "split": split,
                "inputs": {
                    "filter_type": int(sample["parameter_values"][str(FILTER_TYPE_ID)]),
                    "cutoff_keys": float(sample["parameter_values"][str(CUTOFF_ID)]),
                    "attack_seconds": float(sample["parameter_values"][str(ATTACK_ID)]),
                },
                "targets": values,
            }
        )
    return records


def _group_metrics(
    records: Sequence[Mapping[str, Any]],
    scales: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    if not records:
        return {"sample_count": 0, "targets": {}}
    actual = np.asarray(
        [[record["targets"][target]["actual"] for target in TARGET_KEYS] for record in records]
    )
    predicted = np.asarray(
        [
            [record["targets"][target]["predicted"] for target in TARGET_KEYS]
            for record in records
        ]
    )
    return evaluate_model(actual, predicted, scales)


def regional_error(
    records: Sequence[Mapping[str, Any]],
    scales: Mapping[str, Mapping[str, float]],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[tuple[str, list[Mapping[str, Any]]]]] = {
        "filter_type": [
            (
                str(value),
                [record for record in records if record["inputs"]["filter_type"] == value],
            )
            for value in FILTER_VALUES
        ],
        "cutoff_region": [
            ("low 1-42", [r for r in records if r["inputs"]["cutoff_keys"] < 43]),
            (
                "mid 43-84",
                [r for r in records if 43 <= r["inputs"]["cutoff_keys"] < 85],
            ),
            ("high 85-127", [r for r in records if r["inputs"]["cutoff_keys"] >= 85]),
        ],
        "attack_region": [
            ("short 0-.333", [r for r in records if r["inputs"]["attack_seconds"] < 1 / 3]),
            (
                "mid .333-.667",
                [
                    r
                    for r in records
                    if 1 / 3 <= r["inputs"]["attack_seconds"] < 2 / 3
                ],
            ),
            ("long .667-1", [r for r in records if r["inputs"]["attack_seconds"] >= 2 / 3]),
        ],
    }
    return {
        dimension: [
            {"region": label, **_group_metrics(group, scales)} for label, group in entries
        ]
        for dimension, entries in groups.items()
    }


def permutation_importance(
    model: Pipeline,
    x_validation: np.ndarray,
    y_validation: np.ndarray,
    scales: Mapping[str, Mapping[str, float]],
    *,
    repeats: int = 16,
) -> list[dict[str, Any]]:
    """Permutation importance on validation data; values are not causal effects."""

    baseline = evaluate_model(y_validation, predict(model, x_validation), scales)[
        "selection_mean_normalized_mae"
    ]
    generator = np.random.Generator(np.random.PCG64(MODEL_SEED))
    entries = []
    for column, name in enumerate(INPUT_KEYS):
        increases = []
        for _ in range(repeats):
            shuffled = x_validation.copy()
            shuffled[:, column] = generator.permutation(shuffled[:, column])
            score = evaluate_model(y_validation, predict(model, shuffled), scales)[
                "selection_mean_normalized_mae"
            ]
            increases.append(score - baseline)
        entries.append(
            {
                "input": name,
                "mean_validation_normalized_mae_increase": float(np.mean(increases)),
                "standard_deviation": float(np.std(increases)),
                "repeats": repeats,
                "interpretation": "predictive permutation importance, not causality",
            }
        )
    return sorted(
        entries,
        key=lambda item: item["mean_validation_normalized_mae_increase"],
        reverse=True,
    )


def _structured_holdout(
    dataset: Mapping[str, Any],
    selected_name: str,
    config: Mapping[str, Any],
    scales: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    policy = config["structured_holdout"]
    holdout = [
        sample
        for sample in dataset["samples"]
        if sample["parameter_values"][str(CUTOFF_ID)] >= policy["cutoff_keys_min"]
        and sample["parameter_values"][str(ATTACK_ID)] >= policy["attack_seconds_min"]
    ]
    holdout_ids = {sample["sample_id"] for sample in holdout}
    development = [
        sample for sample in dataset["samples"] if sample["sample_id"] not in holdout_ids
    ]
    if len(holdout) < len(FILTER_VALUES):
        raise SurrogateError("structured holdout region is too small")
    model = fit_model(
        selected_name,
        config,
        prepare_features(development),
        prepare_targets(development),
    )
    actual = prepare_targets(holdout)
    predicted = predict(model, prepare_features(holdout))
    return {
        "label": policy["label"],
        "purpose": policy["purpose"],
        "definition": {
            "cutoff_keys_min": policy["cutoff_keys_min"],
            "attack_seconds_min": policy["attack_seconds_min"],
        },
        "training_sample_count": len(development),
        "holdout_sample_count": len(holdout),
        "holdout_sample_ids": sorted(holdout_ids),
        "metrics": evaluate_model(actual, predicted, scales),
    }


def _threshold_results(
    selected_test: Mapping[str, Any], mean_test: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    threshold = config["success_thresholds"]
    per_target = {}
    for target in TARGET_KEYS:
        baseline = float(mean_test["targets"][target]["mae"])
        selected = float(selected_test["targets"][target]["mae"])
        improvement = 0.0 if baseline == 0 else (baseline - selected) / baseline
        per_target[target] = {
            "mae_improvement_over_mean": improvement,
            "threshold": threshold["useful_target_mae_improvement_over_mean"],
            "passed": improvement
            >= threshold["useful_target_mae_improvement_over_mean"],
        }
    rms_mae = float(selected_test["targets"]["rms_dbfs"]["mae"])
    centroid_rank = selected_test["targets"]["spectral_centroid_hz"]["spearman"]
    gates = {
        "aggregate_learned_model_beats_mean": {
            "selected_normalized_mae": selected_test["selection_mean_normalized_mae"],
            "mean_normalized_mae": mean_test["selection_mean_normalized_mae"],
            "passed": selected_test["selection_mean_normalized_mae"]
            < mean_test["selection_mean_normalized_mae"],
        },
        "all_selection_targets_improve_mae_30_percent": {
            "targets": list(SELECTION_TARGETS),
            "minimum_improvement": threshold[
                "useful_target_mae_improvement_over_mean"
            ],
            "passed": all(per_target[target]["passed"] for target in SELECTION_TARGETS),
        },
        "rms_mae": {
            "value_dbfs": rms_mae,
            "maximum_dbfs": threshold["rms_mae_dbfs_max"],
            "passed": rms_mae <= threshold["rms_mae_dbfs_max"],
        },
        "spectral_centroid_rank": {
            "value": centroid_rank,
            "minimum": threshold["spectral_centroid_spearman_min"],
            "passed": centroid_rank is not None
            and centroid_rank >= threshold["spectral_centroid_spearman_min"],
        },
    }
    return {
        "predeclared": deepcopy(threshold),
        "per_target": per_target,
        "gates": gates,
        "overall_passed": all(item["passed"] for item in gates.values()),
    }


def save_model(bundle: Mapping[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    joblib.dump(dict(bundle), temporary, compress=3)
    os.replace(temporary, destination)


def load_model(path: str | Path) -> dict[str, Any]:
    bundle = joblib.load(Path(path))
    if not isinstance(bundle, dict) or bundle.get("schema") != MODEL_SCHEMA:
        raise SurrogateError("serialized surrogate model schema is not supported")
    if tuple(bundle.get("targets", ())) != TARGET_KEYS:
        raise SurrogateError("serialized surrogate target contract changed")
    return bundle


def _git_version(repository_root: Path) -> dict[str, Any]:
    def run(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain", "--untracked-files=no")),
    }


def _write_status(path: Path, status: Mapping[str, Any]) -> None:
    _write_json_atomic(dict(status), path)


def _select_rerender_rows(test_rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    ordered = sorted(
        test_rows, key=lambda sample: sample["parameter_values"][str(CUTOFF_ID)]
    )
    indices = (0, len(ordered) // 2, len(ordered) - 1)
    return [ordered[index] for index in indices]


def run_rerender_check(
    *,
    rows: Sequence[Mapping[str, Any]],
    canonical_state_path: str | Path,
    plugin_path: str | Path,
    renderer_path: str | Path,
    output_directory: str | Path,
) -> dict[str, Any]:
    """Rerender three test states without adding them to training."""

    state = load_canonical_state(canonical_state_path)
    import base64

    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / ".canonical-state.bin"
    state_path.write_bytes(base64.b64decode(state["opaque_state"]["data"], validate=True))
    checks = []
    try:
        for sample in _select_rerender_rows(rows):
            wav_path = root / f"{sample['sample_id']}.wav"
            measured, renderer_metadata = _render_verified_sample(
                renderer=Path(renderer_path),
                plugin=Path(plugin_path),
                state_path=state_path,
                wav_path=wav_path,
                parameter_values=sample["parameter_values"],
            )
            expected = sample["wav"]
            feature_match = measured["features"] == expected["features"]
            checks.append(
                {
                    "sample_id": sample["sample_id"],
                    "split": "test",
                    "parameter_values": sample["parameter_values"],
                    "expected_wav_sha256": expected["sha256"],
                    "rerendered_wav_sha256": measured["sha256"],
                    "wav_sha256_match": measured["sha256"] == expected["sha256"],
                    "features_exact_match": feature_match,
                    "expected_features": expected["features"],
                    "rerendered_features": measured["features"],
                    "render_verification": {
                        "renders": 2,
                        "byte_identical": True,
                        "clipped_samples": renderer_metadata["clipped_samples"],
                    },
                    "used_as_additional_training_data": False,
                }
            )
    finally:
        state_path.unlink(missing_ok=True)
    return {
        "schema": RERENDER_SCHEMA,
        "created_at": _utc_now(),
        "purpose": "real-synth integrity check; rerenders are not new training examples",
        "checks": checks,
        "all_exact": all(
            check["wav_sha256_match"] and check["features_exact_match"]
            for check in checks
        ),
    }


def _write_predictions_csv(records: Sequence[Mapping[str, Any]], path: Path) -> None:
    fields = ["sample_id", "split", *INPUT_KEYS]
    for target in TARGET_KEYS:
        fields.extend([f"{target}_actual", f"{target}_predicted", f"{target}_residual"])
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row: dict[str, Any] = {
                "sample_id": record["sample_id"],
                "split": record["split"],
                **record["inputs"],
            }
            for target in TARGET_KEYS:
                row.update(
                    {
                        f"{target}_actual": record["targets"][target]["actual"],
                        f"{target}_predicted": record["targets"][target]["predicted"],
                        f"{target}_residual": record["targets"][target]["residual"],
                    }
                )
            writer.writerow(row)


def train_surrogate(
    *,
    dataset_path: str | Path,
    output_directory: str | Path,
    repository_root: str | Path,
    canonical_state_path: str | Path | None = None,
    plugin_path: str | Path | None = None,
    renderer_path: str | Path | None = None,
    rerender: bool = True,
    config_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the complete local training, freeze, one-time test, and diagnostics flow."""

    dataset_file = Path(dataset_path)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    config = deepcopy(dict(config_override) if config_override is not None else default_config())
    if config.get("schema") != CONFIG_SCHEMA:
        raise SurrogateError("surrogate config schema is not supported")
    config_path = output / "config.json"
    _write_json_atomic(config, config_path)
    dataset = load_dataset(dataset_file)
    dataset_hash = _sha256(dataset_file)
    if dataset_hash != FROZEN_DATASET_SHA256:
        raise SurrogateError(
            "Milestone 6 dataset bytes changed; new training requires explicit defect approval"
        )
    split = create_split(dataset, seed=int(config["split_seed"]))
    split["dataset_sha256"] = dataset_hash
    split_path = output / "split.json"
    _write_json_atomic(split, split_path)

    status_path = output / "status.json"
    status: dict[str, Any] = {
        "schema": STATUS_SCHEMA,
        "updated_at": _utc_now(),
        "phase": "model_selection",
        "test": "locked",
        "models": {name: "waiting" for name in config["candidate_order"]},
        "timeline": [
            {"at": _utc_now(), "event": "config_and_split_persisted"},
            {"at": _utc_now(), "event": "final_test_locked"},
        ],
    }
    _write_status(status_path, status)

    train_rows = _rows_for_ids(dataset, split["sample_ids"]["train"])
    validation_rows = _rows_for_ids(dataset, split["sample_ids"]["validation"])
    x_train = prepare_features(train_rows)
    y_train = prepare_targets(train_rows)
    x_validation = prepare_features(validation_rows)
    y_validation = prepare_targets(validation_rows)
    scales = target_scales(y_train)
    release_rule = config["release_low_information_rule"]
    release_scale = scales["release_90_to_10_seconds"]
    release_low_information = (
        release_scale["standard_deviation"]
        <= release_rule["training_standard_deviation_seconds_max"]
        and release_scale["range"] <= release_rule["training_range_seconds_max"]
    )

    fitted: dict[str, Pipeline] = {}
    candidates: dict[str, Any] = {}
    curves: dict[str, Any] = {}
    for name in config["candidate_order"]:
        status["models"][name] = "fitting"
        status["updated_at"] = _utc_now()
        status["timeline"].append({"at": _utc_now(), "event": f"{name}_fitting"})
        _write_status(status_path, status)
        model = fit_model(name, config, x_train, y_train)
        fitted[name] = model
        status["models"][name] = "evaluating"
        status["updated_at"] = _utc_now()
        status["timeline"].append({"at": _utc_now(), "event": f"{name}_evaluating"})
        _write_status(status_path, status)
        training_prediction = predict(model, x_train)
        validation_prediction = predict(model, x_validation)
        candidates[name] = {
            "state": "complete",
            "hyperparameters": deepcopy(config["models"][name]),
            "train": evaluate_model(y_train, training_prediction, scales),
            "validation": evaluate_model(y_validation, validation_prediction, scales),
        }
        curves[name] = generate_learning_curve(
            name,
            config,
            x_train,
            y_train,
            x_validation,
            y_validation,
            scales,
        )
        status["models"][name] = "complete"
        status["updated_at"] = _utc_now()
        status["timeline"].append({"at": _utc_now(), "event": f"{name}_complete"})
        _write_status(status_path, status)

    selectable = list(config["selectable_models"])
    selected_name = min(
        selectable,
        key=lambda name: (
            candidates[name]["validation"]["selection_mean_normalized_mae"],
            selectable.index(name),
        ),
    )
    selected_development_model = fitted[selected_name]
    importance = permutation_importance(
        selected_development_model, x_validation, y_validation, scales
    )

    development_rows = [*train_rows, *validation_rows]
    x_development = prepare_features(development_rows)
    y_development = prepare_targets(development_rows)
    final_model = fit_model(selected_name, config, x_development, y_development)
    model_path = output / "model.joblib"
    bundle = {
        "schema": MODEL_SCHEMA,
        "created_at": _utc_now(),
        "model_type": selected_name,
        "inputs": list(INPUT_KEYS),
        "targets": list(TARGET_KEYS),
        "dataset_sha256": dataset_hash,
        "split_sha256": _sha256(split_path),
        "config_sha256": _sha256(config_path),
        "training_sample_ids": sorted(
            split["sample_ids"]["train"] + split["sample_ids"]["validation"]
        ),
        "pipeline": final_model,
    }
    save_model(bundle, model_path)
    freeze = {
        "schema": FREEZE_SCHEMA,
        "created_at": _utc_now(),
        "test_status": "locked",
        "dataset_sha256": dataset_hash,
        "config_sha256": _sha256(config_path),
        "split_sha256": _sha256(split_path),
        "model_artifact_sha256": _sha256(model_path),
        "selected_model": selected_name,
        "selection_rule": config["selection_rule"],
        "selection_targets": list(SELECTION_TARGETS),
        "candidate_validation_scores": {
            name: candidates[name]["validation"]["selection_mean_normalized_mae"]
            for name in config["candidate_order"]
        },
        "predeclared_success_thresholds": deepcopy(config["success_thresholds"]),
        "preprocessing": {
            "filter_type": "fixed one-hot categories 0-5",
            "continuous": (
                "standardized for linear; passed through for mean and random forest"
            ),
        },
        "proof": "written and hashed before test rows or targets are materialized",
    }
    freeze_path = output / "freeze.json"
    _write_json_atomic(freeze, freeze_path)
    freeze_hash_before_test = _sha256(freeze_path)
    status["phase"] = "final_model_frozen"
    status["timeline"].append(
        {"at": _utc_now(), "event": "final_model_frozen_before_test"}
    )
    status["updated_at"] = _utc_now()
    _write_status(status_path, status)

    # This is the first point at which primary test targets are materialized.
    test_rows = _rows_for_ids(dataset, split["sample_ids"]["test"])
    x_test = prepare_features(test_rows)
    y_test = prepare_targets(test_rows)
    selected_test_prediction = predict(final_model, x_test)
    selected_test = evaluate_model(y_test, selected_test_prediction, scales)
    final_mean = fit_model("mean", config, x_development, y_development)
    mean_test_prediction = predict(final_mean, x_test)
    mean_test = evaluate_model(y_test, mean_test_prediction, scales)
    if _sha256(freeze_path) != freeze_hash_before_test:
        raise SurrogateError("frozen pre-test record changed during final evaluation")

    selected_train_prediction = predict(selected_development_model, x_train)
    selected_validation_prediction = predict(selected_development_model, x_validation)
    predictions = [
        *_prediction_records(
            train_rows, y_train, selected_train_prediction, split="train"
        ),
        *_prediction_records(
            validation_rows,
            y_validation,
            selected_validation_prediction,
            split="validation",
        ),
        *_prediction_records(test_rows, y_test, selected_test_prediction, split="test"),
    ]
    predictions_document = {
        "schema": PREDICTIONS_SCHEMA,
        "selected_model": selected_name,
        "note": (
            "train and validation predictions use the train-only development model; "
            "test predictions use the frozen final model fitted on train plus validation"
        ),
        "records": predictions,
    }
    predictions_path = output / "predictions.json"
    _write_json_atomic(predictions_document, predictions_path)
    _write_predictions_csv(predictions, output / "predictions.csv")

    structured = _structured_holdout(dataset, selected_name, config, scales)
    rerender_result: dict[str, Any]
    if rerender:
        if canonical_state_path is None or plugin_path is None or renderer_path is None:
            raise SurrogateError("real-synth rerender paths are required for the full workflow")
        rerender_result = run_rerender_check(
            rows=test_rows,
            canonical_state_path=canonical_state_path,
            plugin_path=plugin_path,
            renderer_path=renderer_path,
            output_directory=output / "rerenders",
        )
        if not rerender_result["all_exact"]:
            raise SurrogateError("real-synth rerender integrity check failed")
    else:
        rerender_result = {
            "schema": RERENDER_SCHEMA,
            "created_at": _utc_now(),
            "purpose": "test-only run skipped real-synth rerender",
            "checks": [],
            "all_exact": None,
        }
    rerender_path = output / "rerender-check.json"
    _write_json_atomic(rerender_result, rerender_path)

    thresholds = _threshold_results(selected_test, mean_test, config)
    repository = Path(repository_root)
    results = {
        "schema": RESULTS_SCHEMA,
        "created_at": _utc_now(),
        "claim": (
            "bounded parameter-to-DSP approximation inside the sampled Filter Type, "
            "Cutoff, and Attack region; not timbre understanding or inverse sound design"
        ),
        "code": _git_version(repository),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "scipy": scipy.__version__,
            "joblib": joblib.__version__,
            "threadpoolctl": threadpoolctl.__version__,
        },
        "dataset": {
            "filename": dataset_file.name,
            "sha256": dataset_hash,
            "sample_count": len(dataset["samples"]),
            "inputs": deepcopy(config["input_contract"]),
            "targets": [
                {"key": target, "label": TARGET_LABELS[target], "unit": TARGET_UNITS[target]}
                for target in TARGET_KEYS
            ],
            "release_characterization": {
                **release_scale,
                "low_information_by_predeclared_rule": release_low_information,
                "rule": deepcopy(release_rule),
            },
            "quiet_examples_removed": False,
        },
        "split": {
            "counts": deepcopy(split["counts"]),
            "manifest_sha256": _sha256(split_path),
            "filter_type_counts": deepcopy(split["filter_type_counts"]),
        },
        "protocol": {
            "test_locked_during_model_selection": True,
            "freeze_sha256_before_test": freeze_hash_before_test,
            "freeze_unchanged_after_test": _sha256(freeze_path)
            == freeze_hash_before_test,
            "final_test_evaluations": 1,
            "test_status": "revealed_after_freeze",
        },
        "target_scales_from_train_only": scales,
        "candidates": candidates,
        "selection": {
            "selected_model": selected_name,
            "rule": config["selection_rule"],
            "final_training_rows": "train plus validation after family/config freeze",
        },
        "final_test": {
            "selected_model": selected_test,
            "mean_baseline": mean_test,
            "thresholds": thresholds,
        },
        "learning_curves": curves,
        "predictions": {
            "filename": predictions_path.name,
            "sha256": _sha256(predictions_path),
        },
        "regional_error_on_final_test": regional_error(
            [record for record in predictions if record["split"] == "test"], scales
        ),
        "permutation_importance_on_validation": importance,
        "structured_holdout_diagnostic": structured,
        "real_synth_rerender": {
            "filename": rerender_path.name,
            "sha256": _sha256(rerender_path),
            "all_exact": rerender_result["all_exact"],
            "sample_count": len(rerender_result["checks"]),
        },
        "artifacts": {
            "config": {"filename": config_path.name, "sha256": _sha256(config_path)},
            "split": {"filename": split_path.name, "sha256": _sha256(split_path)},
            "freeze": {"filename": freeze_path.name, "sha256": _sha256(freeze_path)},
            "model": {
                "filename": model_path.name,
                "sha256": _sha256(model_path),
                "format": "joblib serialized scikit-learn pipeline; load only from trusted repository evidence",
            },
        },
        "limitations": [
            "256 samples test feasibility, not robust generalization.",
            "Filter Type categories are opaque plugin values rather than semantic filter names.",
            "DSP descriptors do not establish human perceptual quality or timbre understanding.",
            "The structured holdout is a bounded diagnostic, not a broad extrapolation claim.",
            "No inverse search, candidate ranking, or LOCK workflow is implemented.",
        ],
    }
    results_path = output / "results.json"
    _write_json_atomic(results, results_path)
    status["phase"] = "complete"
    status["test"] = "revealed_after_freeze"
    status["updated_at"] = _utc_now()
    status["timeline"].extend(
        [
            {"at": _utc_now(), "event": "final_test_evaluated_once"},
            {"at": _utc_now(), "event": "diagnostics_and_rerenders_complete"},
        ]
    )
    _write_status(status_path, status)
    return results


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train and evaluate the bounded Milestone 7 surrogate"
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--state")
    parser.add_argument("--plugin")
    parser.add_argument("--renderer")
    parser.add_argument("--skip-rerender", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    results = train_surrogate(
        dataset_path=args.dataset,
        output_directory=args.output,
        repository_root=args.repository_root,
        canonical_state_path=args.state,
        plugin_path=args.plugin,
        renderer_path=args.renderer,
        rerender=not args.skip_rerender,
    )
    selected = results["selection"]["selected_model"]
    counts = results["split"]["counts"]
    print(
        f"selected {selected}; train={counts['train']} validation={counts['validation']} "
        f"test={counts['test']}; wrote {Path(args.output) / 'results.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

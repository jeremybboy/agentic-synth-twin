"""Milestone 8B fixed multi-target validation for direct real-synth search."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audition import _write_json_atomic
from .direct_search import (
    DEFAULT_BUDGET,
    DEFAULT_GENERATIONS,
    DEFAULT_POPULATION_SIZE,
    RANDOM_SEARCH_SEED,
    SEARCH_SEED,
    DirectSearchError,
    benchmark_random_search,
    build_compact_evidence,
    create_search_run,
)


SUITE_SCHEMA = "agentic-synth-twin/multitarget-validation/v1"
BLINDING_SEED = 20_260_930
TARGET_SUITE = (
    {
        "id": "dark-short",
        "label": "Dark short onset",
        "normalized": (0.52, 0.15, 0.18, 0.02),
    },
    {
        "id": "bright-wide-short",
        "label": "Bright wide short onset",
        "normalized": (0.65, 0.85, 0.80, 0.03),
    },
    {
        "id": "dark-slow",
        "label": "Dark slow onset",
        "normalized": (0.40, 0.55, 0.22, 0.80),
    },
    {
        "id": "bright-slow",
        "label": "Bright slow onset",
        "normalized": (0.58, 0.35, 0.82, 0.75),
    },
    {
        "id": "tight-low",
        "label": "Tight low-cutoff",
        "normalized": (0.48, 0.05, 0.12, 0.10),
    },
    {
        "id": "wide-detuned",
        "label": "Wide detuned medium onset",
        "normalized": (0.80, 0.90, 0.62, 0.18),
    },
    {
        "id": "muted-keylike",
        "label": "Muted key-like onset",
        "normalized": (0.38, 0.25, 0.30, 0.07),
    },
    {
        "id": "center-crosscheck",
        "label": "Interior cross-check",
        "normalized": (0.62, 0.48, 0.55, 0.42),
    },
)

OPTIMIZATION_MINIMUM_IMPROVEMENT_PERCENT = 30.0
OPTIMIZATION_REQUIRED_TARGETS = 6
ADAPTIVE_REQUIRED_WINS = 5


def _blind_assignment(target_id: str) -> dict[str, str]:
    digest = hashlib.sha256(f"{BLINDING_SEED}:{target_id}".encode()).digest()
    if digest[0] % 2:
        return {"candidate_a": "cma", "candidate_b": "random"}
    return {"candidate_a": "random", "candidate_b": "cma"}


def _relative(path: str | Path, root: Path) -> str:
    return str(Path(path).resolve().relative_to(root.resolve()))


def run_validation_suite(
    *,
    canonical_state_path: str | Path,
    plugin_path: str | Path,
    renderer_path: str | Path,
    output_directory: str | Path,
    targets: Sequence[Mapping[str, Any]] = TARGET_SUITE,
) -> dict[str, Any]:
    """Run identical CMA/random budgets on fixed, real-synth-generated targets."""

    output = Path(output_directory).resolve()
    if output.exists() and any(output.iterdir()):
        raise DirectSearchError(f"multi-target output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for target_index, target in enumerate(targets, start=1):
        target_id = str(target["id"])
        print(f"target {target_index}/{len(targets)}: {target_id}", flush=True)
        target_output = output / "targets" / target_id
        run = create_search_run(
            canonical_state_path=canonical_state_path,
            plugin_path=plugin_path,
            renderer_path=renderer_path,
            output_directory=target_output,
            seed=SEARCH_SEED,
            generations=DEFAULT_GENERATIONS,
            population_size=DEFAULT_POPULATION_SIZE,
            target_normalized=target["normalized"],
        )
        run.start_search()
        run.wait()
        if run.status != "COMPLETE":
            raise DirectSearchError(f"CMA search failed for {target_id}: {run.error}")
        random_result = benchmark_random_search(
            run, budget=DEFAULT_BUDGET, seed=RANDOM_SEARCH_SEED
        )
        compact = build_compact_evidence(run, random_result)
        assignment = _blind_assignment(target_id)
        cma_best = compact["final_best"]
        random_best = random_result["best"]
        cma_wins = cma_best["total_loss"] <= random_best["total_loss"]
        audio = {
            "target": _relative(run.target_path, output),
            "start": _relative(run.start_path, output),
            "cma": _relative(run.best_path, output),
            "random": _relative(random_best["audio_path"], output),
        }
        results.append(
            {
                "id": target_id,
                "label": target["label"],
                "index": target_index,
                "target_parameters_private": compact["target"],
                "starting_loss": compact["run"]["starting_loss"],
                "cma": {
                    "best_loss": cma_best["total_loss"],
                    "improvement_percent": compact["run"]["improvement_percent"],
                    "best_parameters_normalized": cma_best[
                        "parameter_values_normalized"
                    ],
                    "best_parameters_real": cma_best["parameter_values_real"],
                    "best_evaluation": cma_best["total_evaluations"],
                    "wav_sha256": cma_best["wav_sha256"],
                    "runtime_seconds": compact["run"]["runtime_seconds"],
                    "evaluations": compact["run"]["evaluations"],
                },
                "random": {
                    "best_loss": random_best["total_loss"],
                    "improvement_percent": random_result["improvement_percent"],
                    "best_parameters_normalized": random_best[
                        "parameter_values_normalized"
                    ],
                    "best_parameters_real": random_best["parameter_values_real"],
                    "best_evaluation": random_best["evaluation"],
                    "wav_sha256": random_best["wav_sha256"],
                    "runtime_seconds": random_result["runtime_seconds"],
                    "evaluations": random_result["budget"],
                },
                "cma_wins": cma_wins,
                "engine_integrity": compact["result"]["engine_integrity"],
                "audio_private": audio,
                "blind_assignment_private": assignment,
            }
        )
        _write_json_atomic(
            {"schema": SUITE_SCHEMA, "completed_targets": results},
            output / "progress.json",
        )

    cma_losses = [item["cma"]["best_loss"] for item in results]
    random_losses = [item["random"]["best_loss"] for item in results]
    improvement_passes = sum(
        item["cma"]["improvement_percent"]
        >= OPTIMIZATION_MINIMUM_IMPROVEMENT_PERCENT
        for item in results
    )
    cma_wins = sum(item["cma_wins"] for item in results)
    median_cma = statistics.median(cma_losses)
    median_random = statistics.median(random_losses)
    aggregate = {
        "target_count": len(results),
        "engine_integrity_pass": all(item["engine_integrity"] for item in results),
        "optimization_pass_count": improvement_passes,
        "optimization_required_count": OPTIMIZATION_REQUIRED_TARGETS,
        "optimization_gate_pass": improvement_passes
        >= OPTIMIZATION_REQUIRED_TARGETS,
        "cma_win_count": cma_wins,
        "adaptive_required_wins": ADAPTIVE_REQUIRED_WINS,
        "median_cma_best_loss": median_cma,
        "median_random_best_loss": median_random,
        "adaptive_gate_pass": cma_wins >= ADAPTIVE_REQUIRED_WINS
        and median_cma <= median_random,
        "human_audition": "PENDING_OWNER_BLIND_COMPARISON",
        "total_search_runtime_seconds": sum(
            item["cma"]["runtime_seconds"] + item["random"]["runtime_seconds"]
            for item in results
        ),
    }
    suite = {
        "schema": SUITE_SCHEMA,
        "procedure": {
            "target_source": "eight fixed parameter states rendered by the real synth",
            "target_count": len(results),
            "cma_seed": SEARCH_SEED,
            "random_seed": RANDOM_SEARCH_SEED,
            "blinding_seed": BLINDING_SEED,
            "evaluations_per_method_per_target": DEFAULT_BUDGET,
            "total_candidate_evaluations": 2 * DEFAULT_BUDGET * len(results),
            "success_gates": {
                "engine_integrity": "all targets",
                "optimization": f"at least {OPTIMIZATION_REQUIRED_TARGETS}/{len(results)} CMA runs improve >= {OPTIMIZATION_MINIMUM_IMPROVEMENT_PERCENT}%",
                "adaptive_value": f"CMA wins at least {ADAPTIVE_REQUIRED_WINS}/{len(results)} and median CMA loss <= median random loss",
                "human_audition": "owner blind comparison reported separately",
            },
        },
        "aggregate": aggregate,
        "targets_private": results,
    }
    _write_json_atomic(suite, output / "suite.json")
    print(json.dumps(aggregate, indent=2), flush=True)
    return suite


def public_suite(suite: Mapping[str, Any], *, reveal: bool = False) -> dict[str, Any]:
    targets = []
    for private in suite["targets_private"]:
        assignment = private["blind_assignment_private"]
        audio = private["audio_private"]
        item = {
            key: value
            for key, value in private.items()
            if not key.endswith("_private")
        }
        item["audio"] = {
            "target": f"/audio/{audio['target']}",
            "start": f"/audio/{audio['start']}",
            "candidate_a": f"/audio/{audio[assignment['candidate_a']]}",
            "candidate_b": f"/audio/{audio[assignment['candidate_b']]}",
        }
        if reveal:
            item["target_parameters"] = private["target_parameters_private"]
            item["blind_assignment"] = assignment
        targets.append(item)
    return {
        "schema": suite["schema"],
        "procedure": suite["procedure"],
        "aggregate": suite["aggregate"],
        "revealed": reveal,
        "targets": targets,
    }


def compact_suite_evidence(suite: Mapping[str, Any]) -> dict[str, Any]:
    """Strip private local paths while retaining exact scientific outcomes."""

    public = public_suite(suite, reveal=True)
    for target in public["targets"]:
        target.pop("audio", None)
    return public


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Milestone 8B validation suite")
    parser.add_argument("--state", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--renderer", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    suite = run_validation_suite(
        canonical_state_path=args.state,
        plugin_path=args.plugin,
        renderer_path=args.renderer,
        output_directory=args.output,
    )
    evidence = compact_suite_evidence(suite)
    _write_json_atomic(evidence, Path(args.output) / "compact-evidence.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

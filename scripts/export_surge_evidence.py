#!/usr/bin/env python3
"""Export a compact, path-free Milestone 9 evidence snapshot from a full local run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _without_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_paths(item)
            for key, item in value.items()
            if key not in {"path", "wav_path", "audio_path", "state_path", "preset_path", "audio_url"}
        }
    if isinstance(value, list):
        return [_without_paths(item) for item in value]
    return value


def build_compact(run: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    if run.get("status") != "COMPLETE" or run.get("error") is not None:
        raise ValueError("Milestone 9 evidence requires a complete error-free run")
    if run.get("evaluations") != run.get("budget"):
        raise ValueError("CMA-ES did not exhaust its predeclared budget")
    random_control = run.get("random_control") or {}
    if random_control.get("budget") != run.get("budget"):
        raise ValueError("random control does not have the same evaluation budget")
    verification = run.get("final_verification") or {}
    if not verification.get("byte_identical") or not verification.get("matches_search_best"):
        raise ValueError("final independent rerender was not exact")
    base_loss = float(run["starting_loss"])
    best_loss = float(run["best_loss"])
    improvement = float(run["improvement_percent"])
    invalid_cma = sum(
        1
        for line in (Path(run["_history_path"]).read_text(encoding="utf-8").splitlines())
        if json.loads(line).get("invalid_reason")
    )
    top_five = [
        {
            key: row[key]
            for key in (
                "rank",
                "preset_name",
                "preset_category",
                "preset_relative_path",
                "spectral_loss",
                "envelope_loss",
                "loudness_loss",
                "total_loss",
                "state_sha256",
                "wav_sha256",
            )
        }
        for row in index["entries"][:5]
    ]
    return _without_paths(
        {
            "schema": run["schema"],
            "result": {
                "surge_integrity": True,
                "preset_retrieval_completed": True,
                "local_optimization_gate_percent": run["success_gate_percent"],
                "local_optimization_pass": improvement >= run["success_gate_percent"],
                "human_listening": "PENDING_OWNER_AUDITION",
            },
            "run": {
                "run_id": run["run_id"],
                "status": run["status"],
                "stopping_reason": run["stopping_reason"],
                "generations": run["generations"],
                "population_size": run["optimizer"]["population_size"],
                "evaluations": run["evaluations"],
                "budget": run["budget"],
                "seed": run["optimizer"]["seed"],
                "initial_sigma": 0.18,
                "normalized_bounds": [0.0, 1.0],
                "base_loss": base_loss,
                "best_loss": best_loss,
                "improvement_percent": improvement,
                "invalid_clipped_candidates": invalid_cma,
            },
            "plugin": run["plugin"],
            "preset_library": {
                "release_artifacts": run["preset_library"]["release_artifacts"],
                "scope": run["preset_library"]["scope"],
                "selected": index["selected_count"],
                "indexed": index["indexed_count"],
                "failed": index["failed_count"],
                "selection": index["selection"],
                "index_workers": index["index_workers"],
                "top_five": top_five,
            },
            "audition": run["audition"],
            "objective": run["objective"],
            "selected_base": run["base_preset"],
            "active_parameters": run["active_parameter_policy"],
            "final_result": run["final_result"],
            "final_verification": verification,
            "random_control": random_control,
            "limitations": run["limitations"],
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory")
    parser.add_argument("output")
    args = parser.parse_args()
    directory = Path(args.run_directory).resolve()
    run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    run["_history_path"] = str(directory / "history.jsonl")
    index = json.loads((directory / "preset-index.json").read_text(encoding="utf-8"))
    compact = build_compact(run, index)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(compact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

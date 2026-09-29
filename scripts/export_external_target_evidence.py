#!/usr/bin/env python3
"""Export compact, reviewable evidence from a real external-target validation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_synth_twin.external_targets import ExternalTargetBank


REPRESENTATIVE_TARGETS = (
    "analog-sub-bass",
    "plucked-electric-guitar",
    "rhodes-style-electric-piano",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def latest_run(target_directory: Path, *, complete: bool = False) -> dict[str, Any]:
    candidates = []
    for path in target_directory.glob("run-*/run.json"):
        value = load_json(path)
        if complete and value.get("status") != "COMPLETE":
            continue
        candidates.append((str(value.get("created_at", "")), path, value))
    if not candidates:
        qualifier = " complete" if complete else ""
        raise RuntimeError(f"no{qualifier} run found under {target_directory}")
    selected = max(candidates, key=lambda item: item[0])
    selected[2]["_run_json_path"] = str(selected[1])
    return selected[2]


def ranking_record(run: dict[str, Any]) -> dict[str, Any]:
    target = run["target"]
    ranking = run["preset_ranking"]
    index = load_json(Path(run["_run_json_path"]).parent / "preset-index.json")
    hashes = {
        row["preset_relative_path"]: row["wav_sha256"] for row in index["entries"]
    }
    if len(ranking) < 5:
        raise RuntimeError(f"target has fewer than five ranked presets: {target['target_id']}")
    return {
        "target_id": target["target_id"],
        "title": target["title"],
        "file_name": target["file_name"],
        "target_wav_sha256": target["sha256"],
        "base_preset": run["base_preset"],
        "top_five": [
            {
                key: row[key]
                for key in (
                    "rank",
                    "preset_name",
                    "preset_category",
                    "preset_relative_path",
                    "total_loss",
                    "spectral_loss",
                    "envelope_loss",
                    "loudness_loss",
                )
            } | {"wav_sha256": hashes[row["preset_relative_path"]]}
            for row in ranking[:5]
        ],
    }


def search_record(run: dict[str, Any]) -> dict[str, Any]:
    verification = run["final_verification"]
    best = run["best"]
    return {
        "target_id": run["target"]["target_id"],
        "status": run["status"],
        "run_id": run["run_id"],
        "evaluations": run["evaluations"],
        "starting_loss": run["starting_loss"],
        "best_loss": run["best_loss"],
        "improvement_percent": run["improvement_percent"],
        "best_parameter_values_normalized": best["parameter_values_normalized"],
        "best_parameter_values_real": best["parameter_values_real"],
        "best_wav_sha256": best["wav_sha256"],
        "clipped_samples": best["clipped_samples"],
        "final_rerender": {
            "wav_sha256": verification["wav_sha256"],
            "search_best_sha256": verification["search_best_sha256"],
            "matches_search_best": verification["matches_search_best"],
            "byte_identical": verification["byte_identical"],
            "verification_renders": verification["verification_renders"],
        },
        "human_listening": "PENDING_OWNER_AUDITION",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    bank = ExternalTargetBank(args.targets)
    rankings = []
    cache_ids = set()
    for target in bank.targets:
        run = latest_run(args.runs / "targets" / target.target_id)
        if run["target"]["sha256"] != target.sha256:
            raise RuntimeError(f"run target hash differs: {target.target_id}")
        cache_ids.add(run["preset_cache"]["identity_sha256"])
        rankings.append(ranking_record(run))
    if len(cache_ids) != 1:
        raise RuntimeError("target rankings do not share one preset-cache identity")
    cache_id = next(iter(cache_ids))
    cache_file = args.runs / "shared-preset-cache" / cache_id[:20] / "cache.json"
    cache = load_json(cache_file)
    searches = [
        search_record(latest_run(args.runs / "targets" / target_id, complete=True))
        for target_id in REPRESENTATIVE_TARGETS
    ]
    if not all(item["final_rerender"]["matches_search_best"] for item in searches):
        raise RuntimeError("a representative final rerender differs from its search best")

    evidence = {
        "schema": "agentic-synth-twin/external-target-validation/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Automated similarity is a transparent fixed one-note proxy; "
            "human perceptual acceptance remains pending."
        ),
        "target_bank": {
            "identity_sha256": bank.identity_sha256,
            "count": len(bank.targets),
            "surge_used": False,
            "third_party_samples_used": False,
            "preset_library_used": False,
        },
        "shared_preset_cache": {
            "identity_sha256": cache_id,
            "selected_count": cache["selected_count"],
            "eligible_indexed_count": cache["indexed_count"],
            "ineligible_or_failed_count": cache["failed_count"],
            "plugin": cache["synth_plugin"],
            "reused_for_all_targets": True,
        },
        "all_target_rankings": rankings,
        "representative_full_searches": searches,
        "human_acceptance": "PENDING_OWNER_AUDITION",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "target_rankings": len(rankings),
        "full_searches": len(searches),
        "cache_identity_sha256": cache_id,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Export compact evidence for the full-library perceptual retrieval audit."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_synth_twin.audition import _write_json_atomic
from agentic_synth_twin.external_targets import ExternalTargetBank
from agentic_synth_twin.synth_adapter import sha256_file


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def latest_run(directory: Path) -> tuple[Path, dict[str, Any]]:
    rows = []
    for path in directory.glob("run-*/run.json"):
        payload = load(path)
        rows.append((str(payload.get("created_at", "")), path, payload))
    if not rows:
        raise RuntimeError(f"no run evidence under {directory}")
    _, path, payload = max(rows, key=lambda item: item[0])
    return path, payload


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "rank",
        "perceptual_rank",
        "legacy_rank",
        "preset_name",
        "preset_category",
        "preset_relative_path",
        "retrieval_score",
        "total_loss",
        "retrieval_eligible",
        "search_eligible",
        "raw_distances",
        "normalized_distances",
        "weighted_contributions",
        "wav_sha256",
        "state_sha256",
    )
    return {key: row[key] for key in keys if key in row}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--sanity", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bank = ExternalTargetBank(args.targets)
    results = []
    cache_ids = set()
    for target in bank.targets:
        path, run = latest_run(args.runs / "targets" / target.target_id)
        if run["target"]["sha256"] != target.sha256:
            raise RuntimeError(f"target hash mismatch: {target.target_id}")
        index = load(path.parent / "preset-index.json")
        cache_ids.add(index["preset_cache_identity_sha256"])
        perceptual = index["perceptual_top_10"]
        legacy = index["legacy_top_10"]
        if len(perceptual) != 10 or len(legacy) != 10:
            raise RuntimeError(f"incomplete Top 10: {target.target_id}")
        results.append(
            {
                "target": target.public_record(),
                "coverage": index["retrieval_coverage"],
                "family_medians": index["family_medians"],
                "cma_base_perceptual_rank": index["cma_base_perceptual_rank"],
                "legacy_top_10": [compact_row(row) for row in legacy],
                "perceptual_top_10": [compact_row(row) for row in perceptual],
            }
        )
    if len(cache_ids) != 1:
        raise RuntimeError("targets do not share one cache identity")
    cache_id = next(iter(cache_ids))
    cache_path = args.runs / "shared-preset-cache" / cache_id[:20] / "cache.json"
    cache = load(cache_path)
    sanity = load(args.sanity)
    if sanity["cache_identity_sha256"] != cache_id:
        raise RuntimeError("sanity evidence uses a different cache identity")
    human_path = args.runs / "human-retrieval-audit.json"
    human = load(human_path) if human_path.is_file() else {"targets": {}}
    human_records = {
        target.target_id: human.get("targets", {}).get(
            target.target_id,
            {"comparison": "PENDING", "plausible_neighborhood": "UNCLEAR"},
        )
        for target in bank.targets
    }
    evidence = {
        "schema": "agentic-synth-twin/full-library-retrieval-audit/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Automated ranks compare two fixed signal representations; only recorded human "
            "listening can establish whether either acoustic neighborhood is useful."
        ),
        "cache": {
            "identity_sha256": cache_id,
            "cache_json_sha256": sha256_file(cache_path),
            "factory_total": cache["selected_count"],
            "retrieval_eligible": cache["retrieval_eligible_count"],
            "search_eligible": cache["search_eligible_count"],
            "failed": cache["failed_count"],
            "synth_plugin": cache["synth_plugin"],
            "descriptor": cache["descriptor"],
        },
        "targets": results,
        "sanity": sanity,
        "human_audit": human_records,
        "human_acceptance_complete": all(
            row["comparison"] != "PENDING" for row in human_records.values()
        ),
    }
    _write_json_atomic(evidence, args.output.resolve())
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "targets": len(results),
                "cache_identity_sha256": cache_id,
                "human_acceptance_complete": evidence["human_acceptance_complete"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Combine three completed local runs into one path-sanitized evidence artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "agentic-synth-twin/perceptual-multistart-cross-target-evidence/v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sanitize(value: Any, repository_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: sanitize(item, repository_root) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, repository_root) for item in value]
    if isinstance(value, str):
        prefix = str(repository_root) + "/"
        if value.startswith(prefix):
            return "${REPOSITORY_ROOT}/" + value[len(prefix) :]
    return value


def load_run(path: Path, repository_root: Path) -> dict[str, Any]:
    evidence_path = path / "multistart.json" if path.is_dir() else path
    run_directory = evidence_path.parent
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    if payload.get("stage") != "COMPLETE":
        raise ValueError(f"run is not COMPLETE: {evidence_path}")
    stable = payload.get("verified_stable_best")
    numeric = payload.get("numeric_best")
    target_id = evidence_path.parent.parent.name
    preset_index_path = run_directory / "preset-index.json"
    run_path = run_directory / "run.json"
    preset_index = json.loads(preset_index_path.read_text(encoding="utf-8"))
    run = json.loads(run_path.read_text(encoding="utf-8"))
    return {
        "target_id": target_id,
        "source_evidence_sha256": sha256_file(evidence_path),
        "source_preset_index_sha256": sha256_file(preset_index_path),
        "source_run_sha256": sha256_file(run_path),
        "target": sanitize(run["external_target"], repository_root),
        "perceptual_top_10": sanitize(
            preset_index["perceptual_top_10"], repository_root
        ),
        "summary": {
            "attempted_start_ranks": payload["attempted_start_ranks"],
            "valid_start_count": len(payload["starts_full"]),
            "skipped_start_count": len(payload["skipped_starts_full"]),
            "cma_evaluations": len(payload["history"]),
            "stage_b_survivors": payload["stage_b_survivors"],
            "numeric_best_score": (
                numeric["retrieval_score"] if numeric is not None else None
            ),
            "verified_stable_score": (
                stable["retrieval_score"] if stable is not None else None
            ),
            "verified_stable_start": (
                stable["optimization_start_index"] if stable is not None else None
            ),
            "stable_verification_succeeded": stable is not None,
            "verification_attempt_count": len(payload["verification_attempts"]),
            "human_judgment": payload["human_judgment"],
        },
        "full_evidence": sanitize(payload, repository_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rhodes", required=True, type=Path)
    parser.add_argument("--guitar", required=True, type=Path)
    parser.add_argument("--bass", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache-index", required=True, type=Path)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    runs = [
        load_run(path.resolve(), repository_root)
        for path in (args.rhodes, args.guitar, args.bass)
    ]
    expected = [
        "rhodes-style-electric-piano",
        "plucked-electric-guitar",
        "analog-sub-bass",
    ]
    if [run["target_id"] for run in runs] != expected:
        raise ValueError("run target order or identity does not match Rhodes/guitar/bass")
    configurations = [run["full_evidence"]["configuration"] for run in runs]
    if configurations[1:] != configurations[:-1]:
        raise ValueError("screen or optimizer configuration changed across targets")
    weights = [run["full_evidence"]["frozen_family_weights"] for run in runs]
    if weights[1:] != weights[:-1]:
        raise ValueError("perceptual family weights changed across targets")
    cache_path = args.cache_index.resolve()
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache_identity = cache["identity"]
    artifact = {
        "schema": SCHEMA,
        "repository": "https://github.com/jeremybboy/agentic-synth-twin",
        "base_commit": args.base_commit,
        "branch": args.branch,
        "pull_request": "PENDING_AT_EVIDENCE_GENERATION",
        "preset_cache": {
            "source_sha256": sha256_file(cache_path),
            "identity_sha256": cache["identity_sha256"],
            "identity": {
                key: cache_identity[key]
                for key in (
                    "schema",
                    "plugin_bundle",
                    "plugin_binary_sha256",
                    "preset_limit",
                    "full_library",
                    "descriptor_version",
                )
            },
            "factory_content_file_count": len(cache_identity["factory_content"]),
            "synth_plugin": cache["synth_plugin"],
        },
        "descriptor_version": cache_identity["descriptor_version"],
        "claim_boundary": (
            "Automated real-render evidence only; timbre quality and musical utility "
            "remain pending owner audition."
        ),
        "configuration_unchanged_across_targets": True,
        "frozen_configuration": configurations[0],
        "frozen_family_weights": weights[0],
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the mandatory self/near-state sanity audit without changing weights."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agentic_synth_twin.audition import MIDI_KEY, NOTE_FRAMES, VELOCITY, _write_json_atomic
from agentic_synth_twin.perceptual_retrieval import (
    DESCRIPTOR_VERSION,
    PerceptualDescriptor,
    describe_audio,
    load_descriptor,
    normalize_and_rank,
    raw_family_distances,
)
from agentic_synth_twin.surge_match import (
    DETERMINISTIC_OVERRIDES,
    discover_active_parameters,
)
from agentic_synth_twin.synth_adapter import SurgeXTAdapter, sha256_file


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def rank_query(
    query: PerceptualDescriptor, entries: list[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for entry in entries:
        candidate = load_descriptor(entry["descriptor_path"])
        rows.append(
            {
                "preset_relative_path": entry["preset_relative_path"],
                "raw_distances": raw_family_distances(query, candidate),
            }
        )
    return normalize_and_rank(rows)[0]


def broad_sample(entries: list[Mapping[str, Any]], count: int) -> list[Mapping[str, Any]]:
    selected = []
    categories = set()
    for entry in entries:
        category = str(entry["preset_category"])
        if category not in categories:
            selected.append(entry)
            categories.add(category)
            if len(selected) == count:
                return selected
    remaining = [entry for entry in entries if entry not in selected]
    if remaining and len(selected) < count:
        step = max(1, len(remaining) // (count - len(selected)))
        selected.extend(remaining[::step][: count - len(selected)])
    return selected


def audit_rank(
    *, query: PerceptualDescriptor, source_path: str, entries: list[Mapping[str, Any]]
) -> dict[str, Any]:
    ranking = rank_query(query, entries)
    source = next(row for row in ranking if row["preset_relative_path"] == source_path)
    best_score = float(ranking[0]["retrieval_score"])
    top_ties = sum(
        abs(float(row["retrieval_score"]) - best_score) <= 1e-12 for row in ranking
    )
    return {
        "source_preset": source_path,
        "source_rank": source["perceptual_rank"],
        "source_score": source["retrieval_score"],
        "best_score": best_score,
        "top_score_tie_count": top_ties,
        "top_10": [
            {
                "rank": row["perceptual_rank"],
                "preset_relative_path": row["preset_relative_path"],
                "retrieval_score": row["retrieval_score"],
            }
            for row in ranking[:10]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--renderer", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--self-samples", type=int, default=12)
    parser.add_argument("--near-state-samples", type=int, default=4)
    args = parser.parse_args()
    cache = load_json(args.cache.resolve())
    if cache["identity"]["descriptor_version"] != DESCRIPTOR_VERSION:
        raise RuntimeError("cache descriptor version differs from audit implementation")
    entries = list(cache["entries"])
    self_records = []
    for number, entry in enumerate(broad_sample(entries, args.self_samples), start=1):
        print(f"self retrieval {number}/{args.self_samples}: {entry['preset_relative_path']}", flush=True)
        self_records.append(
            audit_rank(
                query=describe_audio(entry["audio_path"]),
                source_path=entry["preset_relative_path"],
                entries=entries,
            )
        )

    adapter = SurgeXTAdapter(
        plugin_path=args.plugin,
        factory_data_path=args.data,
        renderer_path=args.renderer,
        probe_path=args.probe,
        deterministic_overrides=DETERMINISTIC_OVERRIDES,
    )
    search_entries = [entry for entry in entries if entry["search_eligible"]]
    near_records = []
    near_directory = args.output.resolve().parent / "near-state-audio"
    near_directory.mkdir(parents=True, exist_ok=True)
    for number, entry in enumerate(
        broad_sample(search_entries, args.near_state_samples), start=1
    ):
        print(
            f"near-state retrieval {number}/{args.near_state_samples}: "
            f"{entry['preset_relative_path']}",
            flush=True,
        )
        parameters = discover_active_parameters(adapter.inspect_state(entry["state_path"]))
        parameter = next(item for item in parameters if item["name"] == "A Filter 1 Cutoff")
        span = float(parameter["max"]) - float(parameter["min"])
        current = float(parameter["current"])
        delta = 0.02 * span
        changed = current + delta if current + delta <= float(parameter["max"]) else current - delta
        wav_path = near_directory / f"near-{number:02d}.wav"
        rendered = adapter.render_verified(
            state_path=entry["state_path"],
            wav_path=wav_path,
            parameter_values={parameter["id"]: changed},
            midi_key=MIDI_KEY,
            velocity=VELOCITY,
            note_frames=NOTE_FRAMES,
        )
        record = audit_rank(
            query=describe_audio(wav_path),
            source_path=entry["preset_relative_path"],
            entries=entries,
        )
        record.update(
            {
                "changed_parameter": {
                    "id": parameter["id"],
                    "name": parameter["name"],
                    "from": current,
                    "to": changed,
                },
                "query_wav_sha256": rendered["wav_sha256"],
                "deterministic": rendered["byte_identical"],
                "source_in_top_10": record["source_rank"] <= 10,
            }
        )
        near_records.append(record)

    payload = {
        "schema": "agentic-synth-twin/perceptual-retrieval-sanity/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_identity_sha256": cache["identity_sha256"],
        "cache_sha256": sha256_file(args.cache),
        "descriptor_version": DESCRIPTOR_VERSION,
        "self_retrieval": {
            "sample_count": len(self_records),
            "selection": "first lexicographic preset from distinct categories, then deterministic spread",
            "records": self_records,
            "all_source_scores_approximately_zero": all(
                row["source_score"] <= 0.01 for row in self_records
            ),
            "all_sources_rank_one_or_top_tied": all(
                row["source_rank"] <= row["top_score_tie_count"]
                for row in self_records
            ),
        },
        "near_state": {
            "sample_count": len(near_records),
            "parameter_change": "two percent of discovered A Filter 1 Cutoff range; no retuning",
            "records": near_records,
            "all_sources_in_top_10": all(row["source_in_top_10"] for row in near_records),
        },
        "synthetic_directionality": "covered by tests/test_perceptual_retrieval.py",
        "weights_changed_after_audit": False,
    }
    _write_json_atomic(payload, args.output.resolve())
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Validated canonical external-audio targets for the Milestone 9 cockpit."""

from __future__ import annotations

import argparse
import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


EXPECTED_EXTERNAL_TARGETS = (
    ("analog-sub-bass", "Analog Sub Bass", "01_analog_sub_bass_C3.wav"),
    ("fm-bell", "FM Bell", "02_fm_bell_C3.wav"),
    (
        "plucked-electric-guitar",
        "Plucked Electric Guitar",
        "03_plucked_electric_guitar_C3.wav",
    ),
    (
        "rhodes-style-electric-piano",
        "Rhodes-style Electric Piano",
        "04_rhodes_style_electric_piano_C3.wav",
    ),
    ("muted-synth-pluck", "Muted Synth Pluck", "05_muted_synth_pluck_C3.wav"),
    ("analog-brass-stab", "Analog Brass Stab", "06_analog_brass_stab_C3.wav"),
    ("warm-poly-pad", "Warm Poly Pad", "07_warm_poly_pad_C3.wav"),
    ("sync-hard-lead", "Sync / Hard Lead", "08_sync_hard_lead_C3.wav"),
    ("marimba-mallet", "Marimba / Mallet", "09_marimba_mallet_C3.wav"),
    (
        "dub-chord-organ-stab",
        "Dub Chord / Organ Stab",
        "10_dub_chord_organ_stab_C3.wav",
    ),
)


class ExternalTargetError(RuntimeError):
    """Raised when the fixed external target pack violates its contract."""


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class ExternalTarget:
    target_id: str
    title: str
    file_name: str
    path: Path
    sha256: str
    byte_count: int
    recipe: str

    def public_record(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "title": self.title,
            "file_name": self.file_name,
            "sha256": self.sha256,
        }


class ExternalTargetBank:
    """Load and fully validate the ten immutable external reference WAVs."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).resolve()
        self.manifest_path = self.directory / "manifest.json"
        if not self.manifest_path.is_file():
            raise ExternalTargetError("external target manifest is missing")
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ExternalTargetError("external target manifest is invalid JSON") from error
        if not isinstance(manifest, Mapping):
            raise ExternalTargetError("external target manifest must be an object")
        self.manifest = dict(manifest)
        self._validate_provenance()
        self._validate_audition_contract()
        self.targets = self._load_targets()
        self.by_id = {target.target_id: target for target in self.targets}
        identity_payload = {
            "manifest_sha256": sha256_file(self.manifest_path),
            "targets": [target.public_record() for target in self.targets],
        }
        self.identity_sha256 = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _validate_provenance(self) -> None:
        generation = self.manifest.get("generation")
        if not isinstance(generation, Mapping):
            raise ExternalTargetError("external target generation metadata is missing")
        for key in ("surge_used", "third_party_samples_used", "preset_library_used"):
            if generation.get(key) is not False:
                raise ExternalTargetError(f"external target provenance flag must be false: {key}")

    def _validate_audition_contract(self) -> None:
        contract = self.manifest.get("audition_contract")
        expected = {
            "nominal_pitch": "C3",
            "sample_rate_hz": 44_100,
            "channels": 2,
            "bit_depth": 16,
            "duration_seconds": 2.5,
            "reference_hold_seconds": 2.0,
            "reference_tail_seconds": 0.5,
        }
        if not isinstance(contract, Mapping):
            raise ExternalTargetError("external target audition contract is missing")
        for key, value in expected.items():
            if contract.get(key) != value:
                raise ExternalTargetError(f"external target audition contract mismatch: {key}")

    def _load_targets(self) -> tuple[ExternalTarget, ...]:
        rows = self.manifest.get("targets")
        if not isinstance(rows, list) or len(rows) != len(EXPECTED_EXTERNAL_TARGETS):
            raise ExternalTargetError("external target manifest must contain exactly ten targets")
        targets: list[ExternalTarget] = []
        seen_ids: set[str] = set()
        seen_paths: set[Path] = set()
        for row, expected in zip(rows, EXPECTED_EXTERNAL_TARGETS, strict=True):
            if not isinstance(row, Mapping):
                raise ExternalTargetError("external target entry must be an object")
            expected_id, expected_title, expected_file = expected
            target_id = str(row.get("id", expected_id))
            title = str(row.get("title", ""))
            file_name = str(row.get("file", ""))
            if target_id != expected_id or title != expected_title or file_name != expected_file:
                raise ExternalTargetError("external target identity or stable ordering changed")
            if target_id in seen_ids:
                raise ExternalTargetError(f"duplicate external target id: {target_id}")
            candidate = (self.directory / file_name).resolve()
            if self.directory not in candidate.parents:
                raise ExternalTargetError("external target path escapes the target directory")
            if candidate in seen_paths:
                raise ExternalTargetError(f"duplicate external target file: {file_name}")
            if not candidate.is_file():
                raise ExternalTargetError(f"external target WAV is missing: {file_name}")
            expected_bytes = row.get("bytes")
            if not isinstance(expected_bytes, int) or candidate.stat().st_size != expected_bytes:
                raise ExternalTargetError(f"external target byte count differs: {file_name}")
            expected_hash = str(row.get("sha256", ""))
            if len(expected_hash) != 64 or sha256_file(candidate) != expected_hash:
                raise ExternalTargetError(f"external target SHA-256 differs: {file_name}")
            self._validate_wav(candidate)
            targets.append(
                ExternalTarget(
                    target_id=target_id,
                    title=title,
                    file_name=file_name,
                    path=candidate,
                    sha256=expected_hash,
                    byte_count=expected_bytes,
                    recipe=str(row.get("recipe", "")),
                )
            )
            seen_ids.add(target_id)
            seen_paths.add(candidate)
        return tuple(targets)

    @staticmethod
    def _validate_wav(path: Path) -> None:
        try:
            with wave.open(str(path), "rb") as source:
                actual = (
                    source.getframerate(),
                    source.getnchannels(),
                    source.getsampwidth(),
                    source.getnframes(),
                    source.getcomptype(),
                )
        except (OSError, wave.Error) as error:
            raise ExternalTargetError(f"external target WAV is invalid: {path.name}") from error
        if actual != (44_100, 2, 2, 110_250, "NONE"):
            raise ExternalTargetError(f"external target WAV format differs: {path.name}")

    def target(self, target_id: str) -> ExternalTarget:
        try:
            return self.by_id[target_id]
        except KeyError as error:
            raise ExternalTargetError(f"unknown external target id: {target_id}") from error

    def public_records(self) -> list[dict[str, Any]]:
        return [target.public_record() for target in self.targets]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the fixed external target bank")
    parser.add_argument("directory")
    args = parser.parse_args()
    bank = ExternalTargetBank(args.directory)
    print(
        json.dumps(
            {
                "target_count": len(bank.targets),
                "identity_sha256": bank.identity_sha256,
                "targets": bank.public_records(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

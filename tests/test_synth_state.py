"""Behavioral tests for the canonical synth-state contract."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agentic_synth_twin.synth_state import (
    SCHEMA_ID,
    StateValidationError,
    canonicalize_probe,
    get_parameter,
    list_parameters,
    load_canonical_state,
    validate_canonical_state,
    write_canonical_state,
)


ROOT = Path(__file__).resolve().parents[1]
RAW_EVIDENCE = ROOT / "docs" / "evidence" / "milestone-2-clap-probe.json"
CANONICAL_EVIDENCE = (
    ROOT / "docs" / "evidence" / "milestone-2-canonical-state.json"
)
JSON_SCHEMA = ROOT / "docs" / "schemas" / "canonical-synth-state-v1.schema.json"
UPSTREAM_COMMIT = "f33b31fff459d66ac18207ec152b323aaa9306f9"


class SynthStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = json.loads(RAW_EVIDENCE.read_text(encoding="utf-8"))
        cls.canonical = json.loads(CANONICAL_EVIDENCE.read_text(encoding="utf-8"))

    def test_committed_state_is_derived_from_real_probe(self) -> None:
        self.assertEqual(self.probe["upstream_commit"], UPSTREAM_COMMIT)
        derived = canonicalize_probe(
            self.probe, upstream_commit=UPSTREAM_COMMIT
        )
        self.assertEqual(derived, self.canonical)

    def test_committed_state_validates(self) -> None:
        validate_canonical_state(self.canonical)
        self.assertEqual(self.canonical["evidence"]["parameter_count"], 10)
        self.assertTrue(
            self.canonical["evidence"]["selected_parameter_restore_verified"]
        )
        self.assertEqual(self.canonical["opaque_state"]["byte_length"], 97)

    def test_json_schema_identifies_the_same_contract(self) -> None:
        schema = json.loads(JSON_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema"]["const"], SCHEMA_ID)
        self.assertFalse(schema["additionalProperties"])

    def test_names_are_preserved_and_units_are_not_invented(self) -> None:
        raw_names = [parameter["name"] for parameter in self.probe["parameters"]]
        canonical_names = [
            parameter["name"] for parameter in self.canonical["parameters"]
        ]
        self.assertEqual(canonical_names, raw_names)
        self.assertTrue(
            all(parameter["unit"] is None for parameter in self.canonical["parameters"])
        )

    def test_blob_tampering_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.canonical)
        tampered["opaque_state"]["data"] = "AA=="
        with self.assertRaisesRegex(StateValidationError, "byte_length"):
            validate_canonical_state(tampered)

    def test_blob_digest_tampering_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.canonical)
        tampered["opaque_state"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(StateValidationError, "sha256"):
            validate_canonical_state(tampered)

    def test_unverified_probe_restore_is_rejected(self) -> None:
        unverified = copy.deepcopy(self.probe)
        unverified["mutation"]["restore_verified"] = False
        with self.assertRaisesRegex(StateValidationError, "did not verify"):
            canonicalize_probe(unverified, upstream_commit=UPSTREAM_COMMIT)

    def test_duplicate_parameter_id_is_rejected(self) -> None:
        duplicated = copy.deepcopy(self.canonical)
        duplicated["parameters"][1]["id"] = duplicated["parameters"][0]["id"]
        with self.assertRaisesRegex(StateValidationError, "duplicate parameter id"):
            validate_canonical_state(duplicated)

    def test_out_of_range_current_value_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.canonical)
        invalid["parameters"][0]["current"] = 8
        with self.assertRaisesRegex(StateValidationError, "outside min/max"):
            validate_canonical_state(invalid)

    def test_schema_drift_is_rejected(self) -> None:
        drifted = copy.deepcopy(self.canonical)
        drifted["unversioned_extension"] = True
        with self.assertRaisesRegex(StateValidationError, "unexpected"):
            validate_canonical_state(drifted)

    def test_parameter_access_is_by_real_clap_id_and_defensive(self) -> None:
        parameters = list_parameters(self.canonical)
        self.assertEqual(get_parameter(self.canonical, 17)["name"], "Cutoff in Keys")
        parameters[0]["name"] = "changed locally"
        self.assertEqual(self.canonical["parameters"][0]["name"], "Unison Count")
        with self.assertRaises(KeyError):
            get_parameter(self.canonical, 999999999)

    def test_write_then_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_canonical_state(self.canonical, path)
            self.assertEqual(load_canonical_state(path), self.canonical)


if __name__ == "__main__":
    unittest.main()

"""Canonical, validated state for one pinned CLAP synthesizer."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_ID = "agentic-synth-twin/canonical-synth-state/v1"
SOURCE_REPOSITORY = "https://github.com/abique/clap-saw-demo"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class StateValidationError(ValueError):
    """Raised when probe or canonical state data violates the contract."""


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateValidationError(f"{path} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], path: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise StateValidationError(
            f"{path} keys do not match contract; "
            f"missing={missing}, unexpected={unexpected}"
        )


def _require_sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise StateValidationError(f"{path} must be an array")
    return value


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise StateValidationError(f"{path} must be a non-empty string")
    return value


def _require_integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StateValidationError(f"{path} must be an integer >= {minimum}")
    return value


def _require_number(value: Any, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateValidationError(f"{path} must be a number")
    if not math.isfinite(value):
        raise StateValidationError(f"{path} must be finite")
    return value


def _require_boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise StateValidationError(f"{path} must be a boolean")
    return value


def _decode_state(data: str, path: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise StateValidationError(f"{path} must be valid base64") from error


def canonicalize_probe(
    probe: Mapping[str, Any], *, upstream_commit: str
) -> dict[str, Any]:
    """Convert direct CLAP probe output into the versioned canonical contract."""

    probe = _require_mapping(probe, "probe")
    if not COMMIT_PATTERN.fullmatch(upstream_commit):
        raise StateValidationError("upstream_commit must be a 40-character lowercase SHA")

    raw_plugin = _require_mapping(probe.get("plugin"), "probe.plugin")
    plugin = {
        "clap_id": _require_string(raw_plugin.get("id"), "probe.plugin.id"),
        "name": _require_string(raw_plugin.get("name"), "probe.plugin.name"),
        "vendor": _require_string(raw_plugin.get("vendor"), "probe.plugin.vendor"),
        "version": _require_string(raw_plugin.get("version"), "probe.plugin.version"),
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": upstream_commit,
    }

    raw_parameters = _require_sequence(probe.get("parameters"), "probe.parameters")
    reported_count = _require_integer(
        probe.get("parameter_count"), "probe.parameter_count"
    )
    if reported_count != len(raw_parameters):
        raise StateValidationError("probe.parameter_count does not match parameters")

    parameters: list[dict[str, Any]] = []
    for index, value in enumerate(raw_parameters):
        raw = _require_mapping(value, f"probe.parameters[{index}]")
        module = raw.get("module")
        if module == "":
            module = None
        elif module is not None:
            module = _require_string(module, f"probe.parameters[{index}].module")
        parameters.append(
            {
                "id": _require_integer(raw.get("id"), f"probe.parameters[{index}].id"),
                "name": _require_string(
                    raw.get("name"), f"probe.parameters[{index}].name"
                ),
                "module": module,
                "unit": None,
                "min": _require_number(
                    raw.get("min"), f"probe.parameters[{index}].min"
                ),
                "max": _require_number(
                    raw.get("max"), f"probe.parameters[{index}].max"
                ),
                "default": _require_number(
                    raw.get("default"), f"probe.parameters[{index}].default"
                ),
                "current": _require_number(
                    raw.get("current"), f"probe.parameters[{index}].current"
                ),
                "flags": _require_integer(
                    raw.get("flags"), f"probe.parameters[{index}].flags"
                ),
                "automatable": _require_boolean(
                    raw.get("automatable"),
                    f"probe.parameters[{index}].automatable",
                ),
                "modulatable": _require_boolean(
                    raw.get("modulatable"),
                    f"probe.parameters[{index}].modulatable",
                ),
            }
        )

    encoded_state = _require_string(probe.get("state_base64"), "probe.state_base64")
    state_bytes = _decode_state(encoded_state, "probe.state_base64")
    reported_bytes = _require_integer(probe.get("state_bytes"), "probe.state_bytes")
    if reported_bytes != len(state_bytes):
        raise StateValidationError("probe.state_bytes does not match decoded state")
    mutation = _require_mapping(probe.get("mutation"), "probe.mutation")
    restore_verified = _require_boolean(
        mutation.get("restore_verified"), "probe.mutation.restore_verified"
    )
    if not restore_verified:
        raise StateValidationError("probe did not verify selected parameter restoration")

    canonical = {
        "schema": SCHEMA_ID,
        "plugin": plugin,
        "parameters": parameters,
        "opaque_state": {
            "encoding": "base64",
            "byte_length": len(state_bytes),
            "sha256": hashlib.sha256(state_bytes).hexdigest(),
            "data": encoded_state,
        },
        "evidence": {
            "capture_method": "clap.params+clap.state",
            "parameter_count": len(parameters),
            "selected_parameter_restore_verified": True,
        },
    }
    validate_canonical_state(canonical)
    return canonical


def validate_canonical_state(state: Mapping[str, Any]) -> None:
    """Validate structural and cross-field invariants for canonical state."""

    state = _require_mapping(state, "state")
    _require_exact_keys(
        state, {"schema", "plugin", "parameters", "opaque_state", "evidence"}, "state"
    )
    if state.get("schema") != SCHEMA_ID:
        raise StateValidationError(f"state.schema must equal {SCHEMA_ID!r}")

    plugin = _require_mapping(state.get("plugin"), "state.plugin")
    _require_exact_keys(
        plugin,
        {
            "clap_id",
            "name",
            "vendor",
            "version",
            "source_repository",
            "source_commit",
        },
        "state.plugin",
    )
    for key in ("clap_id", "name", "vendor", "version", "source_repository"):
        _require_string(plugin.get(key), f"state.plugin.{key}")
    commit = _require_string(plugin.get("source_commit"), "state.plugin.source_commit")
    if not COMMIT_PATTERN.fullmatch(commit):
        raise StateValidationError("state.plugin.source_commit must be a lowercase SHA")

    parameters = _require_sequence(state.get("parameters"), "state.parameters")
    if not parameters:
        raise StateValidationError("state.parameters must not be empty")
    parameter_ids: set[int] = set()
    for index, value in enumerate(parameters):
        path = f"state.parameters[{index}]"
        parameter = _require_mapping(value, path)
        _require_exact_keys(
            parameter,
            {
                "id",
                "name",
                "module",
                "unit",
                "min",
                "max",
                "default",
                "current",
                "flags",
                "automatable",
                "modulatable",
            },
            path,
        )
        parameter_id = _require_integer(parameter.get("id"), f"{path}.id")
        if parameter_id in parameter_ids:
            raise StateValidationError(f"duplicate parameter id {parameter_id}")
        parameter_ids.add(parameter_id)
        _require_string(parameter.get("name"), f"{path}.name")
        for optional_key in ("module", "unit"):
            optional_value = parameter.get(optional_key)
            if optional_value is not None:
                _require_string(optional_value, f"{path}.{optional_key}")
        minimum = _require_number(parameter.get("min"), f"{path}.min")
        maximum = _require_number(parameter.get("max"), f"{path}.max")
        default = _require_number(parameter.get("default"), f"{path}.default")
        current = _require_number(parameter.get("current"), f"{path}.current")
        if minimum > maximum:
            raise StateValidationError(f"{path}.min must be <= max")
        if not minimum <= default <= maximum:
            raise StateValidationError(f"{path}.default is outside min/max")
        if not minimum <= current <= maximum:
            raise StateValidationError(f"{path}.current is outside min/max")
        _require_integer(parameter.get("flags"), f"{path}.flags")
        _require_boolean(parameter.get("automatable"), f"{path}.automatable")
        _require_boolean(parameter.get("modulatable"), f"{path}.modulatable")

    opaque = _require_mapping(state.get("opaque_state"), "state.opaque_state")
    _require_exact_keys(
        opaque,
        {"encoding", "byte_length", "sha256", "data"},
        "state.opaque_state",
    )
    if opaque.get("encoding") != "base64":
        raise StateValidationError("state.opaque_state.encoding must equal 'base64'")
    encoded = _require_string(opaque.get("data"), "state.opaque_state.data")
    decoded = _decode_state(encoded, "state.opaque_state.data")
    byte_length = _require_integer(
        opaque.get("byte_length"), "state.opaque_state.byte_length", minimum=1
    )
    if byte_length != len(decoded):
        raise StateValidationError("state.opaque_state.byte_length does not match data")
    digest = _require_string(opaque.get("sha256"), "state.opaque_state.sha256")
    if digest != hashlib.sha256(decoded).hexdigest():
        raise StateValidationError("state.opaque_state.sha256 does not match data")

    evidence = _require_mapping(state.get("evidence"), "state.evidence")
    _require_exact_keys(
        evidence,
        {
            "capture_method",
            "parameter_count",
            "selected_parameter_restore_verified",
        },
        "state.evidence",
    )
    if evidence.get("capture_method") != "clap.params+clap.state":
        raise StateValidationError("state.evidence.capture_method is unsupported")
    count = _require_integer(
        evidence.get("parameter_count"), "state.evidence.parameter_count"
    )
    if count != len(parameters):
        raise StateValidationError("state.evidence.parameter_count does not match parameters")
    if not _require_boolean(
        evidence.get("selected_parameter_restore_verified"),
        "state.evidence.selected_parameter_restore_verified",
    ):
        raise StateValidationError("state evidence must verify selected parameter restore")


def load_canonical_state(path: str | Path) -> dict[str, Any]:
    """Load and validate canonical state JSON from disk."""

    state = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_canonical_state(state)
    return state


def write_canonical_state(state: Mapping[str, Any], path: str | Path) -> None:
    """Validate and atomically write canonical state JSON."""

    validate_canonical_state(state)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary_path = temporary.name
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            Path(temporary_path).unlink(missing_ok=True)


def list_parameters(state: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return defensive copies of the discovered canonical parameters."""

    validate_canonical_state(state)
    return tuple(copy.deepcopy(parameter) for parameter in state["parameters"])


def get_parameter(state: Mapping[str, Any], parameter_id: int) -> dict[str, Any]:
    """Return one discovered parameter by its real CLAP identifier."""

    for parameter in list_parameters(state):
        if parameter["id"] == parameter_id:
            return parameter
    raise KeyError(parameter_id)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert direct CLAP probe JSON into canonical synth state"
    )
    parser.add_argument("--probe", required=True, help="probe JSON path or - for stdin")
    parser.add_argument("--upstream-commit", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.probe == "-":
        probe = json.load(sys.stdin)
    else:
        probe = json.loads(Path(args.probe).read_text(encoding="utf-8"))
    state = canonicalize_probe(probe, upstream_commit=args.upstream_commit)
    write_canonical_state(state, args.output)
    print(f"wrote canonical synth state: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

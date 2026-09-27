# Milestone 2: canonical synth state

## Result

The project now has a versioned, machine-readable state contract derived from the pinned real plugin. The committed example contains:

- exact plugin identity and upstream source commit;
- all 10 parameters returned by `clap.params`, including real integer IDs, names, modules, ranges, defaults, current values, raw flags, and automation/modulation capabilities;
- the exact 97-byte payload returned by `clap.state`, encoded as base64 and protected by its byte length and SHA-256 digest; and
- capture-method, parameter-count, and selected-parameter restoration evidence.

The implementation lives in `src/agentic_synth_twin/synth_state.py`. The formal shape is documented by [`canonical-synth-state-v1.schema.json`](schemas/canonical-synth-state-v1.schema.json). The direct capture is [`milestone-2-clap-probe.json`](evidence/milestone-2-clap-probe.json), and its normalized state is [`milestone-2-canonical-state.json`](evidence/milestone-2-canonical-state.json).

## Authority boundaries

The opaque CLAP state payload is the authoritative restoration payload exposed by this plugin. The parameter inventory is the readable and queryable view used for inspection and future controlled changes. A parameter list alone is not assumed to capture hidden plugin state.

Names, modules, ranges, defaults, current values, flags, and capabilities are copied from the live plugin response. `unit` is explicitly `null` because this CLAP parameter inventory does not expose a separate unit field. Text such as “(s)” or “in Cents” remains part of the plugin-provided name and is not parsed into invented metadata.

The contract is tied to the recorded plugin ID, version, repository, and commit. The probe verified restoration of the selected test parameter after loading this payload; it does not prove cross-version portability or equivalence of unobserved hidden state.

## Validation

`validate_canonical_state()` rejects malformed plugin identity, duplicate parameter IDs, non-finite or out-of-range values, invalid base64, mismatched byte lengths, state hash tampering, unsupported capture methods, and inconsistent parameter counts. `write_canonical_state()` validates before atomically replacing an output file; `load_canonical_state()` validates on read.

The checked-in JSON Schema documents the interchange shape. The Python validator remains authoritative for cross-field rules that plain JSON Schema does not fully express, including unique IDs, numeric range relationships, decoded byte length, digest integrity, and matching parameter counts.

## Reproduce from the real plugin

Prerequisites are the same Apple Silicon macOS build requirements documented for Milestone 1.

```bash
scripts/capture_canonical_state.sh
```

By default this writes ignored runtime output to `work/evidence/canonical-synth-state.json`. An explicit output path may be supplied as the first argument. The script rebuilds the pinned plugin and probe, captures fresh JSON, canonicalizes it, validates it, and writes it atomically.

## Honest boundary

This milestone provides no GUI, audio render, parameter editor, or general-purpose CLAP host. It proves the data contract needed by those later capabilities. Milestone 3 alone owns deterministic audition rendering.

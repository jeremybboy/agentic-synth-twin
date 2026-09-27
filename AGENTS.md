# Agent operating contract

## Purpose

Build a local, validated synth digital-twin experiment incrementally. Preserve the distinction between predicted behavior, real-synth measurement, human perception, and reproducible locked state.

## Canonical locations

- Reusable Python/backend code: `src/agentic_synth_twin/`
- Tests: `tests/`
- Architecture and decisions: `docs/`
- Reproducible feasibility tools: `scripts/`
- Committed evidence snapshots: `docs/evidence/`
- Editable documentation assets: `docs/assets/`
- Repository automation: `.github/`

Do not create a separate frontend unless an independent frontend architecture becomes necessary. A future Streamlit UI must remain a thin client of backend functions.

## Current boundary

Milestone 5 is deterministic DSP measurement of the completed Milestone 4 A/B probes: exact regenerated audio, traceable WAV hashes, documented level/spectrum/envelope descriptors, preserved human labels, and a read-only local report. The browser remains a thin client of backend functions. Do not add public hosting, loudness normalization, synthetic datasets, ML, optimization, or LOCK until the corresponding milestone is explicitly authorized.

## Invariants

- Discover plugin parameters from the real plugin; never invent names or meanings.
- Keep the audition input deterministic and recorded.
- Trace every important render to exact synth state and experiment configuration.
- Keep inference/search claims separate from real-synth verification.
- Treat human audition as required evidence, not an automated check.
- LOCK requires saved state plus reload/restore/rerender comparison.
- Keep all execution local; do not add cloud services or LLM APIs.

## Validation

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
git diff --check main...HEAD
```

Add milestone-specific checks only when the implementation exists.

## Git workflow

- Never commit directly to `main` after bootstrap.
- Work on a dedicated branch and stage only explicit paths; never use `git add .`, `-A`, or `--all`.
- Preserve unrelated or unfamiliar files and changes.
- Never force-push, enable auto-merge, merge a PR, or delete user work.
- Open one focused PR as ready for review once its checks pass; use draft status only for explicitly unfinished work.
- Keep solo-repository governance frictionless: require the PR and applicable CI, but do not require the owner to assign or obtain a separate approving reviewer.
- Hand the owner a direct PR link for visual inspection and manual merge; never merge or enable auto-merge on the owner's behalf.

See `docs/architecture.md` for component ownership and scope.

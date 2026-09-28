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

Milestone 8B is a playable extension of the Milestone 8A patch cockpit. The editable working copy, real-CLAP rendered-note cache, on-screen and computer keyboards, optional browser Web MIDI input, piano range, velocity bounds, and explicit non-realtime limitation are authoritative. Search evidence remains immutable when the working copy is edited. The browser remains a thin client of backend functions. Do not add a native realtime host, public hosting, loudness normalization, new optimizers, Filter Type search, semantic models, or LOCK until the corresponding milestone is explicitly authorized.

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

## PR communication

- Every implementation PR must include a concise, verified technical brief and a synchronized visual summary. Follow `docs/pr-visual-brief-standard.md`.
- The brief must distinguish current state, user-visible changes, touched layers, explicitly untouched scope, persistence/undo impact, audio/realtime impact, C2PA/provenance impact, automated evidence, and human acceptance still required.
- Store project-bound visuals in `docs/assets/`, link them from the brief and PR description, and label proposed, implemented, automated, and manually accepted states accurately.
- Keep visual documentation subordinate to the implementation scope; it must not introduce features, imply validation that did not occur, or replace listening and hardware checks.

See `docs/architecture.md` for component ownership and scope.

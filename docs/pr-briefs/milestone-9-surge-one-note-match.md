# Milestone 9: selectable external targets and Surge XT one-note match

![Milestone 9 full-library retrieval-audit map](../assets/milestone-9-full-library-retrieval-audit.svg)

> Follow-up scope: the original selectable-target implementation remains, but retrieval is now audited over the complete factory library with the frozen perceptual hypothesis documented in [`docs/perceptual-retrieval-audit.md`](../perceptual-retrieval-audit.md).

## Verified status and starting point

- Repository: `jeremybboy/agentic-synth-twin`
- Base: `main` at `44b531e23bf04f36c04966418110a9ff1d30f488`
- Branch: `codex/milestone-9-surge-one-note`
- Pull request: [#13](https://github.com/jeremybboy/agentic-synth-twin/pull/13)
- Real synth: Surge XT CLAP `1.3.4`, plugin ID `org.surge-synth-team.surge-xt`
- Status: full-library retrieval-audit implementation and real-library evidence complete; owner browser/audio acceptance remains a separate gate

## Goal and user-visible workflow

Keep the existing playable cockpit and ten named external procedural C3 references, but replace the bounded retrieval experiment with a complete-library audit. A target click reuses one target-independent real-Surge cache, exposes legacy and perceptual Top 10 lists, selects the highest-ranked search-eligible perceptual result as Base, and creates a clean target-specific run; it never starts search automatically. The user listens, records the human audit, and may then start the unchanged CMA-ES refinement and playable-patch workflow.

## Transparency/change ledger

| Area | Verified boundary |
| --- | --- |
| Current state | PR #13 provided ten external targets, a 120-preset/39-eligible bounded retrieval, eight-control local refinement, and the preserved playable cockpit; listening did not support a convincing timbre-match claim. |
| User-visible change | Complete-library legacy versus perceptual Top 10, contribution/eligibility inspection, selected-candidate plots/audio, coverage diagnostics, and explicit saved human judgments. |
| Code/data layers touched | Perceptual descriptor module, shared cache identity/eligibility, target ranking and Base selection, cockpit audit API/UI, sanity script/tests, documentation, and synchronized SVG. |
| Explicitly untouched | CMA objective/refinement; `clap-saw-demo`; keyboard mappings; editable patch; hidden-target regression; multi-note/velocity/phrase matching; upload/pitch detection; semantic models; hardware synths; public hosting; native realtime hosting; LOCK. |
| Persistence/undo impact | Canonical target WAVs are immutable committed fixtures. Shared cache and per-target run states/audio/history stay in ignored `work/`; switching never deletes or overwrites another run. Working-patch edits remain non-authoritative and do not mutate search evidence. |
| Audio/realtime impact | Every preset, candidate, Base, Best, and playable note remains a process-isolated real CLAP render. External targets are exact procedural WAVs. First-strike latency and the six-second note cache remain; this is not a realtime host. |
| C2PA/provenance impact | No C2PA behavior changes. Provenance is explicit: targets are original procedural, non-Surge, sample-free references; synth results bind plugin/state/parameter/render/WAV hashes. |
| Automated evidence | Descriptor arithmetic/directionality, full cache coverage, deterministic state/audio hashes, both Top 10 lists, Base eligibility/rank, and self/near-state sanity are verified independently in [`docs/evidence/milestone-9/perceptual-retrieval-audit.json`](../evidence/milestone-9/perceptual-retrieval-audit.json). |
| Human acceptance still required | For every target, owner compares both Top 10s, records ranking status and plausible-neighborhood status, then separately judges any CMA Base/Best and playable behavior. |

## Implemented sequence

1. Validate the exact ten-file external target manifest, stable order, paths, format, hashes, and provenance flags.
2. Build one target-independent cache from all 637 presets in Surge XT 1.3.4's authoritative `patches_factory/` tree.
3. Keep every deterministic, audible, unclipped render in retrieval; separately mark whether it exposes the exact authorized eight-control inventory needed for CMA refinement.
4. Rank the complete retrieval-eligible cache twice for each target: unchanged legacy objective and frozen uncapped four-family perceptual score.
5. Expose both Top 10s, their raw/normalized/weighted components, eligibility, audio, waveform/log-mel comparison, and coverage diagnostics.
6. Select the highest perceptual result that is also search eligible as Base while preserving its true library rank; target changes remain `IDLE` and never start CMA automatically.
7. Keep the existing manual Start/Pause/Resume/Stop controls, plots, histories, editable Base/Best patch, piano, Ableton-style keys, velocity/octave controls, Web MIDI, and note cache.
8. Preserve the original hidden reachable-target reproduction, numeric gate, and equal-budget random control as a separate regression path.

## Real-plugin result

The factory inventory contained 637 presets: 467 (73.3%) passed retrieval, 257 (40.3%) also passed the eight-control search boundary, and 170 failed the deterministic render/descriptor contract. All ten targets produced legacy and perceptual Top 10 lists from shared cache `579fa526c21e0bee682bc7e3f8011be5b6fb78fba09f52ac19db8ac74ee95ead`; target switching did not rerender the library.

The perceptual #1 differs from the legacy #1 for all ten targets. Search eligibility moved the actual CMA Base below perceptual #1 for five targets: guitar rank 5, Rhodes rank 9, muted pluck rank 4, sync lead rank 4, and marimba rank 13. Self-retrieval passed 12/12 at rank 1 and near-state retrieval passed 4/4 in the Top 10 without changing weights. Browser QA also observed occasional later Base-initialization equality failures that passed on retry; successful two-render hashes remain event-level evidence, not a claim that every future host invocation is stable.

This proves cache identity, eligibility separation, repeatable ranking mechanics, and the declared sanity properties. It does **not** prove that any Top 10 sounds like its target: all human comparisons are `PENDING` and all plausible-neighborhood judgments are `UNCLEAR`.

## Verification

```bash
PYTHONPATH=src .venv/bin/python3 -m unittest discover -s tests -v
.venv/bin/python3 -m compileall -q src tests
git diff --check main...HEAD
scripts/validate_external_targets.sh
sh -n scripts/*.sh
scripts/run_surge_match.sh
```

Manual browser acceptance: open `http://127.0.0.1:8886`; select every target; audition both Top 10s; inspect waveform/log-mel/components; save one human comparison and plausible-neighborhood status per target; confirm selection stops at `IDLE`; optionally start one search and confirm selection locks until Stop/completion; compare Target/Base/Best; load and edit Base/Best; play all visible white/black keys and the Ableton mapping; optionally connect USB MIDI. Human judgment remains `PENDING_OWNER_AUDITION`.

Do not merge automatically. The owner reviews this same PR, performs the listening/browser checks, and manually merges only if accepted.

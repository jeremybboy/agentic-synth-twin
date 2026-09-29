# Perceptual multi-start refinement

This follow-up tests whether full-library perceptual retrieval can become a better real-Surge patch search by refining several plausible factory states instead of committing to one preset. It preserves the Milestone 9 target bank, full-factory cache, frozen perceptual descriptor, cockpit, editable patch, piano, computer keyboard, and optional browser MIDI path. It does not claim instrument recognition, perceptual equivalence, multi-note validity, or realtime hosting.

## Frozen experiment contract

- Retrieval uses the existing complete factory cache and the unchanged `55 / 25 / 15 / 5` log-mel, envelope, harmonic, and flatness weights.
- Family medians are copied from the target's complete retrieval ranking and remain fixed through screening and optimization.
- The search scans perceptual ranks 1–10 until five presets expose a valid adaptive continuous space or the list is exhausted.
- Every audition remains the fixed C3, velocity 100, two-second note plus half-second tail contract.
- CMA-ES does not choose presets. Retrieval supplies starting states; CMA-ES searches only the frozen real controls selected independently for each state.

## Transparent hierarchical screen

The screen never assumes that all 775 reported Surge controls are useful continuous audio controls.

1. The real CLAP inventory and current patch state produce an explicit inclusion/exclusion ledger. Invalid ranges, stepped/hidden/read-only flags, host-fixed controls, unassigned macros, routing/capacity controls, bypass, and FX slots whose real CLAP text value is `Off` are excluded with named rules.
2. Broad CLAP modules are subdivided only from explicit Surge names, such as numbered oscillator, filter, envelope, LFO, and FX-slot prefixes. Unmatched controls retain their original CLAP module path.
3. Four no-change renders establish a per-family render-noise floor for the preset.
4. Each subgroup gets a neutral render that sends its current values and matched −5%/+5% renders. A subgroup advances only when a valid directional render exceeds the measured noise floor.
5. If Surge snaps or refuses a subgroup request, requested and applied values are both recorded and that control is excluded from the continuous screen.
6. Only controls inside responsive subgroups receive matched neutral/−5%/+5% probes. Every raw family distance, normalized distance, weighted contribution, invalid reason, hash, requested value, and applied value remains in the run evidence.
7. Responsive controls are reduced to 4–12 dimensions: strongest available representative per contributing family, then target-aligned gains up to eight controls, then at most four remaining high-influence controls. Each selected control receives a ±0.20 normalized local bound clipped to 0–1.

This is a measured screen, not semantic parameter inference. If real metadata and behavior cannot produce four valid continuous controls, that preset is marked insufficient and the scan continues; no parameter meaning is invented.

## Two-stage CMA schedule

Each of five valid starts receives a deterministic 32-evaluation pilot: population 8 for four generations. Starts are ranked by absolute frozen perceptual score, not percentage improvement. The two best pilot trajectories continue from their existing CMA state for eight more generations, 64 evaluations each, producing a fixed maximum CMA budget of 288 evaluations.

Invalid, silent, clipped, failed, or descriptor-silent candidates consume their assigned budget and retain an explicit penalty record. The optimizer is not secretly retuned for Rhodes or for later targets.

## Independent stability verification

The numerically best candidates are checked in ascending score order. Each candidate is rendered twice from the same native preset state and requested control values; the two WAV hashes and real applied-value readbacks must match. A failed candidate is recorded and never retried; verification proceeds to the next distinct candidate. The numeric best and verified stable best are separate evidence fields and may differ.

## Cockpit and evidence

The existing cockpit remains one thin browser client. It adds the five trajectories, structural pool, responsive subgroup table, selected 4–12D parameter space, Stage A/Stage B status, four perceptual contributions, numeric best, and verified stable best. Target/Base/Best remain auditionable and the adopted verified patch remains editable and playable on the existing keyboards.

Generated full evidence remains under ignored `work/` directories. The committed evidence snapshot is generated only after Rhodes completes and the unchanged electric-guitar and sub-bass configurations run as falsification targets. Automated evidence cannot accept timbre quality; Target/Base/Best listening remains `PENDING_OWNER`.

## Reproduce

```bash
scripts/reproduce_perceptual_multistart.sh rhodes-style-electric-piano
scripts/reproduce_perceptual_multistart.sh plucked-electric-guitar
scripts/reproduce_perceptual_multistart.sh analog-sub-bass
```

All three commands reuse the same target-independent real-Surge state/audio/descriptor cache. No cloud service or API is used.

## Completed automated result

The frozen run used five valid 12-control starts and exactly 288 CMA evaluations for each target. Rhodes produced a `0.167452` numeric best from retrieval rank 1, but the first three numerical candidates failed independent stability rerender; retrieval rank 3 supplied the first verified stable result at `0.181136`. The Rhodes `EP 2` start improved from `0.361990` to `0.301584` but did not survive the absolute-score pilot ranking. Electric guitar produced a numeric and stable best of `0.333648` from retrieval rank 2; sub bass did not beat the rank-1 screened Base at `0.407065`.

The [committed evidence](evidence/perceptual-multistart-refinement.json) contains the three path-sanitized full records. Across targets the screen and optimizer configuration and family weights are identical; only the target audio and pre-frozen target-specific normalization medians differ. This result validates the inspectable experiment machinery, not perceptual timbre quality: all three human judgments remain `PENDING_OWNER`.

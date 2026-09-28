# Milestone 9: Surge XT one-note timbre match

![Implementation map showing Target Note, Surge XT Factory Library, Closest Presets, Local CMA-ES, Real Surge XT, and Verified One-Note Result while preserving ranking, parameters, convergence, Target/Base/Best, and the playable keyboard.](assets/milestone-9-surge-one-note-visual.png)

## Question and boundary

Milestone 9 asks one narrow question: given one fixed target note, can Agentic Synth Twin retrieve and locally refine a real Surge XT factory preset whose rendered C3 timbre becomes measurably and audibly closer to that target? It does not claim reconstruction across pitches, velocities, chords, phrases, or semantic prompts. Playing other notes in the preserved cockpit is exploratory audition only.

## Additive architecture

The browser, objective, search telemetry, editable working copy, note cache, piano, Ableton-style computer mapping, octave/velocity controls, and optional Web MIDI input remain one shared system. `ClapSynthAdapter` owns process-isolated state inspection and exact rendered-note operations; `SurgeXTAdapter` adds only native factory-preset discovery and state extraction. `clap-saw-demo` remains supported by the existing direct-search path.

```text
existing cockpit / keyboard / objective / evidence
                         |
                  CLAP synth adapter
                    /           \
          clap-saw-demo       Surge XT 1.3.4
                                   |
                         bounded factory index
                                   |
                         local eight-control search
```

## Frozen experiment

- Synth: Surge XT CLAP 1.3.4 for macOS, official plugin-only release.
- Preset content: official Surge XT 1.3.4 portable content; `patches_factory` only.
- Library selection: 120 presets selected before scoring by deterministic category round-robin from 637 available factory presets; the hidden reachable target base is guaranteed to be included. Sixty-five passed the double-render, non-silent, non-clipping gate; all 55 exclusions are recorded.
- Audition: MIDI C3/48, velocity 100, two-second hold, half-second tail, 44.1 kHz, stereo 16-bit PCM, 64-frame blocks.
- Determinism: both scenes' three oscillator Retrigger controls are forced to their discovered real value `1.0`; every indexed preset is independently rendered twice, and non-deterministic, silent, clipped, or failed entries are excluded and recorded.
- Objective: unchanged `0.65 * spectral + 0.25 * envelope + 0.10 * loudness`, with the existing multi-resolution log-magnitude STFT and envelope definitions.
- Local search: eight predeclared, real, continuous Scene A parameters; categorical controls are frozen.
- CMA-ES: seed `20260930`, population 8, 15 generations, initial sigma 0.18, normalized bounds 0..1, fixed 120-evaluation budget.
- Random control: seed `20260931`, same target, base state, parameters, bounds, objective, and 120-evaluation budget.
- Numeric success gate: at least 25% total-objective improvement from the selected base.
- Human success gate: owner separately answers whether Best is audibly closer than Base after hearing Target/Base/Best.

The hidden target is real Surge audio generated from a known factory state plus a fixed eight-value interior vector. Search receives only target audio; target identity and values remain hidden until completion.

## Frozen result

The nearest valid factory state was `Basses/Attacky.fxp` at total loss `0.281768571357`. The next four were `Percussion/Kick Tech 2.fxp`, `Basses/Bass 3.fxp`, `Percussion/Kick Tech 1.fxp`, and `Basses/Bass 4.fxp`. Factory labels were not used in scoring.

CMA-ES exhausted the predeclared 120-evaluation budget and reached `0.028968679924`, an `89.718981%` reduction from Base and therefore a pass on the 25% numeric gate. Two clipped candidates remain in history with fixed worst loss. Equal-budget local random search reached `0.184376533661`; this single run is reported as a control, not proof of universal optimizer superiority.

The final Best rerender matched search SHA-256 `7e1f0848eadf30fa622da32f1a3be1883e6a4ab11ba2495b54501ab4a63f49b1` byte-for-byte. Human Target/Base/Best judgment remains `PENDING_OWNER_AUDITION`; no perceptual success claim has been made.

## Active controls

The exact CLAP inventory is checked on the selected base before search. The approved controls are `A Osc 1 Shape`, `A Osc 1 Width 1`, `A Osc 1 Unison Detune`, `A Filter 1 Cutoff`, `A Filter 1 Resonance`, `A Amp EG Attack`, `A Amp EG Decay`, and `A Amp EG Release`. Their real IDs, ranges, base values, candidates, and final values are preserved in run evidence; no Surge meaning is invented by the repository.

## Run and reproduce

```bash
scripts/run_surge_match.sh
# open http://127.0.0.1:8879

scripts/reproduce_surge_match.sh
```

The setup script downloads the two official 1.3.4 artifacts into ignored `work/`, verifies the publisher checksums, and compiles the repository CLAP probe and renderer. It does not install a system plugin, use a cloud service, or commit generated WAVs.

## Evidence and authority

The full local run persists `preset-index.json`, `history.jsonl`, `run.json`, exact extracted states, and rendered WAVs under ignored `work/milestone-9-final/`. A compact, reviewable snapshot is committed under `docs/evidence/milestone-9/`; it contains hashes, configuration, ranking, component losses, changed parameters, fair-control results, and final rerender evidence—not generated audio.

Automated evidence can prove exact plugin attribution, state hashes, applied values, fixed conditions, objective arithmetic, cache behavior, and repeated render identity. It cannot prove perceptual similarity. Target/Base/Best listening, browser playback, and USB MIDI behavior remain explicit owner acceptance checks.

## Known limitations

- One reachable hidden target cannot establish broad matching performance.
- Preset categories are metadata, not acoustic truth.
- A Top 5 numerical ranking may disagree with human preference.
- The eight-control screen is a bounded first policy, not proof that these are optimal controls for every preset.
- The browser performs cached rendered-note audition, not low-latency native hosting.
- The final result is a base preset plus parameter changes and verified real audio, not a newly trained instrument.

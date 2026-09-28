# Milestone 8A: direct real-synth search

Milestone 8A asks a narrow question: can an adaptive optimizer find audio close to a hidden target by evaluating the real synthesizer directly? It does not use the Milestone 7 surrogate, text prompts, a neural model, or a browser-side optimizer. The answer from the fixed demonstration is mixed: the engine and optimization gates passed, while CMA-ES did not beat the equal-budget random-search control.

## Fixed experiment

The target, starting patch, and every candidate use the existing deterministic audition: C3 (MIDI 48), velocity 100, 44.1 kHz, 64-frame blocks, two seconds held plus a half-second release tail. Filter Type (ID `14255`) remains fixed at its discovered value `0`. The four continuous coordinates are normalized to `[0,1]`, but every render records the applied real value.

| Parameter discovered from the plugin | ID | Real range | Starting value |
|---|---:|---:|---:|
| Oscillator Detuning (in cents) | 8675309 | -200–200 | 0 |
| Unison Spread in Cents | 2391 | 0–100 | 10 |
| Cutoff in Keys | 17 | 1–127 | 69 |
| Amplitude Attack (s) | 2874 | 0–1 | 0.01 |

The hidden target is a fixed interior normalized vector `[0.72, 0.68, 0.28, 0.62]`. Its real state is detuning `88`, spread `68`, cutoff `36.28`, and attack `0.62`. The optimizer receives only the target WAV and allowed ranges; the cockpit withholds these values until the run finishes and the owner clicks **Reveal target parameters**. This is interface-level experiment secrecy, not a security boundary, because the deterministic target recipe exists in local source and evidence.

## Transparent objective

For every candidate, the backend computes:

```text
total_loss = 0.65 × spectral_loss
           + 0.25 × envelope_loss
           + 0.10 × loudness_loss
```

`spectral_loss` is the mean absolute difference between Hann-windowed log-magnitude STFTs at `(FFT, hop)` sizes `(256,64)`, `(1024,256)`, and `(4096,1024)`, divided by `0.05` and capped at 1. `envelope_loss` compares the full log-RMS envelope using a 512-sample window and 128-sample hop, divided by `1.5` and capped at 1. `loudness_loss` is the absolute whole-file RMS dBFS difference divided by 24 dB and capped at 1. The identity case is exactly zero, and a previously verified real-synth Unison variant has positive component and total loss; those sanity cases were fixed before the final hidden-target run.

The score is an audio-domain distance, not proof of perceptual equivalence. It deliberately exposes each component in the cockpit and run history.

## Optimizer and controls

CMA-ES uses pycma `4.5.0`, its ask/tell API, bounds `[0,1]^4`, seed `20260928`, initial mean equal to the canonical patch, initial sigma `0.22`, population 8, and 20 generations. The fixed budget is therefore 160 real-synth evaluations. Equal-budget bounded random search uses seed `20260929` against the same target and objective.

The backend owns rendering, objective evaluation, optimizer state, persistence, controls, and target reveal. The loopback browser polls backend state and supplies Start, Pause/Resume, Stop, live current parameters, CMA mean/spread, selectable 2-D search-space views, convergence, component losses, complete history, shared-scale target/best spectrograms, and Target/Start/Best playback. Pause and stop take effect between atomic real-synth evaluations, so no half-evaluated record is persisted.

## Predeclared gates and result

| Gate | Rule | Result |
|---|---|---|
| Engine integrity | Exact logged state, deterministic target/start/final rerenders, no clipping | **PASS** |
| Optimization | At least 30% better than start within 160 evaluations | **PASS** — 86.811% |
| Adaptive-search value | CMA-ES best loss no worse than equal-budget random best | **FAIL** — 0.062362 vs 0.058876 |
| Human audition | Owner separately compares Target, Start, and Best | **PENDING** |

The starting loss was `0.472832750041`; CMA-ES found `0.062361549029` at evaluation 140, generation 18, in 5.019 seconds. Its best normalized state was `[0.651588401791, 0.000713200838, 0.337432210042, 0.59717536604]`, corresponding to detuning `60.635360716445`, spread `0.071320083766`, cutoff `43.516458465287`, and attack `0.59717536604`. A second fixed-seed run reproduced all 160 proposed normalized states, component scores, real states, and WAV hashes exactly. The compact committed record is [`evidence/milestone-8a/result.json`](evidence/milestone-8a/result.json).

The failure matters: this run demonstrates a working direct real-synth search loop, not superiority of CMA-ES. One target is also insufficient to establish general behavior, and numerical closeness still requires owner audition.

## Run locally

Create a Python environment with the pinned project dependencies, then launch the cockpit:

```bash
scripts/run_direct_search.sh
```

Open `http://127.0.0.1:8765`, press **Start search**, watch the real-time telemetry, and audition the three WAVs. Use a separate local output and port with `scripts/run_direct_search.sh work/my-run 8879`.

Reproduce the fixed CMA-ES run and its equal-budget random control:

```bash
scripts/reproduce_direct_search.sh
```

Generated WAVs, complete JSONL history, private run state, and benchmark details remain under ignored `work/`; only the compact evidence snapshot is committed.

## Boundary after this experiment

Do not add Bayesian optimization, Filter Type search, semantic intent, public hosting, loudness normalization, or LOCK from this result. Milestone 7 remains separate evidence about a global surrogate; Milestone 8A neither deletes it nor depends on it.

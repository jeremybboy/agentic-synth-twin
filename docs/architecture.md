# Architecture

## Decision record: Milestone 0

Agentic Synth Twin is a local macOS experiment, not a general-purpose DAW, hosted service, or autonomous agent platform. Prototype 0 uses Python for orchestration, experiment data, analysis, and learning; a thin Streamlit interface is planned because it can support supervised audio comparison without introducing a separate frontend stack.

The reusable backend will live under `src/agentic_synth_twin/`. UI code may call backend interfaces, but it must not own synth control, rendering, analysis, dataset, or model logic. A top-level `frontend/` is intentionally omitted until a genuinely independent frontend exists.

## Conceptual chain

```text
Human
  ↓
Local Web GUI
  ↓
Experiment Controller
  ↓
CLAP Host / Synth Controller
  ↓
clap-saw-demo
  ↓
Audio Renderer
  ↓
DSP Analyzer
  ↓
Dataset
  ↓
Surrogate Model
  ↓
Optimizer
  ↓
Real Synth Verification
  ↓
Human Validation
  ↓
LOCK
```

This is a planned responsibility chain. PR 1 proves only the narrow CLAP feasibility boundary; it does not claim that the remaining components exist.

## Component responsibilities

### Human and local web GUI

The human supplies sonic intent, evaluates controlled A/B movements, chooses active parameters, auditions candidates, and makes KEEP/REJECT/LOCK decisions. Milestone 3 adds only a thin one-button browser preview of committed audio evidence. Milestone 4 adds a local thin client that presents randomized Sound 1/Sound 2 pairs, records YES/NO directly through backend APIs, and advances automatically. Milestone 5 adds a read-only local report that places those answers beside verified measurements and exact A/B playback. SQLite persistence, probe generation, measurement, and authority rules remain backend responsibilities.

### Experiment controller

The controller coordinates deterministic auditions and records configuration, seeds, parameter states, judgments, artifact paths, and lifecycle status. It does not implement plugin hosting, signal analysis, or model training.

### CLAP host and synth controller

Milestone 1 proves that a small CLAP C API probe can load the pinned `clap-saw-demo` build, discover parameters directly from the plugin, read and modify one value, and restore saved state. Milestone 2 converts that direct evidence into a validated canonical contract containing both a readable parameter inventory and the exact opaque CLAP state. The probe remains feasibility evidence rather than a general-purpose host. No parameter names or semantics may be invented.

### Audio renderer

Milestone 3 applies scientific C3 (MIDI 48), velocity 100, a two-second hold, a fixed half-second release tail, 44.1 kHz sample rate, and 64-frame processing blocks. Rendering is callable without the browser, and two runs must produce byte-identical WAV evidence on the test system.

### DSP analyzer and dataset

Milestone 5 implements deterministic whole-file RMS and peak, power-spectrum centroid and 85% rolloff, plus simple moving-RMS attack and release estimates. Stereo energy and spectra are aggregated across channels without changing or normalizing the render. Milestone 6 adds one exact baseline plus 255 seeded states across Filter Type, Cutoff, and Attack, with balanced stepped categories and stratified continuous dimensions. Each row remains traceable to exact prior evidence, synth state, input configuration, WAV hash, render verification, and features; the WAV files stay local while the manifest is committed.

### Surrogate model and optimizers

The Milestone 7 surrogate experiment is separate evidence about predicting descriptors in a bounded sampled region. Milestone 8A intentionally bypasses it: bounded CMA-ES proposes four normalized continuous parameters, every proposal is rendered on the real synth, and a transparent multi-resolution spectral, envelope, and loudness objective scores the resulting WAV. An equal-budget random search is the scientific control; the fixed demonstration did not establish CMA-ES superiority.

Milestone 8B holds that search contract fixed across eight predetermined in-synth targets. A separate loopback report blinds CMA versus random identities, records the owner's A/B/no-difference judgment locally, and reveals method mappings only after all comparisons. This validates repeatability within the synth's reachable space; it is not evidence for guitar, Rhodes, or arbitrary reference matching.

### Real synth verification, human validation, and LOCK

Promising states return to the real synth for rendering and measurement. The UI will compare predictions with measurements and baseline with candidates. LOCK must persist exact plugin state, canonical parameter JSON, render, measurements, and experiment metadata, then prove restoration after reload by rerendering and comparing.

## Intended backend boundaries

Names may evolve as evidence arrives, but the backend should expose operations equivalent to:

- parameter/state: `list_parameters`, `get_state`, `set_parameters`, `save_state`, `load_state`
- controlled evidence: `render_audition`, `extract_features`
- human calibration: `generate_probe`, `record_probe_answer`
- learning/search: `generate_dataset`, `train_surrogate`, `optimize_target`
- direct search: `create_search_run`, `compute_audio_objective`, `benchmark_random_search`
- validation: `run_validation_suite`, `record_judgment`, `reveal`
- authority/reproducibility: `verify_candidate`, `lock_patch`

## Data and reproducibility invariants

1. Plugin metadata and the exact upstream commit are recorded.
2. Parameter inventory is discovered from the actual plugin.
3. Every important render has an exact synth state and audition configuration.
4. Random processes use recorded fixed seeds.
5. Generated datasets and audio artifacts are not casually committed; manifests and storage policy arrive with the generating milestone.
6. Surrogate predictions are labeled as predictions until measured on the real synth.
7. LOCK is incomplete until reload, restoration, rerender, and comparison succeed.

## Scope boundary

Prototype 0 deliberately excludes:

- LLM APIs
- MIDI 2.0
- ML-CLAP semantic embeddings
- diffusion or generated-audio models
- multiple synthesizers
- autonomous agent loops
- cloud backends
- production-quality UI

Surge XT is not introduced. `clap-saw-demo` is the sole planned prototype synth until the experiment earns expansion.

## Delivery boundaries

Every roadmap milestone is a small pull request with independently reviewable evidence. Milestone 1 owns the pinned CLAP feasibility proof documented in [`clap-feasibility.md`](clap-feasibility.md). Milestone 2 owns the state contract documented in [`canonical-synth-state.md`](canonical-synth-state.md). Milestone 3 owns the fixed render and one-button preview documented in [`deterministic-audition.md`](deterministic-audition.md). Milestone 4 owns the terminal human calibration path documented in [`human-ab-probe.md`](human-ab-probe.md). Milestone 5 owns the bounded DSP analyzer documented in [`dsp-features.md`](dsp-features.md). Milestone 6 owns the bounded, traceable dataset documented in [`synthetic-dataset.md`](synthetic-dataset.md). Milestone 7 retains the separately reviewed surrogate result; Milestone 8A owns the direct search described in [`direct-real-synth-search.md`](direct-real-synth-search.md), and Milestone 8B owns the paired validation described in [`multitarget-validation.md`](multitarget-validation.md).

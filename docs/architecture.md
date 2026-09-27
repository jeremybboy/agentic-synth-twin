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

The human supplies sonic intent, evaluates controlled A/B movements, chooses active parameters, auditions candidates, and makes KEEP/REJECT/LOCK decisions. Milestone 3 adds only a thin one-button browser preview of committed audio evidence. Milestone 4 adds a local thin client that presents randomized Sound 1/Sound 2 pairs, records YES/NO directly through backend APIs, and advances automatically. SQLite persistence, probe generation, and authority rules remain backend responsibilities.

### Experiment controller

The controller coordinates deterministic auditions and records configuration, seeds, parameter states, judgments, artifact paths, and lifecycle status. It does not implement plugin hosting, signal analysis, or model training.

### CLAP host and synth controller

Milestone 1 proves that a small CLAP C API probe can load the pinned `clap-saw-demo` build, discover parameters directly from the plugin, read and modify one value, and restore saved state. Milestone 2 converts that direct evidence into a validated canonical contract containing both a readable parameter inventory and the exact opaque CLAP state. The probe remains feasibility evidence rather than a general-purpose host. No parameter names or semantics may be invented.

### Audio renderer

Milestone 3 applies scientific C3 (MIDI 48), velocity 100, a two-second hold, a fixed half-second release tail, 44.1 kHz sample rate, and 64-frame processing blocks. Rendering is callable without the browser, and two runs must produce byte-identical WAV evidence on the test system.

### DSP analyzer and dataset

The analyzer will compute a deliberately small descriptor set such as RMS proxy, peak, spectral centroid, rolloff, and simple envelope estimates. Each dataset row must remain traceable to exact synth state, input configuration, audio, features, software versions, and experiment metadata.

### Surrogate model and optimizer

The surrogate will model only the sampled local region of approximately 2–5 human-selected active parameters. An initial simple model will predict acoustic descriptors from parameter values and report held-out error. The optimizer will search predictions cheaply, nominate only a few candidates, and never substitute its predictions for real-synth evidence.

### Real synth verification, human validation, and LOCK

Promising states return to the real synth for rendering and measurement. The UI will compare predictions with measurements and baseline with candidates. LOCK must persist exact plugin state, canonical parameter JSON, render, measurements, and experiment metadata, then prove restoration after reload by rerendering and comparing.

## Intended backend boundaries

Names may evolve as evidence arrives, but the backend should expose operations equivalent to:

- parameter/state: `list_parameters`, `get_state`, `set_parameters`, `save_state`, `load_state`
- controlled evidence: `render_audition`, `extract_features`
- human calibration: `generate_probe`, `record_probe_answer`
- learning/search: `generate_dataset`, `train_surrogate`, `optimize_target`
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

Every roadmap milestone is a small pull request with independently reviewable evidence. Milestone 1 owns the pinned CLAP feasibility proof documented in [`clap-feasibility.md`](clap-feasibility.md). Milestone 2 owns the state contract documented in [`canonical-synth-state.md`](canonical-synth-state.md). Milestone 3 owns the fixed render and one-button preview documented in [`deterministic-audition.md`](deterministic-audition.md). Milestone 4 owns the terminal human calibration path documented in [`human-ab-probe.md`](human-ab-probe.md); Milestone 5 remains separately authorized work.

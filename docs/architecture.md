# Architecture

## Decision record: PR 0

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

This is a planned responsibility chain, not a claim that these components exist in PR 0.

## Component responsibilities

### Human and local web GUI

The human supplies sonic intent, evaluates controlled A/B movements, chooses active parameters, auditions candidates, and makes KEEP/REJECT/LOCK decisions. The future GUI is a supervisory surface only: it presents state and invokes backend operations.

### Experiment controller

The controller coordinates deterministic auditions and records configuration, seeds, parameter states, judgments, artifact paths, and lifecycle status. It does not implement plugin hosting, signal analysis, or model training.

### CLAP host and synth controller

This boundary will load the pinned `clap-saw-demo` build, discover parameters directly from the plugin, read and modify state, and restore prior or locked state. PR 1 must evaluate existing reliable host/library options before custom host work. No parameter names or semantics may be invented.

### Audio renderer

The renderer will apply one fixed audition input with explicit pitch or chord, velocity, note duration, sample rate, and deterministic timing. Rendering must be callable without the GUI.

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

Every roadmap milestone is a small PR with independently reviewable evidence. PR 0 contains only architecture, governance, package boundaries, and validation scaffolding. PR 1 alone owns CLAP feasibility and must not begin before human approval of PR 0.

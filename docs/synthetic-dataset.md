# Milestone 6: bounded synthetic dataset

## Result

Milestone 6 renders and measures 256 deterministic examples from the pinned real `clap-saw-demo`. The committed [dataset evidence](evidence/milestone-6-synthetic-dataset.json) contains exact parameter values, WAV hashes, two-render verification, DSP features, sampling metadata, and hashes binding it to the canonical state, accepted audition, completed human calibration, and Milestone 5 measurements.

All 256 WAVs are unique, all were rendered twice byte-identically, none clipped, and the first row exactly reproduces the accepted Milestone 3 WAV. The roughly 108 MiB of WAV audio remains under ignored `work/dataset/audio/`; committing it would add repository weight without improving the machine-readable evidence.

## Authorized parameter boundary

This first dataset uses three parameters chosen by the owner from the five Milestone 4 human-YES results:

| Real CLAP parameter | ID | Discovered range | Sampling treatment |
|---|---:|---:|---|
| Filter Type | `14255` | `0–5` | Stepped categories, balanced across the dataset |
| Cutoff in Keys | `17` | `1–127` | Continuous stratified coverage |
| Amplitude Attack (s) | `2874` | `0–1` | Continuous stratified coverage |

Filter Type values remain opaque plugin categories. The system does not invent names such as “low-pass” or “high-pass,” because the plugin inventory did not provide them.

## Sampling contract

- Seed: `20260927`, using NumPy `PCG64`.
- Row 0: exact canonical baseline.
- Rows 1–255: independent one-dimensional stratification for Cutoff and Attack, with one jittered point per stratum.
- Filter Type: deterministic balanced allocation with counts `43, 43, 43, 43, 42, 42` for values `0–5`.
- Every row explicitly applies all three parameters to the same canonical opaque state before the fixed C3 audition.
- Every row is rendered twice; different bytes or renderer metadata fail generation.
- Audio is measured with the unchanged Milestone 5 descriptor contract and no loudness normalization.

This is a compact space-filling design, not a factorial grid and not evidence that 256 points are sufficient for a reliable surrogate.

## Reproduce

On Apple Silicon macOS with Xcode command-line tools, CMake, Git, and the pinned Python dependencies installed:

```bash
scripts/generate_synthetic_dataset.sh
```

The default writes ignored WAVs and runtime evidence under `work/dataset/`. To reproduce the committed evidence file explicitly:

```bash
scripts/generate_synthetic_dataset.sh work/dataset docs/evidence/milestone-6-synthetic-dataset.json 256
```

To inspect parameter coverage, exact values, DSP outputs, and individual WAVs:

```bash
scripts/serve_dataset_report.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765). The read-only server verifies all 256 local WAV hashes before serving the page or audio.

## Observed range

The dataset spans RMS from `-66.158` to `-14.653 dBFS`, peak from `-55.661` to `-4.010 dBFS`, spectral centroid from `15.6` to `13,452.8 Hz`, and 85% rolloff from `21.5` to `17,506.5 Hz`. No release measurement was tail-censored in these 256 rows.

The wide range is useful but exposes a future modeling risk: some parameter combinations are extremely quiet. Milestone 7 must report held-out behavior honestly and must not silently discard, normalize, or rebalance these states after seeing model results.

## Boundary

This milestone contains no train/test split, fitted model, prediction, feature selection, inverse search, candidate ranking, or LOCK. The human YES results authorized which axes to sample; they are not labels for the 256 combinations, and no combination has been perceptually approved.

# Milestone 7: learned surrogate and training cockpit

## Result

Milestone 7 trains real local models on the frozen 256-example Milestone 6 dataset and answers one bounded question: can exact Filter Type, Cutoff, and Attack states predict the six measured DSP descriptors better than a trivial mean predictor on unseen examples?

The deterministic split contains 166 TRAIN, 38 VALIDATION, and 52 TEST examples, stratified across the six opaque Filter Type categories. Random Forest won the predeclared validation rule and materially beat the mean baseline on the final test in aggregate, but the milestone's complete success criteria **failed**: RMS MAE was `2.059 dBFS` against a `≤2.0 dBFS` threshold, and Attack improved only `22.9%` over the trivial predictor against the predeclared `≥30%` useful-target threshold.

That failure is the result. No thresholds or model settings were changed after the test reveal.

## Authority and frozen inputs

- Dataset: [Milestone 6 evidence](evidence/milestone-6-synthetic-dataset.json), SHA-256 `9942500229339874cf231e244300cdfe2e70811957119f904646b63290c2c3ae`.
- Inputs: Filter Type (`14255`), Cutoff in Keys (`17`), and Amplitude Attack (`2874`).
- Filter Type is fixed one-hot categorical data; the pipeline never interprets `0–5` as an ordered physical scale.
- Cutoff and Attack remain continuous.
- No rows—including extremely quiet examples—are removed or loudness-normalized.
- Outputs: RMS, peak, spectral centroid, 85% rolloff, attack duration, and release duration.

Release remains in the output contract, but it is flagged as low-information by a rule fixed before test evaluation: the TRAIN range is `0.0296 s` and standard deviation is `0.00462 s`, both below the declared `0.05 s` and `0.01 s` limits. Release is therefore reported and predicted but excluded from model selection.

## Leakage-safe protocol

The workflow writes `config.json` and `split.json` before fitting. It trains and compares candidates using TRAIN and VALIDATION only, selects the lowest validation mean normalized MAE across the five predeclared selection targets, fits the selected family on TRAIN plus VALIDATION, serializes the final pipeline, and writes `freeze.json` containing hashes for the configuration, split, artifact, thresholds, preprocessing, and selected model.

Only after that immutable freeze record exists does the implementation materialize TEST targets and evaluate the final model once. The freeze hash is checked again after evaluation. The committed [split manifest](evidence/milestone-7/split.json) records every sample ID, and the [freeze record](evidence/milestone-7/freeze.json) proves the declared ordering.

## Compared models

| Candidate | Preprocessing | Fixed model setting | Validation mean normalized MAE |
|---|---|---|---:|
| Mean | one-hot Filter Type; continuous passthrough | mean strategy | `0.1484` |
| Regularized linear | one-hot Filter Type; standardized continuous inputs | Ridge `alpha=1.0` | `0.1092` |
| **Random Forest** | one-hot Filter Type; continuous passthrough | 256 trees, depth 12, leaf minimum 2 | **`0.0921`** |

Gradient Boosting and neural networks were not added. The three required models were sufficient to answer this feasibility question, and an iterative loss display would not justify another model family by itself.

## Final untouched TEST metrics

The selected Random Forest was refitted on the 204 TRAIN plus VALIDATION examples after family/configuration freeze, then evaluated on the 52 untouched TEST examples.

| DSP target | MAE | Normalized MAE | R² | Spearman | MAE improvement vs mean | ≥30% |
|---|---:|---:|---:|---:|---:|:---:|
| RMS | `2.059 dBFS` | `0.0460` | `0.897` | `0.873` | `67.1%` | PASS |
| Peak | `0.973 dBFS` | `0.0206` | `0.967` | `0.930` | `77.0%` | PASS |
| Spectral centroid | `197.8 Hz` | `0.0147` | `0.930` | `0.917` | `81.6%` | PASS |
| 85% rolloff | `288.3 Hz` | `0.0165` | `0.931` | `0.930` | `81.2%` | PASS |
| Attack duration | `0.396 s` | `0.2675` | `0.338` | `0.620` | `22.9%` | **FAIL** |
| Release duration | `0.00149 s` | `0.0503` | `0.539` | `0.448` | `37.8%` | PASS, low-information target |

Predeclared gates:

- Learned model beats mean aggregate: **PASS** (`0.0731` vs `0.1487` normalized MAE).
- Every useful selection target improves MAE by at least 30%: **FAIL** (Attack).
- RMS MAE ≤ `2.0 dBFS`: **FAIL** (`2.059 dBFS`).
- Spectral-centroid Spearman ≥ `0.8`: **PASS** (`0.917`).
- Overall predeclared feasibility result: **FAIL**.

## Learning and generalization diagnostics

Random Forest validation normalized MAE improved from `0.1116` with 32 training examples to `0.0921` with all 166, while training error reached `0.0608`. More data was still helping, but the persistent gap indicates some overfitting.

The separate structured diagnostic withheld the upper-Cutoff (`≥85`) and upper-Attack (`≥0.667 s`) region: 31 examples were excluded while a diagnostic model trained on the other 225. RMS MAE degraded to `5.833 dBFS`, centroid MAE to `871.5 Hz`, and Attack MAE to `0.941 s`. Centroid ordering remained strong (`Spearman 0.978`), but this region check shows the model is substantially better at local interpolation than at generalizing into a jointly withheld region.

Validation permutation importance ranks Filter Type first, then Cutoff, then Attack. These are predictive sensitivity measurements, not causal or perceptual claims.

## Real-synth integrity check

Three deterministic states from the untouched TEST split—selected at low, middle, and high Cutoff positions—were rerendered twice through the actual pinned `clap-saw-demo`. Every rerender matched the original dataset WAV hash and all DSP values exactly, with zero clipped samples. The rerenders were not added to training; see [the compact check](evidence/milestone-7/rerender-check.json).

## Reproduce locally

Install the pinned dependencies into the active Python environment, then run:

```bash
python3 -m pip install -e .
scripts/train_surrogate.sh
```

Training writes ignored runtime artifacts to `work/milestone-7/`, including the serialized `model.joblib`, full split and freeze records, predictions, metrics, learning-curve data, regional diagnostics, status transitions, and rerender WAVs.

Open the read-only cockpit with:

```bash
scripts/serve_surrogate_report.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765). The server verifies dataset, configuration, split, freeze, prediction, and model hashes before exposing the dashboard.

The compact committed evidence lives under [`docs/evidence/milestone-7/`](evidence/milestone-7/). The joblib artifact is trusted repository evidence, not an interchange format; never load an untrusted pickle/joblib file.

## Boundary

This result supports only a bounded parameter-state → DSP approximation inside the sampled region. It does not establish that the synth is fully learned, that the model understands timbre, that descriptors predict perceptual quality, or that the model generalizes beyond this experiment. There is no inverse search, “make darker” optimizer, candidate ranking, human KEEP/REJECT loop, or LOCK implementation in this milestone.

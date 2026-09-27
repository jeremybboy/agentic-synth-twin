# Milestone 5: deterministic DSP measurements

## Result

Milestone 5 reproduces all ten completed Milestone 4 A/B probes on the pinned real `clap-saw-demo`, verifies every WAV against its render manifest, and measures a deliberately small descriptor set. The committed [measurement evidence](evidence/milestone-5-dsp-measurements.json) is cryptographically bound to the completed calibration result and preserves the human YES/NO labels as observations rather than inferred targets.

No audio is normalized. That is intentional: level changes were audible during calibration and must remain visible instead of being silently removed.

## Descriptor contract

The analyzer accepts uncompressed mono or stereo 16-bit PCM WAV files. For stereo, level and envelope use mean-square energy across channels; spectra sum power across channels. This avoids phase cancellation from a naïve mono average while preserving the rendered signal unchanged.

| Descriptor | Exact definition |
|---|---|
| RMS | Whole-file root mean square, reported in dBFS |
| Peak | Whole-file absolute sample peak, reported in dBFS |
| Spectral centroid | Power-weighted frequency mean over summed 2,048-sample Hann-windowed spectra, 512-sample hop |
| Spectral rolloff | First frequency bin containing 85% of summed spectral power |
| Attack | First 10% to first 90% of the note-region peak on a 10 ms moving-RMS envelope |
| Release | 90% to 10% of the median 50 ms pre-note-off envelope; `null` when the fixed tail ends first |

Every numeric delta is **B minus A**. These are compact engineering descriptors, not perceptual models, semantic embeddings, loudness standards, or causal explanations.

## Reproduce

On Apple Silicon macOS with Xcode command-line tools, CMake, Git, and NumPy installed:

```bash
scripts/measure_calibration_dsp.sh
```

The default writes ignored runtime renders and JSON under `work/dsp/`. To reproduce the committed evidence path explicitly:

```bash
scripts/measure_calibration_dsp.sh work/dsp docs/evidence/milestone-5-dsp-measurements.json
```

To inspect the verified comparison locally:

```bash
scripts/serve_dsp_report.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765). The report is read-only, serves the exact regenerated A/B WAVs only after checking their hashes, and places the human answer beside the measured deltas.

## What the first run actually shows

- The two strongest centroid increases were human YES results: Cutoff in Keys (`+288.7 Hz`) and Filter Type (`+260.2 Hz`).
- Large measured changes did not guarantee a YES. Pre Filter VCA changed RMS by `-6.021 dB`, Resonance by `+3.692 dB`, and Unison Count by `-3.006 dB`; all three were judged NO.
- Amplitude Attack was judged YES and changed RMS by `-10.202 dB`, but that does not prove level was the sole perceptual cause.
- The maximum Amplitude Release probe did not decay to 10% inside the fixed half-second tail. Its release value is explicitly censored rather than fabricated.

The honest conclusion is narrow: the descriptors detect several real changes, but this single ten-probe session does not establish thresholds, rankings, causality, or general perceptual prediction.

## Boundary

This milestone does not generate a synthetic dataset, choose a reduced parameter space, train a model, optimize a target, or implement LOCK. The next milestone must decide its sampling design and artifact-storage policy before generating combinations.

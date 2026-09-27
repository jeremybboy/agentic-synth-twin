# Milestone 4: terminal human A/B probe

## Result

Milestone 4 adds a local, terminal-only workflow for changing one real CLAP parameter at a time, rendering deterministic A/B files, playing them in a fixed order, and recording a human YES/NO judgment. It does not add or depend on a browser interface.

The first completed probe used the real discovered parameter **Unison Count** (`id 1378`), with A at the accepted canonical value `3` and B at `7`. The renderer reported that the plugin applied `7`; both sides were byte-identical across two renders and contained no clipped samples. A reproduced the accepted Milestone 3 WAV exactly.

The human answer was **YES**, with the note: “Small difference, especially the volume, but audibly different.” This establishes only that this particular A/B movement was perceptible in this listening event.

## Probe contract

- A is always rerendered from the exact accepted canonical state.
- B starts from that same opaque state and changes exactly one parameter discovered from the real plugin.
- The requested value must be within the discovered range and differ from the baseline.
- The renderer reports the requested and retained parameter value.
- A and B are each rendered twice and must be byte-identical within their side.
- A must reproduce the accepted Milestone 3 WAV hash.
- The machine records structure, hashes, parameter application, peak, and clipping.
- Only the listener can answer whether the difference is meaningful.

The completed [probe manifest](evidence/milestone-4-unison-count/ab-probe.json), [A baseline](evidence/milestone-4-unison-count/A-baseline.wav), and [B variant](evidence/milestone-4-unison-count/B-variant.wav) are committed as the first bounded evidence set.

## Run another probe

On the validated macOS environment:

```bash
scripts/run_terminal_ab_probe.sh PARAMETER_ID PARAMETER_VALUE
```

With no arguments, the script reproduces the first probe (`1378`, `7`). It builds the pinned synth and renderer, verifies the A/B evidence, plays A then B with `afplay`, and accepts `y`, `n`, or `r` to replay. Runtime results are written under `work/` unless an explicit output directory is provided.

## Honest interpretation

This was not a blind test and contained one presentation of each sound. More importantly, no loudness normalization or DSP measurement exists yet: B's floating-point peak (`0.230041`) differs from A's (`0.296464`), and the listener specifically noticed volume. Therefore the result does **not** yet prove that Unison Count creates a meaningful timbral difference independent of level.

Milestone 5 may measure a small declared descriptor set, including level, but must not retroactively convert this human observation into a stronger claim. Datasets, ML, optimization, and LOCK remain unimplemented.

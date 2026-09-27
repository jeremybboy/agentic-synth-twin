# Milestone 3: deterministic C3 audition

## Result

The pinned real `clap-saw-demo` now renders one fixed, traceable audition from the exact Milestone 2 canonical state. The committed [WAV evidence](evidence/milestone-3-c3.wav) and [render manifest](evidence/milestone-3-c3.json) record the complete input, state hashes, output hashes, format, and repeatability result.

Two independent renders produced byte-identical WAV files with SHA-256 `e55d49128ae37298d5b036adea9cbb98844800a1345b852cfa7d652d07d6e475`. The output is non-silent, has a peak floating-point magnitude of `0.296464`, and has zero samples outside the PCM range.

## Fixed audition contract

| Field | Value |
|---|---:|
| Pitch | scientific C3, MIDI key 48 |
| Velocity event | 100 / 127 |
| Note hold | 2.0 seconds |
| Release tail | 0.5 seconds |
| Sample rate | 44,100 Hz |
| Processing block | 64 frames |
| Output | stereo, 16-bit PCM WAV |
| Total output | 110,250 frames / 2.5 seconds |

The release tail is fixed separately so the two-second note receives a note-off event and its envelope can finish without an arbitrary hard cut at the note boundary.

## State and rendering path

`scripts/clap_render.cpp` is a deliberately small offline CLAP renderer. It loads the plugin, restores the exact 97-byte opaque state from the canonical JSON, activates at 44.1 kHz, sends sample-timed CLAP note-on and note-off events, processes fixed-size blocks, and writes PCM WAV.

`agentic_synth_twin.audition` owns orchestration and evidence. It validates canonical state, invokes the renderer twice, requires exact WAV and renderer-metadata equality, verifies WAV structure and non-silence, rejects clipping, and writes the traceable manifest. This remains an offline evidence path, not a general-purpose plugin host.

## Listen in the browser

The preview is intentionally thin and contains one PLAY button plus factual render metadata. It does not own synth or experiment logic.

```bash
scripts/serve_audition_preview.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765). The committed evidence WAV is served directly, so listening does not require rebuilding the plugin.

## Reproduce the real render

```bash
scripts/render_deterministic_audition.sh
```

This rebuilds the pinned plugin, compiles the renderer, restores canonical state, renders twice, and writes ignored runtime artifacts under `work/audition/` by default.

## Known limitations

- The pinned synth ignores the velocity value in its note handler. Velocity 100 is sent and recorded for a stable future contract, but it has no sonic effect in this upstream revision.
- Byte identity is verified across two runs on the test Mac. Cross-platform or cross-toolchain byte identity is not claimed.
- Automated checks prove structure, traceability, non-silence, lack of clipping, and repeatability; they do not prove musical or perceptual quality.
- The browser remains a one-action Milestone 3 artifact. Milestone 4 A/B probing and judgments are implemented separately in the terminal; no browser controls were added.

Human listening remains required acceptance evidence.

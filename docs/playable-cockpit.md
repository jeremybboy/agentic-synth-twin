# Milestone 8B: playable patch cockpit

Milestone 8B makes the existing direct-search result useful as an instrument audition. The reference Target, Starting Patch, and Current Best evidence remain unchanged. A separate playable working copy loads Starting or Current Best, exposes the same four parameters discovered from the real plugin, and lets the user change them without rewriting search history or its best result.

## Inputs and patch state

The on-screen two-octave piano, mapped computer keys, and optional browser Web MIDI input all call the same note-on/note-off functions. The computer mapping follows Ableton's visible layout: `A S D F G H J K L` play white notes, `W E T Y U O` play black notes, `Z/X` shifts the octave, and `C/V` changes velocity. These shortcuts remain active after clicking buttons or parameter sliders. Octave controls move the visible keyboard through the standard piano range, MIDI 21–108. Velocity is bounded to 1–127. Web MIDI is optional because browser support and the required user permission vary; the virtual and computer keyboards work without it.

The editable controls are exactly the four Milestone 8A search coordinates discovered from `clap-saw-demo`:

- Oscillator Detuning, ID `8675309`, range `-200..200`
- Unison Spread, ID `2391`, range `0..100`
- Cutoff in Keys, ID `17`, range `1..127`
- Amplitude Attack, ID `2874`, range `0..1`

Filter Type remains fixed. Loading Current Best copies its normalized vector into the working controls. Any slider movement creates an edited working vector; it does not mutate the stored candidate, target, optimizer, or evidence.

## Rendered-note contract

Each unseen `(patch vector, MIDI key, velocity)` combination invokes the real CLAP plugin through the backend renderer. The note is held for six seconds, followed by the existing half-second plugin release tail, at 44.1 kHz with 64-frame processing blocks. The backend validates the piano key, velocity, normalized parameter bounds, renderer metadata, applied parameters, clipping count, and output file before serving the WAV. A content-derived filename caches the verified result locally under the ignored run directory.

The browser decodes that WAV and starts a voice. Multiple decoded buffers may overlap, so simple chords are possible. Releasing a key applies a short browser gain fade; it does not send a live note-off event back into a persistent plugin process. The first strike of a new combination therefore has render/decode latency, while cached strikes are immediate on the test machine.

## Honest boundary

This is a practical rendered-note instrument, not a native realtime CLAP host. It does not promise audio-device latency, sustain pedal behavior, aftertouch, pitch bend, a plugin-authentic early release envelope, or uninterrupted notes beyond six seconds. Those require a separately authorized persistent audio/MIDI host. It also does not repair the failed Milestone 8A adaptive-search comparison or claim that the numeric objective matches human preference.

## Run locally

```bash
scripts/run_direct_search.sh
```

Open `http://127.0.0.1:8765`. The playable Starting patch is available immediately. After a search has produced a candidate, press **Load Current Best** to copy it into the editable instrument. Press **Connect USB MIDI** only when using a compatible browser and controller; macOS may display a browser permission prompt.

# Milestone 4: local human A/B calibration

## Result

Milestone 4 adds a local server-based calibration app. It prepares deterministic one-parameter A/B probes from the real CLAP inventory, randomizes probe order and A/B placement, presents them as neutral **Sound 1** and **Sound 2**, requires both to be heard, records a clicked YES/NO judgment directly in SQLite, and advances automatically.

The browser is a thin client: it does not render the synth, choose parameter values, own experiment state, or write files directly. The Python backend validates audio evidence, controls the session, enforces the listening rule, persists judgments transactionally, and exports machine-readable results. The server binds only to loopback and adds no public hosting or cloud service.

## Probe contract

- Every probe starts from the exact accepted canonical state.
- B changes exactly one parameter discovered from the real plugin.
- The default plan uses a strong discovered-range movement without assigning invented semantics: maximum when available; for a continuous parameter already at maximum, midpoint; for a stepped parameter already at maximum, minimum. Silent renders are rejected.
- The renderer reports the requested and retained real-synth value.
- A and B are each rendered twice and must be byte-identical within their side.
- A must reproduce the accepted Milestone 3 WAV hash.
- Probe order and left/right A/B placement are randomized and stored with the session seed.
- The browser does not reveal parameter identity or A/B placement during listening.
- YES/NO remains a human observation, separate from render verification.

## Run locally

```bash
scripts/run_local_calibration.sh
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The first run builds the pinned synth and renderer, prepares the probe files under `work/calibration/probes/`, and creates `work/calibration/calibration.sqlite3`. Later runs resume the same session; pass another output directory as the first argument to create an independent session.

For each probe, play Sound 1 and Sound 2 in either order. The answer buttons remain disabled until both have been played. Click YES when the difference is meaningful or NO when it is not; the response is committed to SQLite and the next randomized probe appears. On completion, the app exposes a JSON export derived from the database.

## Completed blinded session

The first complete [calibration result](evidence/milestone-4-calibration-results.json) contains all ten judgments, the randomization seed, hidden A/B placement, play counts, values, and timestamps. The listener answered YES for five movements: Oscillator Detuning, Unison Spread, Filter Type, Cutoff, and Amplitude Attack. The other five received NO.

This is successful screening evidence, not a ranking. Notably, the blinded session gave Unison Count `3 → 7` a NO after the earlier unblinded pilot received a weak YES; the contradiction is preserved rather than resolved by assumption.

## Pilot evidence and honest interpretation

Before the browser workflow was finalized, the first pilot used the real discovered parameter **Unison Count** (`id 1378`), with A at `3` and B at `7`. The renderer confirmed that the plugin retained `7`; both sides were deterministic and unclipped, and A reproduced Milestone 3 exactly. The human answer was **YES**, with the note: “Small difference, especially the volume, but audibly different.” The [pilot manifest](evidence/milestone-4-unison-count/ab-probe.json) and WAV files remain committed as evidence of the backend path, not as a substitute for the blinded session.

The default range plan is intentionally crude: it tests whether a parameter can create a perceptible movement, not whether the chosen value is musically useful or defines a sensible local sampling range. No loudness normalization or DSP measurement exists yet. In the pilot, B's floating-point peak (`0.230041`) differed from A's (`0.296464`), and the listener noticed volume; this does not prove an independent timbral effect.

Milestone 5 may measure a small declared descriptor set, including level, focused first on the five YES parameters and ambiguous level-driven cases. It must not retroactively strengthen human observations. Synthetic datasets, ML, optimization, and LOCK remain unimplemented.

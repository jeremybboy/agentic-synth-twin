# Agentic Synth Twin — External Targets v1

This pack contains 10 deterministic reference sounds for evaluating:
external target audio -> Surge XT preset retrieval -> bounded parameter refinement -> best playable Surge state.

## Important provenance
- Surge XT was NOT used to create these reference sounds.
- No third-party samples were used.
- No preset library was used.
- All sounds were synthesized procedurally with original Python/NumPy DSP.
- The generation seed is recorded in `manifest.json`.

## Common file contract
- Nominal note: C3 (130.8127826502993 Hz)
- Sample rate: 44.1 kHz
- Format: 16-bit stereo WAV, dual mono
- File duration: 2.5 s
- Reference timing: 2.0 s note window + 0.5 s tail
- Percussive sounds naturally decay before 2.0 s.

## Targets
1. Analog Sub Bass
2. FM Bell
3. Plucked Electric Guitar
4. Rhodes-style Electric Piano
5. Muted Synth Pluck
6. Analog Brass Stab
7. Warm Poly Pad
8. Sync / Hard Lead
9. Marimba / Mallet
10. Dub Chord / Organ Stab

See `manifest.json` for the exact generation description and SHA-256 of every WAV.

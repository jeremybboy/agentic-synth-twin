"""Backend boundary for the local Agentic Synth Twin experiment.

The current implementation validates canonical synth state, deterministic
audition evidence, local browser A/B calibration, bounded DSP measurement, a
256-example traceable real-synth dataset, and a locally trained surrogate with
held-out diagnostics. Optimization and LOCK remain future milestones.
"""

__version__ = "0.0.0"

#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
port=${1:-8765}

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.dsp_report \
    --measurements "$repository_root/docs/evidence/milestone-5-dsp-measurements.json" \
    --audio-root "$repository_root/work/dsp/probes" \
    --host 127.0.0.1 \
    --port "$port"

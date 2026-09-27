#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
port=${1:-8765}

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.preview \
    --wav "$repository_root/docs/evidence/milestone-3-c3.wav" \
    --manifest "$repository_root/docs/evidence/milestone-3-c3.json" \
    --host 127.0.0.1 \
    --port "$port"

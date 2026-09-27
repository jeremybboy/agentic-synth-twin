#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
port=${1:-8765}

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.dataset_report \
    --dataset "$repository_root/docs/evidence/milestone-6-synthetic-dataset.json" \
    --audio-root "$repository_root/work/dataset/audio" \
    --host 127.0.0.1 \
    --port "$port"

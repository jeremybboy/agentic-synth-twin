#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
evidence_root=${1:-"$repository_root/work/milestone-7"}
port=${2:-8765}
python_bin=${PYTHON_BIN:-python3}

PYTHONPATH="$repository_root/src" "$python_bin" -m agentic_synth_twin.surrogate_report \
    --results "$evidence_root/results.json" \
    --predictions "$evidence_root/predictions.json" \
    --status "$evidence_root/status.json" \
    --dataset "$repository_root/docs/evidence/milestone-6-synthetic-dataset.json" \
    --host 127.0.0.1 \
    --port "$port"

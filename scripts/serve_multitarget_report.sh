#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output_directory=${1:-"$repository_root/work/milestone-8b-validation"}
port=${2:-8766}
python_bin=${PYTHON_BIN:-"$repository_root/.venv/bin/python3"}

if [ ! -x "$python_bin" ]; then
    python_bin=$(command -v python3)
fi

PYTHONPATH="$repository_root/src" "$python_bin" -m agentic_synth_twin.multitarget_report \
    --output "$output_directory" \
    --host 127.0.0.1 \
    --port "$port"

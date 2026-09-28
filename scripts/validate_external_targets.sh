#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${PYTHON_BIN:-"$repository_root/.venv/bin/python3"}
target_directory=${1:-"$repository_root/examples/targets/external-v1"}

if [ ! -x "$python_bin" ]; then
    python_bin=$(command -v python3)
fi

PYTHONPATH="$repository_root/src" "$python_bin" \
    -m agentic_synth_twin.external_targets "$target_directory"

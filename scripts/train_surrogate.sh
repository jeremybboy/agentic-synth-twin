#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_root="$repository_root/work/upstream/clap-saw-demo"
plugin_bundle="$source_root/ignore/build-pr1/clap-saw-demo.clap"
renderer="$repository_root/work/bin/clap-render-m7"
dataset="$repository_root/docs/evidence/milestone-6-synthetic-dataset.json"
canonical_state="$repository_root/docs/evidence/milestone-2-canonical-state.json"
output_directory=${1:-"$repository_root/work/milestone-7"}
python_bin=${PYTHON_BIN:-python3}

if ! "$python_bin" -c 'import joblib, numpy, scipy, sklearn, threadpoolctl' >/dev/null 2>&1; then
    echo "Milestone 7 requires the pinned project dependencies." >&2
    echo "Install them with: python3 -m pip install -e ." >&2
    echo "Or set PYTHON_BIN to a Python environment where they are installed." >&2
    exit 1
fi

"$repository_root/scripts/build_clap_feasibility.sh" >/dev/null
mkdir -p "$repository_root/work/bin" "$output_directory"
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_render.cpp" \
    -o "$renderer"

PYTHONPATH="$repository_root/src" "$python_bin" -m agentic_synth_twin.surrogate \
    --dataset "$dataset" \
    --output "$output_directory" \
    --repository-root "$repository_root" \
    --state "$canonical_state" \
    --plugin "$plugin_bundle" \
    --renderer "$renderer"

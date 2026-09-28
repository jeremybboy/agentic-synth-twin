#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_root="$repository_root/work/upstream/clap-saw-demo"
plugin_bundle="$source_root/ignore/build-pr1/clap-saw-demo.clap"
renderer="$repository_root/work/bin/clap-render-m8a"
canonical_state="$repository_root/docs/evidence/milestone-2-canonical-state.json"
output_directory=${1:-"$repository_root/work/milestone-8a-live"}
port=${2:-8765}
python_bin=${PYTHON_BIN:-"$repository_root/.venv/bin/python3"}

if [ ! -x "$python_bin" ]; then
    python_bin=$(command -v python3)
fi

if [ ! -x "$plugin_bundle/Contents/MacOS/clap-saw-demo" ]; then
    "$repository_root/scripts/build_clap_feasibility.sh" >/dev/null
fi
mkdir -p "$repository_root/work/bin" "$output_directory"
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_render.cpp" \
    -o "$renderer"

PYTHONPATH="$repository_root/src" "$python_bin" -m agentic_synth_twin.search_cockpit \
    --state "$canonical_state" \
    --plugin "$plugin_bundle" \
    --renderer "$renderer" \
    --output "$output_directory" \
    --host 127.0.0.1 \
    --port "$port"

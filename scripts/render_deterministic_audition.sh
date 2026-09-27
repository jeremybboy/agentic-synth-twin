#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_root="$repository_root/work/upstream/clap-saw-demo"
plugin_bundle="$source_root/ignore/build-pr1/clap-saw-demo.clap"
renderer="$repository_root/work/bin/clap-render"
canonical_state="$repository_root/docs/evidence/milestone-2-canonical-state.json"
output_directory=${1:-"$repository_root/work/audition"}

"$repository_root/scripts/build_clap_feasibility.sh" >/dev/null
mkdir -p "$repository_root/work/bin" "$output_directory"
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_render.cpp" \
    -o "$renderer"

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.audition \
    --state "$canonical_state" \
    --plugin "$plugin_bundle" \
    --renderer "$renderer" \
    --wav "$output_directory/c3-audition.wav" \
    --manifest "$output_directory/c3-audition.json"

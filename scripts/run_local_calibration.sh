#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_root="$repository_root/work/upstream/clap-saw-demo"
plugin_bundle="$source_root/ignore/build-pr1/clap-saw-demo.clap"
renderer="$repository_root/work/bin/clap-render"
canonical_state="$repository_root/docs/evidence/milestone-2-canonical-state.json"
accepted_manifest="$repository_root/docs/evidence/milestone-3-c3.json"
accepted_wav="$repository_root/docs/evidence/milestone-3-c3.wav"
output_directory=${1:-"$repository_root/work/calibration"}
port=${2:-8765}
database="$output_directory/calibration.sqlite3"

"$repository_root/scripts/build_clap_feasibility.sh" >/dev/null
mkdir -p "$repository_root/work/bin" "$output_directory"
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_render.cpp" \
    -o "$renderer"

if [ ! -f "$database" ]; then
    PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.calibration prepare \
        --state "$canonical_state" \
        --accepted-manifest "$accepted_manifest" \
        --accepted-wav "$accepted_wav" \
        --plugin "$plugin_bundle" \
        --renderer "$renderer" \
        --output "$output_directory" \
        --database "$database"
fi

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.calibration serve \
    --database "$database" \
    --host 127.0.0.1 \
    --port "$port"

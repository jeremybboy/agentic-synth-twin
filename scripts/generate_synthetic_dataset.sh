#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_root="$repository_root/work/upstream/clap-saw-demo"
plugin_bundle="$source_root/ignore/build-pr1/clap-saw-demo.clap"
renderer="$repository_root/work/bin/clap-render"
canonical_state="$repository_root/docs/evidence/milestone-2-canonical-state.json"
accepted_manifest="$repository_root/docs/evidence/milestone-3-c3.json"
accepted_wav="$repository_root/docs/evidence/milestone-3-c3.wav"
calibration_results="$repository_root/docs/evidence/milestone-4-calibration-results.json"
dsp_measurements="$repository_root/docs/evidence/milestone-5-dsp-measurements.json"
output_directory=${1:-"$repository_root/work/dataset"}
evidence_output=${2:-"$output_directory/milestone-6-synthetic-dataset.json"}
sample_count=${3:-256}

"$repository_root/scripts/build_clap_feasibility.sh" >/dev/null
mkdir -p "$repository_root/work/bin" "$output_directory/audio"
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_render.cpp" \
    -o "$renderer"

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.dataset \
    --state "$canonical_state" \
    --accepted-manifest "$accepted_manifest" \
    --accepted-wav "$accepted_wav" \
    --calibration-results "$calibration_results" \
    --dsp-measurements "$dsp_measurements" \
    --plugin "$plugin_bundle" \
    --renderer "$renderer" \
    --audio "$output_directory/audio" \
    --output "$evidence_output" \
    --count "$sample_count"

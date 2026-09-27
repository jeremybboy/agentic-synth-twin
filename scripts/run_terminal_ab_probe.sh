#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_root="$repository_root/work/upstream/clap-saw-demo"
plugin_bundle="$source_root/ignore/build-pr1/clap-saw-demo.clap"
renderer="$repository_root/work/bin/clap-render"
canonical_state="$repository_root/docs/evidence/milestone-2-canonical-state.json"
accepted_manifest="$repository_root/docs/evidence/milestone-3-c3.json"
accepted_wav="$repository_root/docs/evidence/milestone-3-c3.wav"
parameter_id=${1:-1378}
parameter_value=${2:-7}
output_directory=${3:-"$repository_root/work/ab-probe-$parameter_id"}

if ! command -v afplay >/dev/null 2>&1; then
    echo "run_terminal_ab_probe: afplay is required on macOS" >&2
    exit 1
fi

"$repository_root/scripts/build_clap_feasibility.sh" >/dev/null
mkdir -p "$repository_root/work/bin" "$output_directory"
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_render.cpp" \
    -o "$renderer"

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.ab_probe generate \
    --state "$canonical_state" \
    --accepted-manifest "$accepted_manifest" \
    --accepted-wav "$accepted_wav" \
    --plugin "$plugin_bundle" \
    --renderer "$renderer" \
    --output "$output_directory" \
    --parameter-id "$parameter_id" \
    --parameter-value "$parameter_value"

while :; do
    printf '\nPlaying A: accepted canonical baseline\n'
    afplay "$output_directory/A-baseline.wav"
    printf 'Playing B: one-parameter variant\n'
    afplay "$output_directory/B-variant.wav"
    printf '\nDid B create a meaningful audible difference from A? [y/n/r replay] '
    read -r answer
    case "$answer" in
        y|Y|yes|YES)
            recorded_answer=yes
            break
            ;;
        n|N|no|NO)
            recorded_answer=no
            break
            ;;
        r|R|replay|REPLAY)
            ;;
        *)
            echo "Please answer y, n, or r."
            ;;
    esac
done

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.ab_probe record \
    --manifest "$output_directory/ab-probe.json" \
    --answer "$recorded_answer"

echo "Probe evidence: $output_directory/ab-probe.json"

#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
probe_binary="$repository_root/work/bin/clap-probe"
plugin_bundle="$repository_root/work/upstream/clap-saw-demo/ignore/build-pr1/clap-saw-demo.clap"
upstream_commit="f33b31fff459d66ac18207ec152b323aaa9306f9"
output_path=${1:-"$repository_root/work/evidence/canonical-synth-state.json"}
probe_output=$(mktemp "${TMPDIR:-/tmp}/agentic-synth-probe.XXXXXX")
trap 'rm -f "$probe_output"' EXIT HUP INT TERM

"$repository_root/scripts/build_clap_feasibility.sh" >/dev/null
"$probe_binary" "$plugin_bundle" >"$probe_output"

PYTHONPATH="$repository_root/src" python3 -m agentic_synth_twin.synth_state \
    --probe "$probe_output" \
    --upstream-commit "$upstream_commit" \
    --output "$output_path"

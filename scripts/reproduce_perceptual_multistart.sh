#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
version=1.3.4
upstream="$repository_root/work/upstream/surge-xt-$version"
plugin="$upstream/extracted/Surge XT.clap"
data="$upstream/content/Surge Synth Team/SurgeXTData"
renderer="$repository_root/work/bin/clap-render-surge"
probe="$repository_root/work/bin/clap-probe-surge"
target_id=${1:-rhodes-style-electric-piano}
output=${2:-"$repository_root/work/perceptual-multistart-$target_id"}
cache_root=${3:-"$repository_root/work/milestone-9-perceptual-audit/shared-preset-cache"}
python_bin=${PYTHON_BIN:-"$repository_root/.venv/bin/python3"}
targets="$repository_root/examples/targets/external-v1"

"$repository_root/scripts/setup_surge_xt.sh" >/dev/null
if [ ! -x "$python_bin" ]; then
    python_bin=$(command -v python3)
fi

PYTHONPATH="$repository_root/src" "$python_bin" -m agentic_synth_twin.surge_match refine \
    --plugin "$plugin" \
    --data "$data" \
    --renderer "$renderer" \
    --probe "$probe" \
    --output "$output" \
    --targets "$targets" \
    --target-id "$target_id" \
    --cache-root "$cache_root"

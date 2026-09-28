#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
version=1.3.4
upstream="$repository_root/work/upstream/surge-xt-$version"
plugin="$upstream/extracted/Surge XT.clap"
data="$upstream/content/Surge Synth Team/SurgeXTData"
renderer="$repository_root/work/bin/clap-render-surge"
probe="$repository_root/work/bin/clap-probe-surge"
output=${1:-"$repository_root/work/milestone-9-live"}
port=${2:-8879}
python_bin=${PYTHON_BIN:-"$repository_root/.venv/bin/python3"}

"$repository_root/scripts/setup_surge_xt.sh" >/dev/null
if [ ! -x "$python_bin" ]; then
    python_bin=$(command -v python3)
fi

PYTHONPATH="$repository_root/src" "$python_bin" -m agentic_synth_twin.surge_match serve \
    --plugin "$plugin" \
    --data "$data" \
    --renderer "$renderer" \
    --probe "$probe" \
    --output "$output" \
    --host 127.0.0.1 \
    --port "$port"

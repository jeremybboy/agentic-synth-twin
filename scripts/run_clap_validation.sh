#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tool_root="$repository_root/work/tools/clap-validator"
archive="$tool_root/clap-validator-0.4.1-127-g152b982-macos-15-aarch64.zip"
binary="$tool_root/bin/clap-validator"
plugin_bundle="$repository_root/work/upstream/clap-saw-demo/ignore/build-pr1/clap-saw-demo.clap"
release="0.4.1"
asset="clap-validator-0.4.1-127-g152b982-macos-15-aarch64.zip"
expected_sha256="719a0248ea431718bb7c92c8a0b9a78afa0bb23d692cf452e80973fe8b89282d"
expected_binary_sha256="39d19636050bf3cb55362426d335e3b660ae3589503e17004bd5e9b44db652ec"

if [ ! -d "$plugin_bundle" ]; then
    echo "build the plugin first: scripts/build_clap_feasibility.sh" >&2
    exit 1
fi

if [ ! -f "$archive" ]; then
    mkdir -p "$tool_root/download" "$tool_root/bin"
    gh release download "$release" \
        --repo free-audio/clap-validator \
        --pattern "$asset" \
        --dir "$tool_root"
fi

actual_sha256=$(shasum -a 256 "$archive" | awk '{print $1}')
if [ "$actual_sha256" != "$expected_sha256" ]; then
    echo "clap-validator archive checksum mismatch: $actual_sha256" >&2
    exit 1
fi

if [ ! -x "$binary" ]; then
    mkdir -p "$tool_root/download" "$tool_root/bin"
    ditto -x -k "$archive" "$tool_root/download"
    tar -xzf "$tool_root/download/clap-validator-0.3.2-127-g152b982-macos-15-aarch64.tar.gz" \
        -C "$tool_root/bin"
fi

actual_binary_sha256=$(shasum -a 256 "$binary" | awk '{print $1}')
if [ "$actual_binary_sha256" != "$expected_binary_sha256" ]; then
    echo "clap-validator binary checksum mismatch: $actual_binary_sha256" >&2
    exit 1
fi

"$binary" --version
"$binary" list plugins --json "$plugin_bundle"
"$binary" validate --json \
    --include '^scan-rtld-now$|^param-default-values$|^state-reproducibility-(basic|binary|buffered)$' \
    "$plugin_bundle"

#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
version=1.3.4
upstream="$repository_root/work/upstream/surge-xt-$version"
plugin_zip="$upstream/surge-xt-macos-$version-pluginsonly.zip"
content_archive="$upstream/surge-xt-portable-content-$version.tar.gz"
plugin="$upstream/extracted/Surge XT.clap"
data="$upstream/content/Surge Synth Team/SurgeXTData"
source_root="$repository_root/work/upstream/clap-saw-demo"
renderer="$repository_root/work/bin/clap-render-surge"
probe="$repository_root/work/bin/clap-probe-surge"

mkdir -p "$upstream" "$repository_root/work/bin"

if [ ! -f "$plugin_zip" ]; then
    curl -fL "https://github.com/surge-synthesizer/releases-xt/releases/download/$version/surge-xt-macos-$version-pluginsonly.zip" -o "$plugin_zip"
fi
if [ "$(md5 -q "$plugin_zip")" != "8afca4159d9b417c5e07ebc1a5e96ed3" ]; then
    echo "Surge XT plugin archive checksum mismatch" >&2
    exit 1
fi
if [ ! -x "$plugin/Contents/MacOS/Surge XT" ]; then
    mkdir -p "$upstream/extracted"
    ditto -x -k "$plugin_zip" "$upstream/extracted"
fi

if [ ! -f "$content_archive" ]; then
    curl -fL "https://github.com/surge-synthesizer/releases-xt/releases/download/$version/surge-xt-portable-content-$version.tar.gz" -o "$content_archive"
fi
if [ "$(md5 -q "$content_archive")" != "089b26486e9680aa4fe5121a7fd4c8c4" ]; then
    echo "Surge XT portable-content checksum mismatch" >&2
    exit 1
fi
if [ ! -d "$data/patches_factory" ]; then
    mkdir -p "$upstream/content"
    tar -xzf "$content_archive" -C "$upstream/content"
fi

if [ ! -d "$source_root/libs/clap/include" ]; then
    "$repository_root/scripts/build_clap_feasibility.sh" >/dev/null
fi
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_render.cpp" \
    -o "$renderer"
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_probe.cpp" \
    -o "$probe"

printf '%s\n' "plugin=$plugin" "data=$data" "renderer=$renderer" "probe=$probe"

#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_root="$repository_root/work/upstream/clap-saw-demo"
build_root="$source_root/ignore/build-pr1"
probe_binary="$repository_root/work/bin/clap-probe"
upstream_url="https://github.com/abique/clap-saw-demo.git"
upstream_commit="f33b31fff459d66ac18207ec152b323aaa9306f9"
compatibility_patch="$repository_root/scripts/patches/clap-saw-demo-vstgui-modern-clang.patch"

if [ ! -d "$source_root/.git" ]; then
    mkdir -p "$(dirname -- "$source_root")"
    git clone "$upstream_url" "$source_root"
fi

git -C "$source_root" fetch origin "$upstream_commit"
git -C "$source_root" checkout --detach "$upstream_commit"
git -C "$source_root" submodule update --init --recursive

actual_commit=$(git -C "$source_root" rev-parse HEAD)
if [ "$actual_commit" != "$upstream_commit" ]; then
    echo "unexpected clap-saw-demo commit: $actual_commit" >&2
    exit 1
fi

vstgui_root="$source_root/libs/vstgui"
if git -C "$vstgui_root" apply --reverse --check "$compatibility_patch" 2>/dev/null; then
    echo "VSTGUI compatibility patch already applied"
elif git -C "$vstgui_root" apply --check "$compatibility_patch"; then
    git -C "$vstgui_root" apply "$compatibility_patch"
else
    echo "VSTGUI compatibility patch does not apply cleanly" >&2
    exit 1
fi

cmake -S "$source_root" -B "$build_root" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    '-DCMAKE_CXX_FLAGS=-include utility'
cmake --build "$build_root" --parallel 4

mkdir -p "$(dirname -- "$probe_binary")"
c++ -std=c++17 -O2 -Wall -Wextra -Werror \
    -I "$source_root/libs/clap/include" \
    "$repository_root/scripts/clap_probe.cpp" \
    -o "$probe_binary"

plugin_bundle="$build_root/clap-saw-demo.clap"
file "$plugin_bundle/Contents/MacOS/clap-saw-demo"
"$probe_binary" "$plugin_bundle"

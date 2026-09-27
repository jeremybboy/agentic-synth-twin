#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_directory=${1:-"$repository_root/work/milestone-7"}
destination="$repository_root/docs/evidence/milestone-7"

for filename in config.json split.json freeze.json model.joblib predictions.json rerender-check.json results.json status.json; do
    if [ ! -f "$source_directory/$filename" ]; then
        echo "missing Milestone 7 artifact: $source_directory/$filename" >&2
        exit 1
    fi
done

mkdir -p "$destination"
cp "$source_directory/config.json" "$destination/config.json"
cp "$source_directory/split.json" "$destination/split.json"
cp "$source_directory/freeze.json" "$destination/freeze.json"
cp "$source_directory/model.joblib" "$destination/model.joblib"
cp "$source_directory/predictions.json" "$destination/predictions.json"
cp "$source_directory/rerender-check.json" "$destination/rerender-check.json"
cp "$source_directory/results.json" "$destination/results.json"
cp "$source_directory/status.json" "$destination/status.json"

echo "captured compact Milestone 7 evidence: $destination"

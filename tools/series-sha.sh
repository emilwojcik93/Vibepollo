#!/usr/bin/env bash
# series-sha.sh - print the sha256 of the patch series: the bytes of patches/series followed by every listed patch, in order.
set -euo pipefail
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
patch_dir=${VP_PATCH_DIR:-$script_dir/../patches}
{
  cat "$patch_dir/series"
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%%#*}; line=$(printf '%s' "$line" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    [ -n "$line" ] && cat "$patch_dir/$line"
  done < "$patch_dir/series"
} | sha256sum | cut -d' ' -f1

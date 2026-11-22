#!/usr/bin/env bash
# series-sha.sh - print the series hash (patch_series_sha256).
#
# Definition (identical in every repository that uses this scheme):
#   sha256 over the bytes of `patches/series` with CRLF normalised to LF, followed by the bytes of each patch file
#   listed in it (comments and blank lines ignored), in series order, patch files taken exactly as stored.
set -euo pipefail
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
patch_dir=${VP_PATCH_DIR:-$script_dir/../patches}
{
  tr -d '\r' < "$patch_dir/series"
  tr -d '\r' < "$patch_dir/series" | while IFS= read -r line || [ -n "$line" ]; do
    line=${line%%#*}; line=$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    [ -n "$line" ] && cat "$patch_dir/$line"
  done
} | sha256sum | cut -d' ' -f1

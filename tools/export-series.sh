#!/usr/bin/env bash
# export-series.sh - regenerate patches/*.patch and patches/series from a scratch branch of commits.
#
#   tools/export-series.sh <scratch-worktree-or-repo> <base-ref> [<head-ref>]
#
# Takes the commits <base-ref>..<head-ref> (default HEAD) of <scratch-worktree-or-repo>, which must be built on the
# pinned upstream tag, and writes them as `git format-patch` files (reproducible flags: no signature, zero commit id).
# Existing patches/*.patch are replaced; comment lines in patches/series that precede a patch name are kept for patches
# whose file name did not change. Requires bash >= 4. Afterwards run tools/apply-series.sh <tag> --verify <head-ref>
# and tools/update-lock.sh <tag>.
set -euo pipefail
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
patch_dir=${VP_PATCH_DIR:-$script_dir/../patches}
[ $# -ge 2 ] || { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 3; }
src=$1; base=$2; head=${3:-HEAD}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/export-series.XXXXXX"); trap 'rm -rf "$tmp"' EXIT
git -C "$src" format-patch --quiet --no-signature --zero-commit -N -o "$tmp/out" "$base..$head"
ls "$tmp/out"/*.patch >/dev/null 2>&1 || { echo "error: no commits in $base..$head" >&2; exit 3; }

declare -A notes=()
block=""; first=1
if [ -f "$patch_dir/series" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    case "$line" in
      '#'*) block+="$line"$'\n' ;;
      '') ;;
      *) if ((first)); then first=0; else notes[$line]=$block; fi; block="" ;;
    esac
  done < "$patch_dir/series"
fi

mkdir -p "$patch_dir"
rm -f "$patch_dir"/*.patch
cp "$tmp/out"/*.patch "$patch_dir/"
{
  echo "# Ordered patch series for Vibepollo posture. One patch file name per line."
  echo "# Blank lines and lines starting with '#' are ignored. Order matters: each patch builds on the previous one."
  echo "# Regenerate with tools/export-series.sh, never edit the .patch files by hand."
  first=1
  for f in "$tmp/out"/*.patch; do
    b=$(basename "$f")
    if ((first)); then first=0; else printf '%s' "${notes[$b]:-}"; fi
    echo "$b"
  done
} > "$patch_dir/series"
echo "exported $(ls "$tmp/out"/*.patch | wc -l) patches to $patch_dir"

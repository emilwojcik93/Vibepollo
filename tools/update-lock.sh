#!/usr/bin/env bash
# update-lock.sh - rewrite upstream.lock for <tag>, after proving the series applies to it.
#
#   tools/update-lock.sh <tag> [--reference <ref>] [--release-id <id>]
#
# Resolves the tag (annotated tags are dereferenced to the commit), applies the series with tools/apply-series.sh
# (optionally --verify <ref>), and records the tag, commit, owner, series hash and the resulting tree in upstream.lock.
set -euo pipefail
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$script_dir/.." && pwd)
repo=${VP_REPO:-$root}
[ $# -ge 1 ] || { sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 3; }
tag=$1; shift
reference=""; release_id="null"
while (($#)); do
  case "$1" in
    --reference) reference=$2; shift 2 ;;
    --release-id) release_id=$2; shift 2 ;;
    *) echo "error: unknown argument $1" >&2; exit 3 ;;
  esac
done
[[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "error: tag '$tag' is not X.Y.Z" >&2; exit 3; }
commit=$(git -C "$repo" rev-parse --verify "refs/tags/$tag^{commit}")
tag_object=$(git -C "$repo" rev-parse --verify "refs/tags/$tag")
tag_date=$(git -C "$repo" log -1 --format=%cI "$commit")
verify_args=()
if [ -n "$reference" ]; then reference=$(git -C "$repo" rev-parse --verify "$reference^{commit}"); verify_args=(--verify "$reference"); fi
out=$("$script_dir/apply-series.sh" "$tag" "${verify_args[@]}")
tree=$(sed -n 's/^OK .* tree=\([0-9a-f]*\)$/\1/p' <<<"$out")
count=$(grep -cvE '^[[:space:]]*(#|$)' "$root/patches/series")
owner=$(sed -n 's/^  *- *\(.*\)$/\1/p' "$root/policy.yml" | head -n1)
cat > "$root/upstream.lock" <<LOCK
# upstream.lock - written by tools/update-lock.sh. Do not edit by hand.
# A tag that later resolves to a different commit than recorded here must halt the pipeline.
upstream:
  repository: ${owner:-Nonary}/Vibepollo
  owner: ${owner:-Nonary}
  tag: $tag
  commit: $commit
  tag_object: $tag_object
  commit_date: $tag_date
  release_id: $release_id   # filled in by the upstream-watch workflow (Phase 2)
patch_series:
  count: $count
  sha256: $("$script_dir/series-sha.sh")   # sha256 of patches/series followed by every patch in order (tools/series-sha.sh)
  result_tree: $tree         # tree of <tag> + series; must equal the reference tree
  reference: ${reference:-null}
locked_on: $(date -u +%Y-%m-%d)
LOCK
cat "$root/upstream.lock"

#!/usr/bin/env bash
# rot-matrix.sh - dry-run the patch series against the last N upstream release tags and print a pass/fail table.
#
#   tools/rot-matrix.sh [N] [--fetch] [--prefix-check]
#
# N        how many of the newest numeric upstream tags (X.Y.Z) to test; default 5.
# --fetch  first run `git fetch --tags upstream` (network). Default: use the tags already in this repository.
# --prefix-check  apply patch by patch (apply-series --prefix-check) and add a column with how many prefixes apply (k/N),
#                 so a failure shows exactly which prefix of the series breaks.
#
# Each tag is tested with tools/apply-series.sh in a temporary worktree, so no real branch or ref is touched.
# Prints a Markdown table (Tag, Commit, Result, Detail). Exit code: 0 all pass, 1 at least one tag fails, 3 usage/environment error.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$script_dir/.." && pwd)
repo=${VP_REPO:-$root}
n=5; fetch=0; prefix=0; pargs=()
while (($#)); do
  case "$1" in
    --fetch) fetch=1; shift ;;
    --prefix-check) prefix=1; pargs=(--prefix-check); shift ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    ''|*[!0-9]*) echo "error: unexpected argument '$1'" >&2; exit 3 ;;
    *) n=$1; shift ;;
  esac
done
((n >= 1)) || { echo "error: N must be >= 1" >&2; exit 3; }

if ((fetch)); then git -C "$repo" fetch --quiet --tags upstream >&2; fi

tags=()
while IFS= read -r t; do tags+=("$t"); done < <(git -C "$repo" tag -l --sort=-v:refname '[0-9]*' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | head -n "$n")
((${#tags[@]})) || { echo "error: no numeric upstream tags found in $repo" >&2; exit 3; }

series_sha=$("$script_dir/series-sha.sh")
echo "Series sha256: $series_sha ($(grep -cvE '^[[:space:]]*(#|$)' "${VP_PATCH_DIR:-$root/patches}/series") patches); tags tested: ${#tags[@]}"
echo
total=$(grep -cvE '^[[:space:]]*(#|$)' "${VP_PATCH_DIR:-$root/patches}/series")
if ((prefix)); then echo "| Tag | Commit | Result | Prefixes | Detail |"; echo "|---|---|---|---|---|"
else echo "| Tag | Commit | Result | Detail |"; echo "|---|---|---|---|"; fi
fail=0
for t in "${tags[@]}"; do
  commit=$(git -C "$repo" rev-parse --short=9 "refs/tags/$t^{commit}")
  set +e
  out=$("$script_dir/apply-series.sh" "$t" "${pargs[@]}" 2>/dev/null)
  rc=$?
  set -e
  pcol=""
  if ((prefix)); then pcol=" $(grep -c '^PREFIX ' <<<"$out" || true)/$total |"; fi
  if ((rc == 0)); then
    echo "| $t | $commit | PASS |$pcol $(sed -n 's/^OK .*patches=\([0-9]*\) tree=\(.\{9\}\).*/\1 patches, tree \2/p' <<<"$out") |"
  else
    fail=1
    line=$(grep -m1 '^FAIL' <<<"$out" || true)
    patch=$(sed -n 's/.* patch=\([^ ]*\) .*/\1/p' <<<"$line")
    name=$(sed -n 's/.* name=\([^ ]*\) .*/\1/p' <<<"$line")
    conf=$(sed -n 's/.* conflicts=\(.*\)$/\1/p' <<<"$line")
    echo "| $t | $commit | FAIL |$pcol patch $patch ${name%%.patch}; conflicts: ${conf:-none (patch does not apply)} |"
  fi
done
exit $fail

#!/usr/bin/env bash
# apply-series.sh - apply patches/series onto an upstream ref in a throwaway worktree.
#
#   tools/apply-series.sh <upstream-ref> [--verify <ref-or-tree>] [--branch <name>] [--prefix-check] [--report <file>] [--keep] [--fetch]
#
# <upstream-ref>  tag, branch or commit that exists in this repository (e.g. 2.20.0).
# --verify X      after applying, require HEAD^{tree} == X^{tree}; exit 2 if different.
# --branch NAME   on success, keep the result as local branch NAME (refuses to overwrite). Default: leave nothing behind.
# --prefix-check  apply the patches one at a time and print "PREFIX k/N <patch> OK commit=<sha> tree=<tree>" after each,
#                 so CI can build every prefix; stops at the first prefix that fails. With --branch the branch holds all
#                 prefix commits (check them out by the printed sha).
# --report FILE   also write key=value lines (status, ref, commit, patch, patch_name, patches_total, prefixes_ok, conflicts,
#                 tree, verify) to FILE; written on success and on every failure, for CI to parse.
# --keep          keep the temporary worktree (debugging) and print its path.
# --fetch         if <upstream-ref> is a tag that is not present, try `git fetch upstream tag <ref>` first.
#
# Environment: VP_REPO (repository holding the upstream objects; default: the repo this script lives in),
#              VP_PATCH_DIR (default: <repo>/patches), VP_GIT_NAME / VP_GIT_EMAIL (committer identity for git am).
#
# Exit codes: 0 applied (and verified, if asked); 1 a patch failed to apply; 2 --verify mismatch; 3 usage/environment error.
# Output (stdout): "OK ..." / "FAIL ... patch=N/M name=... conflicts=a,b" / "VERIFY ..." lines; details on stderr.
# Nothing is left behind unless --keep/--branch.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$script_dir/.." && pwd)
repo=${VP_REPO:-$root}
patch_dir=${VP_PATCH_DIR:-$root/patches}

ref=""; verify=""; branch=""; report=""; prefix=0; keep=0; fetch=0; commit=""; patches=()

# write_report <status> [key=value ...]: key=value file for CI (only when --report was given).
write_report() {
  [ -n "$report" ] || return 0
  local status=$1; shift
  { echo "status=$status"; echo "ref=$ref"; echo "commit=$commit"; echo "patches_total=${#patches[@]}"; for kv in "$@"; do echo "$kv"; done; } > "$report"
}
usage() { sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; }
die() { echo "error: $*" >&2; write_report error "message=$*"; exit 3; }

while (($#)); do
  case "$1" in
    --verify) [ $# -ge 2 ] || die "--verify needs an argument"; verify=$2; shift 2 ;;
    --branch) [ $# -ge 2 ] || die "--branch needs an argument"; branch=$2; shift 2 ;;
    --prefix-check) prefix=1; shift ;;
    --report) [ $# -ge 2 ] || die "--report needs an argument"; report=$2; shift 2 ;;
    --keep) keep=1; shift ;;
    --fetch) fetch=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) usage; die "unknown option $1" ;;
    *) [ -z "$ref" ] || { usage; die "only one upstream ref allowed"; }; ref=$1; shift ;;
  esac
done
[ -n "$ref" ] || { usage; die "missing <upstream-ref>"; }

git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "$repo is not a git repository"

resolve_commit() { git -C "$repo" rev-parse --verify --quiet "$1^{commit}" 2>/dev/null || true; }
commit=$(resolve_commit "$ref")
if [ -z "$commit" ] && ((fetch)); then
  git -C "$repo" fetch --quiet upstream tag "$ref" >&2 2>&1 || true
  commit=$(resolve_commit "$ref")
fi
[ -n "$commit" ] || die "cannot resolve '$ref' to a commit in $repo (fetch the upstream tags, or pass --fetch)"

# Read the series (ignore blank lines and # comments).
while IFS= read -r line || [ -n "$line" ]; do
  line=${line%%#*}
  line=$(printf '%s' "$line" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  [ -n "$line" ] && patches+=("$line")
done < "$patch_dir/series"
((${#patches[@]})) || die "series is empty: $patch_dir/series"
for p in "${patches[@]}"; do [ -f "$patch_dir/$p" ] || die "listed in series but missing: $patch_dir/$p"; done

if [ -n "$branch" ]; then
  git -C "$repo" check-ref-format "refs/heads/$branch" || die "bad branch name '$branch'"
  if git -C "$repo" show-ref --verify --quiet "refs/heads/$branch"; then die "branch '$branch' already exists; refusing to overwrite"; fi
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/apply-series.XXXXXX")
wt=$tmp/wt
cleanup() {
  if ((keep)); then echo "kept worktree: $wt" >&2; return 0; fi
  git -C "$repo" worktree remove --force "$wt" >/dev/null 2>&1 || true
  rm -rf "$tmp"
  git -C "$repo" worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM
mkdir -p "$tmp/nohooks"

# Checkout with exact blob bytes (no CRLF conversion) so the result is reproducible on any OS.
git -C "$repo" -c core.autocrlf=false worktree add --quiet --detach "$wt" "$commit" >&2 2>&1 || die "worktree add failed for $commit"

G=(git -C "$wt" -c core.autocrlf=false -c core.safecrlf=false -c core.hooksPath="$tmp/nohooks" -c commit.gpgsign=false
   -c user.name="${VP_GIT_NAME:-vibepollo-patches}" -c user.email="${VP_GIT_EMAIL:-patches@invalid}")

args=()
for p in "${patches[@]}"; do args+=("$patch_dir/$p"); done

fail_at() {  # fail_at <1-based index of the failing patch>
  local idx=$1 name="?" conflicts
  if [[ "$idx" =~ ^[0-9]+$ ]] && (( idx >= 1 && idx <= ${#patches[@]} )); then name=${patches[$((idx - 1))]}; fi
  conflicts=$("${G[@]}" diff --name-only --diff-filter=U 2>/dev/null | paste -sd, - || true)
  echo "FAIL ref=$ref commit=$commit patch=$idx/${#patches[@]} name=$name conflicts=${conflicts:-none}"
  echo "--- git am output (tail) ---" >&2
  tail -n 25 "$tmp/am.log" >&2
  write_report fail "patch=$idx" "patch_name=$name" "prefixes_ok=$((idx - 1))" "conflicts=${conflicts:-}"
  exit 1
}

: > "$tmp/am.log"
if ((prefix)); then
  i=0
  for p in "${patches[@]}"; do
    i=$((i + 1))
    "${G[@]}" am --3way --keep-cr "$patch_dir/$p" >>"$tmp/am.log" 2>&1 || fail_at "$i"
    echo "PREFIX $i/${#patches[@]} $p OK commit=$("${G[@]}" rev-parse --short=12 HEAD) tree=$("${G[@]}" rev-parse 'HEAD^{tree}')"
  done
else
  if ! "${G[@]}" am --3way --keep-cr "${args[@]}" >"$tmp/am.log" 2>&1; then
    next=$(cat "$("${G[@]}" rev-parse --git-path rebase-apply/next 2>/dev/null)" 2>/dev/null || echo "?")
    fail_at "$next"
  fi
fi

head_tree=$("${G[@]}" rev-parse 'HEAD^{tree}')
echo "OK ref=$ref commit=$commit patches=${#patches[@]} tree=$head_tree"
verify_state=none

if [ -n "$verify" ]; then
  want_tree=$(git -C "$repo" rev-parse --verify --quiet "$verify^{tree}" 2>/dev/null) || die "cannot resolve --verify '$verify'"
  if [ "$want_tree" != "$head_tree" ]; then
    echo "VERIFY FAIL expected=$want_tree got=$head_tree"
    write_report verify_failed "tree=$head_tree" "prefixes_ok=${#patches[@]}" "verify=fail" "expected_tree=$want_tree"
    echo "--- paths that differ (expected -> got) ---" >&2
    git -C "$repo" diff --name-status "$want_tree" "$head_tree" >&2 || true
    exit 2
  fi
  echo "VERIFY OK tree=$want_tree reference=$verify"
  verify_state=ok
fi

if [ -n "$branch" ]; then
  git -C "$repo" update-ref "refs/heads/$branch" "$("${G[@]}" rev-parse HEAD)"
  echo "BRANCH $branch -> $("${G[@]}" rev-parse --short HEAD)"
fi
write_report ok "tree=$head_tree" "prefixes_ok=${#patches[@]}" "verify=$verify_state" "conflicts="

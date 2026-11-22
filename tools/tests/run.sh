#!/usr/bin/env bash
# tools/tests/run.sh - self-contained tests for apply-series.sh, rot-matrix.sh and series-sha.sh.
# Builds a throwaway upstream repo with tags, a 2-patch series and a conflicting tag; needs no network.
# Usage: tools/tests/run.sh   (exit 0 = all passed)
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tools=$(cd "$here/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/tools-tests.XXXXXX")
trap 'rm -rf "$work"' EXIT
pass=0; failn=0
ok()   { pass=$((pass + 1)); echo "ok   - $1"; }
bad()  { failn=$((failn + 1)); echo "FAIL - $1"; }
check() { local name=$1; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
repo=$work/up; mkdir -p "$repo"; cd "$repo"
git init -q -b main .
printf 'a\nb\nc\nd\ne\n' > f.txt; printf 'x\n' > g.txt
git add -A; git commit -q -m base; git tag 1.0.0
# patched work: two commits on top of 1.0.0 -> series
echo "extra" > new.txt; git add -A; git commit -q -m "p1: add new.txt"
sed -i 's/^c$/c-patched/' f.txt; git commit -q -am "p2: patch c"
git format-patch -q --no-signature --zero-commit -N -o "$work/patches" 1.0.0..HEAD
ref=$(git rev-parse HEAD)
printf '# test series\n' > "$work/patches/series"; ls "$work/patches"/*.patch | xargs -n1 basename >> "$work/patches/series"
# 1.1.0: clean upstream change elsewhere; 1.2.0: upstream rewrote line c (conflict with p2)
git checkout -q -b up 1.0.0; printf 'z\n' >> g.txt; git commit -q -am "up: g"; git tag 1.1.0
sed -i 's/^c$/c-upstream/' f.txt; git commit -q -am "up: c"; git tag 1.2.0
git checkout -q main

export VP_REPO=$repo VP_PATCH_DIR=$work/patches
S=$tools/apply-series.sh

out=$("$S" 1.0.0 --verify "$ref" --report "$work/r1" 2>&1); rc=$?
check "apply onto base verifies against reference (rc=$rc)" test $rc -eq 0
check "report says status=ok" grep -q '^status=ok$' "$work/r1"
check "report has verify=ok and tree" bash -c "grep -q '^verify=ok$' '$work/r1' && grep -q '^tree=[0-9a-f]\{40\}$' '$work/r1'"
"$S" 1.1.0 >/dev/null 2>&1; check "clean upstream change applies" test $? -eq 0
"$S" 1.2.0 --report "$work/r2" >/dev/null 2>&1; rc=$?
check "conflicting tag exits 1" test $rc -eq 1
check "report lists failing patch and conflicts" bash -c "grep -q '^status=fail$' '$work/r2' && grep -q '^patch=2$' '$work/r2' && grep -q '^conflicts=f.txt$' '$work/r2' && grep -q '^prefixes_ok=1$' '$work/r2'"
out=$("$S" 1.1.0 --verify 1.0.0 2>/dev/null); rc=$?
check "wrong --verify exits 2" test $rc -eq 2
"$S" nosuchtag --report "$work/r3" >/dev/null 2>&1; rc=$?
check "unknown ref exits 3 with status=error" bash -c "test $rc -eq 3 && grep -q '^status=error$' '$work/r3'"
out=$("$S" 1.0.0 --prefix-check 2>/dev/null)
check "prefix-check prints one PREFIX line per patch" test "$(grep -c '^PREFIX ' <<<"$out")" -eq 2
out=$("$S" 1.2.0 --prefix-check 2>/dev/null)
check "prefix-check stops at the failing prefix (1 PREFIX line, FAIL line)" bash -c "test $(grep -c '^PREFIX ' <<<"$out") -eq 1 && grep -q '^FAIL .* patch=2/2 ' <<<'$out'"
"$S" 1.0.0 --branch built/test >/dev/null 2>&1
check "--branch creates the branch" git -C "$repo" show-ref --verify --quiet refs/heads/built/test
"$S" 1.0.0 --branch built/test >/dev/null 2>&1; check "--branch refuses to overwrite" test $? -eq 3
check "no worktrees left behind" test "$(git -C "$repo" worktree list | wc -l)" -eq 1
check "default run leaves no branch" test "$(git -C "$repo" branch --list | wc -l)" -eq 3

# series-sha: CRLF in the series must not change the hash; patch content must
h1=$("$tools/series-sha.sh")
mkdir -p "$work/crlf"; cp "$work"/patches/* "$work/crlf/"; sed -i 's/$/\r/' "$work/crlf/series"
h2=$(VP_PATCH_DIR=$work/crlf "$tools/series-sha.sh")
check "series-sha ignores CRLF in series" test "$h1" = "$h2"
expect=$({ tr -d '\r' < "$work/patches/series"; for p in $(grep -v '^#' "$work/patches/series"); do cat "$work/patches/$p"; done; } | sha256sum | cut -d' ' -f1)
check "series-sha matches the documented definition" test "$h1" = "$expect"
echo "x" >> "$work/crlf/$(grep -v '^#' "$work/patches/series" | head -1)"
check "series-sha changes with patch content" test "$h1" != "$(VP_PATCH_DIR=$work/crlf "$tools/series-sha.sh")"

# rot-matrix: tag regex X.Y.Z, N newest, exit 1 when any fails
VP_REPO=$repo VP_PATCH_DIR=$work/patches "$tools/rot-matrix.sh" 3 --prefix-check >"$work/rot.txt" 2>/dev/null; rc=$?
check "rot-matrix exits 1 when one tag fails" test $rc -eq 1
check "rot-matrix table has PASS and FAIL rows" bash -c "grep -q '| 1.2.0 .*| FAIL |' '$work/rot.txt' && grep -q '| 1.1.0 .*| PASS |' '$work/rot.txt' && grep -q '| 1.0.0 .*| PASS |' '$work/rot.txt'"
check "rot-matrix shows prefix counts" grep -q '| 1/2 |' "$work/rot.txt"

[ -n "${DEBUG_ROT:-}" ] && cat "$work/rot.txt"
echo; echo "passed=$pass failed=$failn"
[ "$failn" -eq 0 ]

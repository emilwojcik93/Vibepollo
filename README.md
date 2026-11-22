# Vibepollo posture: patch series (`automation` branch)

This branch holds only the automation for the stream posture feature of `emilwojcik93/Vibepollo`: an ordered
`git format-patch` series that turns a pinned upstream release of `Nonary/Vibepollo` into the posture build.
It does not contain the upstream source tree. The patched tree is always produced by applying the series onto an
upstream tag.

Feature: touch clients (phone, handheld, tablet, 2-in-1 in tablet posture) get a virtual display that reports a small
physical size so Windows docks its touch keyboard, the host reports laptop/slate to Windows, and handhelds can enter
the Xbox full screen experience. Contract and options: `docs/touch-posture.md` (inside patch 0008).

## Layout

```
patches/series        ordered list of patch files ('#' comments allowed)
patches/0001..0008    git format-patch files, applied with git am --3way
upstream.lock         pinned upstream tag + commit + series hash + resulting tree (tools/update-lock.sh writes it)
policy.yml            CONTRACT.md section 5 shape: schema, allow_upstream_owners, cooling_off_hours, hold, deny, keep_releases
upstream-seen.json    CI state (first-seen tag -> commit, builds); written by CI only, see docs/CI.md
.github/workflows/    upstream-watch, rot-matrix, notify, keepalive, ci-selftest (CI engineer; docs/CI.md)
ci/                   CI scripts, wrapper workflow template and their tests (python -m pytest ci/tests -q)
tools/tests/run.sh    self-contained tests for the tools/ scripts (no network)
tools/apply-series.sh   apply the series onto an upstream ref in a temp worktree, optionally verify the tree
tools/rot-matrix.sh     dry-run the series against the last N upstream tags, print a table
tools/export-series.sh  regenerate patches/ from a scratch branch (reproducible format-patch flags)
tools/update-lock.sh    rewrite upstream.lock for a tag
tools/series-sha.sh     sha256 of series + patches (the value stored in upstream.lock)
docs/RESOLVING-ROT.md   what to do when the series stops applying to a new upstream release
docs/ROT-MATRIX-*.md    recorded rot matrix runs
```

## The series (applies onto 2.20.0, result tree `37b3569f00c79532f7d86b6ab8d8d827cd4d5ab3` = reference commit `e666fdc02`)

| # | Subject | What it adds |
|---|---|---|
| 0001 | posture: add the touch virtual display size policy | `posture_size.*`, pure size/class policy + tests |
| 0002 | posture: track per-client posture state and display sizes | `posture_state.*` + tests |
| 0003 | posture: add host options and per-client posture flags | config options, `touch_device`/`posture_mode` client fields, launch-session hints |
| 0004 | posture: report laptop/slate to Windows and add handheld full screen mode | `tablet_posture*`, Windows indicator + Xbox full screen backend, `/api/tablet-posture`, installer cleanup |
| 0005 | nvhttp: accept client posture on launch and resume, add /posture | launch/resume arguments, `/posture`, client status |
| 0006 | virtual display: hand the posture size to the driver and recreate on resume | display backends, resume recreation |
| 0007 | web ui: expose the posture settings | settings page, device editor, locales |
| 0008 | docs: document touch posture and add a tablet-switch simulator | docs, `scripts/sim-tablet-switch.py` |

Every patch carries its rationale in the commit message. Patches 0001 to 0007 are ordered so that each one only uses
symbols and files from earlier patches, and each patch adds its own CMake source-list lines. Compiling every
intermediate step has not been verified (no toolchain on the authoring machine; `--prefix-check` below lets CI do
it); the final result is identical to the reference branch `feature/stream-posture-2.20`.

## Use

Prerequisite: a git repository that has the upstream tags (this clone, or `git fetch --tags upstream`).
Requires bash (git-bash on Windows, any Linux runner) and git >= 2.40.

```bash
# apply onto a tag in a throwaway worktree; prove the result equals the reference commit
tools/apply-series.sh 2.20.0 --verify 1591d2c95
# keep the result as a local branch instead of discarding it (the future build/<tag>-nN flow)
tools/apply-series.sh 2.20.0 --branch build/2.20.0-n1
# does the series still apply to the last 5 upstream releases?
tools/rot-matrix.sh 5
# patch-by-patch: prints "PREFIX k/N <patch> OK commit=<sha> tree=<tree>" per prefix and stops at the first prefix that
# fails; CI can build each printed commit. rot-matrix.sh --prefix-check adds a "Prefixes" column (k/N) per tag.
tools/apply-series.sh 2.20.0 --prefix-check --branch build/2.20.0-n1
# machine-readable result for CI (key=value lines: status, ref, commit, patch, patch_name, patches_total, prefixes_ok,
# conflicts, tree, verify); status is ok | fail | verify_failed | error
tools/apply-series.sh 2.20.1 --report result.txt
```

`apply-series.sh` exit codes: 0 ok, 1 a patch failed (prints `FAIL ... patch=N/M name=... conflicts=a,b`), 2 `--verify` tree
mismatch, 3 usage or environment error. It never touches existing branches; on failure the temporary worktree is
removed and nothing is left behind. It always applies with `git am --3way --keep-cr` and `core.autocrlf=false`, so the
result does not depend on the OS or the user's git config. The `patches/` files are marked `-text` in
`.gitattributes` for the same reason: never let a tool rewrite their line endings.

## Add or refresh patches

Never edit the `.patch` files by hand. Work on commits, then export.

```bash
# 1. scratch branch on the pinned tag, with the current series applied
tools/apply-series.sh 2.20.0 --branch scratch/posture
git worktree add ../scratch scratch/posture && cd ../scratch
# 2. change code as normal commits (new patch = new commit; fixup + rebase -i for changes to an old patch)
# 3. export reproducibly (replaces patches/*.patch, rewrites patches/series)
cd <automation clone> && tools/export-series.sh ../scratch 2.20.0
# 4. prove it: the series must reproduce the scratch branch's tree
tools/apply-series.sh 2.20.0 --verify scratch/posture
tools/update-lock.sh 2.20.0 --reference scratch/posture
git add -A && git commit -m "series: <what changed>"
```

To move the pin to a new upstream release, see `docs/RESOLVING-ROT.md` (clean apply: just run
`tools/update-lock.sh <new-tag>`; conflict: resolve first).

## Policy

`policy.yml` follows CONTRACT.md section 5 exactly and is parsed strictly (fails closed on any other shape: no nesting,
no unknown shapes):

```yaml
schema: 1
allow_upstream_owners: [Nonary]           # only releases from these owners are built or installed
cooling_off_hours: {stable: 24, canary: 0} # minimum release age per channel
hold: false                                # true stops new builds; the last good release stays current
deny: []                                   # tags that must never install
keep_releases: 10                          # rollback window
```

CI reads it (`ci/policy.py`); the scripts under `tools/` do not. Authorship: every patch is authored
`emilwojcik93 <adminit@adar.pl>`; keep that when adding patches.

## Two state files, two jobs

- `upstream.lock` (this README, `tools/update-lock.sh`): the reference pin. One upstream tag, its commit, the series hash
  and the resulting tree. CI halts if a tag moves against it. Moved by hand only.
- `upstream-seen.json` (CI, `docs/CI.md`): first-seen commit and build numbers per upstream tag, written by the
  `upstream-watch` workflow. Never edit by hand.

CI applies the series with `ci/apply_series.py` (pinned identity and dates, reproducible tip SHA), not with
`tools/apply-series.sh`; both implement the same steps and produce the same tree, which the CI tests check
(`result_tree` 37b3569f... for 2.20.0). Use the scripts here for humans and local checks.

## Series hash (`patch_series_sha256`)

Defined once, in `tools/series-sha.sh`, and identical in every repository that uses this scheme (also the moonlight-qt
one): sha256 over the bytes of `patches/series` (CRLF normalised to LF) followed by the bytes of each patch file listed
in it (comments and blank lines in the series ignored), in series order, patch files exactly as stored.
Equivalent: `{ tr -d '
' < patches/series; for p in <listed patches in order>; do cat patches/$p; done; } | sha256sum`.

## upstream.lock

Records the exact upstream tag, the dereferenced commit, the series hash (`patch_series_sha256`), the reference commit
and the resulting tree. The pin is only moved by `tools/update-lock.sh <tag>`; a newer upstream release that also
applies cleanly (check with `tools/rot-matrix.sh`) does not change it. If a recorded tag later resolves to another
commit, something is wrong upstream and the pipeline must halt rather than build.

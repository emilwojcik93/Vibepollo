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
patches/0001..0009    git format-patch files, applied with git am --3way
upstream.lock         pinned upstream tag + commit + series hash + resulting tree (tools/update-lock.sh writes it)
policy.yml            allowlisted owners, cooling-off, hold switch, channels, retention
tools/apply-series.sh   apply the series onto an upstream ref in a temp worktree, optionally verify the tree
tools/rot-matrix.sh     dry-run the series against the last N upstream tags, print a table
tools/export-series.sh  regenerate patches/ from a scratch branch (reproducible format-patch flags)
tools/update-lock.sh    rewrite upstream.lock for a tag
tools/series-sha.sh     sha256 of series + patches (the value stored in upstream.lock)
docs/RESOLVING-ROT.md   what to do when the series stops applying to a new upstream release
docs/ROT-MATRIX-*.md    recorded rot matrix runs
```

## The series (applies onto 2.20.0, result tree `9132c484c39db62a4a1c89d53ee76f3b9825c116`)

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
| 0009 | tools: drop a stray blank line in CMakeLists.txt | cosmetic; only for tree identity with the reference |

Every patch carries its rationale in the commit message. Patches 0001 to 0006 are ordered so that each one only uses
symbols and files from earlier patches, and each patch adds its own CMake source-list lines. Compiling every
intermediate step has not been verified (no toolchain on the authoring machine); the final result is identical to
the reference branch.

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
```

`apply-series.sh` exit codes: 0 ok, 1 a patch failed (prints `FAIL patch=... conflicts=...`), 2 `--verify` tree
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

`policy.yml` is the single place for decisions: which upstream owners are trusted (`Nonary`), the cooling-off age
before a release is built or deployed (24 h), the `hold` switch (true stops new builds, last good release stays),
the canary and stable channels, and how many releases are kept for rollback (10). The scripts here do not read it
yet; the Phase 2 workflows will.

## upstream.lock

Records the exact upstream tag, the dereferenced commit, the series hash (`sha256` over `patches/series` followed by
every patch in order, `tools/series-sha.sh`), and the resulting tree. If a recorded tag later resolves to another
commit, something is wrong upstream and the pipeline must halt rather than build.

# Rot matrix 2026-11-22

Series: 9 patches, sha256 `fd06dfd007ce8e0b800e3d72df2f5f8a600d7a705675f63ed31527e81b003753`
(`tools/series-sha.sh`). Pinned upstream: 2.20.0. Method: `tools/rot-matrix.sh 8` in this clone (local upstream tags),
git 2.56.1.windows.1 in git-bash, every tag applied with `git am --3way --keep-cr` in a temporary worktree. Total run
time 69 s for 8 tags. PASS means "the whole series applies cleanly"; it does not mean "builds".

| Tag | Commit | Result | Detail |
|---|---|---|---|
| 2.20.0 | 2b0608e24 | PASS | 9 patches, tree 9132c484c (equals reference 1591d2c95) |
| 2.19.0 | 83c0eb26c | PASS | 9 patches, tree 7f7cf8cb3 |
| 2.18.19 | 6001fa086 | PASS | 9 patches, tree 870c5134b |
| 2.18.18 | acaad96c4 | PASS | 9 patches, tree 12e00ab47 |
| 2.18.9 | c286d0862 | FAIL | patch 4/9 (tablet posture): conflict in `src/main.cpp` |
| 2.17.6 | e9bf2a6cb | PASS | 9 patches, tree 2d7c0a97c |
| 2.17.5 | bc2e16e20 | PASS | 9 patches, tree 177bce7d2 |
| 2.17.3 | bce817d4e | PASS | 9 patches, tree bcdee5594 |

The default `tools/rot-matrix.sh` (N=5) covers the first five rows: 2.20.0, 2.19.0, 2.18.19, 2.18.18, 2.18.9. 2.17.6
is the sixth numeric tag; it was in the expected-pass list and passes. Only 2.18.9 fails, as expected.

## What actually fails on 2.18.9

The first run stops at patch 4 (`src/main.cpp`). To see everything that would conflict, the first conflict was
resolved with `--theirs` in a scratch worktree (experiment only, nothing kept) and the series continued:

| Patch | Result on 2.18.9 | Why |
|---|---|---|
| 0001, 0002, 0003 | clean | |
| 0004 tablet posture | conflict `src/main.cpp` | hunk context includes the `#ifdef __APPLE__` restore-watchdog block at the top of `main()`, which 2.18.9 does not have yet; the `#include` hunk and the other files merge fine |
| 0005, 0006 | clean | |
| 0007 web ui | conflict `src_assets/common/assets/web/configs/configFieldSchema.ts` | the `minimum_fps_target` line used as hunk context does not exist in 2.18.9 |
| 0008, 0009 | clean | |

So against 2.18.9 two files need a manual resolution, both context-only (the posture lines themselves are
independent of the missing upstream lines). A single combined patch fails on the same two files when applied with
`git apply --3way`.

## Notes

- Which patch fails first is order dependent: a failure in patch N hides failures in later patches until N is fixed
  (see the 2.18.9 table above for the full picture).
- The matrix only dry-runs the apply. Whether a tag compiles and the posture tests pass is a CI question (Phase 2).
- Only numeric `X.Y.Z` tags are considered upstream releases (plan R-TRK-1); older tags and the `v1.*` tags were not tested.

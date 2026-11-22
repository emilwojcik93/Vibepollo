# Rot matrix 2026-11-22 (regenerated after dropping the cosmetic patch)

Series: 8 patches, `patch_series_sha256` `93b5e40bc1b3ed563a9a4dbe2d46229aedb24d0f1152bc475118430f13de75b8`
(`tools/series-sha.sh`). Pinned upstream: 2.20.0 (not bumped). Reference: `e666fdc02` on `feature/stream-posture-2.20`
(2.20.0 + posture only), tree `37b3569f00c79532f7d86b6ab8d8d827cd4d5ab3`. Method: `tools/rot-matrix.sh 9 --prefix-check`
in this clone (local upstream tags), git-bash, every tag applied patch by patch with `git am --3way --keep-cr` in a
temporary worktree. Run time 113 s for 9 tags. PASS means "the whole series applies cleanly"; it does not mean "builds".

| Tag | Commit | Result | Prefixes | Detail |
|---|---|---|---|---|
| 2.20.1 | 0f80298bc | PASS | 8/8 | tree 27d8643da |
| 2.20.0 | 2b0608e24 | PASS | 8/8 | tree 37b3569f0 (equals reference) |
| 2.19.0 | 83c0eb26c | PASS | 8/8 | tree 10ccab7b8 |
| 2.18.19 | 6001fa086 | PASS | 8/8 | tree 93ba41f7f |
| 2.18.18 | acaad96c4 | PASS | 8/8 | tree d63aef5c9 |
| 2.18.9 | c286d0862 | FAIL | 3/8 | patch 4/8 (tablet posture): conflict in `src/main.cpp` |
| 2.17.6 | e9bf2a6cb | PASS | 8/8 | tree 343493712 |
| 2.17.5 | bc2e16e20 | PASS | 8/8 | tree ec28cda14 |
| 2.17.3 | bce817d4e | PASS | 8/8 | tree 78e82391d |

The default `tools/rot-matrix.sh` (N=5) covers the five newest numeric tags: 2.20.1, 2.20.0, 2.19.0, 2.18.19,
2.18.18 (all pass). 2.18.9 is the sixth and the only failure, as expected. 2.20.1 appeared after the first run of this
matrix; the series applies to it cleanly. `upstream.lock` still pins 2.20.0.

## What actually fails on 2.18.9

The first run stops at patch 4 (`src/main.cpp`). To see everything that would conflict, the first conflict was
resolved with `--theirs` in a scratch worktree (experiment only, nothing kept) and the series continued (done on the
earlier 9-patch series; the dropped patch was the last one and is irrelevant here):

| Patch | Result on 2.18.9 | Why |
|---|---|---|
| 0001, 0002, 0003 | clean | |
| 0004 tablet posture | conflict `src/main.cpp` | hunk context includes the `#ifdef __APPLE__` restore-watchdog block at the top of `main()`, which 2.18.9 does not have yet; the `#include` hunk and the other files merge fine |
| 0005, 0006 | clean | |
| 0007 web ui | conflict `src_assets/common/assets/web/configs/configFieldSchema.ts` | the `minimum_fps_target` line used as hunk context does not exist in 2.18.9 |
| 0008 | clean | |

So against 2.18.9 two files need a manual resolution, both context-only. A single combined patch fails on the same
two files when applied with `git apply --3way`.

## Notes

- Which patch fails first is order dependent: a failure in patch N hides failures in later patches until N is fixed.
- The matrix only dry-runs the apply. Whether a tag compiles and the posture tests pass is a CI question (Phase 2).
- Only numeric `X.Y.Z` tags are considered upstream releases (plan R-TRK-1); older tags and the `v1.*` tags were not tested.

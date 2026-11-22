# Resolving patch rot

"Rot" = the series no longer applies (or no longer builds) on a new upstream release. The automation fails closed:
no release is built, the last good release stays current. This is the manual fix, step by step.

## 1. Find out what broke

```bash
git fetch --tags upstream                      # latest upstream tags
tools/apply-series.sh <new-tag>                # prints: FAIL patch=N/M name=... conflicts=file1,file2
tools/rot-matrix.sh 5                          # how far back does it still apply?
```

The output names the first patch that failed and the conflicting files. Later patches may fail too; you only see
them one at a time. `tools/apply-series.sh <new-tag> --keep` leaves the half-applied worktree (path on stderr) so
you can look at the conflict markers.

## 2. Resolve in a scratch branch

```bash
git worktree add --detach ../rot <new-tag>
cd ../rot
git -c core.autocrlf=false am --3way --keep-cr <automation clone>/patches/*.patch   # stops at the first conflict
git status                                     # files with conflict markers
# edit the files: keep upstream's new code, re-insert the posture lines in the right place
git add <files> && git am --continue           # repeat until the whole series is applied
```

Use `git diff`, `git show --diff-merges=...` and the commit message of the failing patch to see what the patch
meant to do (every message explains the why). Typical causes:

- context moved or a neighbouring block was added or removed upstream (like the macOS watchdog block before
  `main()` that 2.18.9 lacks): re-apply the hunk by hand next to its new context;
- upstream renamed or changed a function the patch calls: adapt the call, keep the behaviour;
- upstream added its own implementation of something the patch adds: drop the duplicate and keep upstream's.

Do not resolve by taking one side wholesale (`--theirs`/`--ours`): that silently drops posture or upstream code.

## 3. Check that it still means the same

- Build: Windows MSI via the upstream `ci-windows.yml` with tests on (the posture unit tests are part of it).
- Read the diff of the resolution commits against the original patches (`git range-diff`):
  ```bash
  git range-diff <old-tag>..<scratch-before> <new-tag>..HEAD
  ```
  Only the conflicting hunks should differ.

## 4. Re-export and lock

```bash
cd <automation clone>
tools/export-series.sh ../rot <new-tag>                   # rewrites patches/ and patches/series
tools/apply-series.sh <new-tag> --verify <rot-branch-or-HEAD-of-../rot>
tools/rot-matrix.sh 5                                     # the older releases must still apply, or be dropped from policy on purpose
tools/update-lock.sh <new-tag> --reference <rot-HEAD>     # writes upstream.lock
git add -A && git commit -m "series: rebase onto <new-tag>"
```

If the re-exported series no longer applies to an older tag that matters (for example the canary pin), keep the old
series on a separate branch instead of mixing both; one series targets one upstream line.

## 5. When not to fix it

- The upstream change implements the feature, or makes it impossible: stop, open an issue, ask the owner.
- The conflict is in a sensitive path (`.github/`, installer custom actions, driver pins, network code): have the
  owner review the resolution before it is released.
- `--verify` fails after a resolution: the exported series does not reproduce your branch; do not release.

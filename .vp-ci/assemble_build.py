#!/usr/bin/env python3
"""Turn "upstream commit + applied patches" into the pushable build branch tip.

Run after ``apply_series.py``.  Steps (all on the already-patched work tree):

1. guard: the patches must not touch submodule gitlinks, ``.gitmodules``, the VHF
   contract pins, or any ``.github/`` path (PLAN R-PAT-2);
2. strip every upstream workflow except ``ci-windows.yml`` (a ``workflow_call``-only
   reusable workflow).  Upstream workflows with ``push`` triggers would otherwise
   run inside *our* repository, with *our* secrets, on every pushed build branch;
3. install the wrapper workflow template and the ``ci/`` helper scripts
   (as ``.vp-ci/``) and write ``.vp-ci/build-meta.json``;
4. make one deterministic commit.

Exit 0 and prints the new tip SHA; exit 1 on guard/validation failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List

import apply_series as ap_mod
import vpcommon as v
import workflow_check

KEEP_WORKFLOWS = {"ci-windows.yml"}
WRAPPER_NAME = "patched-windows-release.yml"
FORBIDDEN_PATCH_PATHS = (".github/", ".gitmodules",
                         "cmake/packaging/windows_virtual_gamepad_contract.cmake")


def guard_patch_paths(repo: str, base: str) -> List[str]:
    """Return offending paths touched between ``base`` and HEAD."""
    out = ap_mod.git(repo, "diff", "--raw", "--no-renames", "-z", f"{base}", "HEAD").stdout
    bad: List[str] = []
    fields = out.split("\0")
    i = 0
    while i + 1 < len(fields) and fields[i]:
        meta, path = fields[i], fields[i + 1]
        i += 2
        modes = meta.lstrip(":").split(" ")[:2]
        if "160000" in modes or path.startswith(FORBIDDEN_PATCH_PATHS) or path in FORBIDDEN_PATCH_PATHS:
            bad.append(path)
    return bad


def strip_workflows(repo: str) -> List[str]:
    wf = os.path.join(repo, ".github", "workflows")
    removed: List[str] = []
    if os.path.isdir(wf):
        for name in sorted(os.listdir(wf)):
            if name in KEEP_WORKFLOWS:
                continue
            ap_mod.git(repo, "rm", "-rq", "--", os.path.join(".github", "workflows", name))
            removed.append(name)
    return removed


def sensitive_block(args: argparse.Namespace) -> Dict[str, Any]:
    """Sanitized list of sensitive upstream paths touched since the previous release."""
    paths: List[str] = []
    if getattr(args, "sensitive_file", ""):
        with open(args.sensitive_file, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                p = line.strip()
                if p.startswith("- `") and p.endswith("`"):
                    p = p[3:-1]
                if p and not p.startswith("- ..."):
                    paths.append("".join(c if 32 <= ord(c) < 127 else "?" for c in p)[:200])
    since = getattr(args, "sensitive_since", "") or ""
    if since:
        v.upstream_tag(since)
    return {"since": since, "count": int(args.sensitive_count), "paths": paths[:50],
            "enforced": args.gate == "approval"}


def build_meta(args: argparse.Namespace, patch_commits: int) -> Dict[str, Any]:
    import version_map

    tag = v.upstream_tag(args.tag)
    ver = version_map.compute(tag, args.n)
    return {
        "schema": 1,
        "upstream": {"owner": args.repo_slug.split("/")[0], "repo": args.repo_slug.split("/")[1],
                     "tag": tag, "commit": v.sha1(args.base), "release_id": int(args.release_id),
                     "published_at": v.require(v.ISO_TS_RE, args.published_at, "published_at")},
        "build": {"n": args.n, "release_tag": ver["release_tag"], "version": ver["build_version"],
                  "msi_product_version": ver["msi_product_version"], "branch": args.branch,
                  "trial": bool(args.trial)},
        "patches": {"series_sha256": v.sha256(args.series_sha), "count": int(args.patch_count),
                    "groups": [g for g in args.groups.split(",") if g]},
        "patch_commits": patch_commits,
        "gate": args.gate,
        "sensitive_count": int(args.sensitive_count),
        "sensitive": sensitive_block(args),
    }


def assemble(args: argparse.Namespace) -> int:
    repo = args.repo
    v.require(v.REPO_SLUG_RE, args.repo_slug, "upstream repo")
    if args.gate not in ("auto", "approval"):
        return v.fail("gate must be auto or approval")
    bad = guard_patch_paths(repo, args.base)
    if bad:
        return v.fail("patch series touches protected paths: " + ", ".join(bad[:5]))
    patch_commits = int(ap_mod.git(repo, "rev-list", "--count", f"{args.base}..HEAD").stdout.strip())

    upstream_wf = os.path.join(repo, ".github", "workflows", "ci-windows.yml")
    if not os.path.isfile(upstream_wf):
        return v.fail("upstream ci-windows.yml is missing")
    workflow_check.require_workflow_call_only(upstream_wf)  # fail closed on new triggers
    strip_workflows(repo)
    wf_dir = os.path.join(repo, ".github", "workflows")
    os.makedirs(wf_dir, exist_ok=True)
    template = os.path.join(args.ci_dir, "wrapper", WRAPPER_NAME)
    if not os.path.isfile(template):
        return v.fail("wrapper template missing")
    shutil.copyfile(template, os.path.join(wf_dir, WRAPPER_NAME))

    vp = os.path.join(repo, ".vp-ci")
    if os.path.exists(vp):
        shutil.rmtree(vp)
    os.makedirs(vp)
    for name in sorted(os.listdir(args.ci_dir)):
        if name.endswith(".py"):
            shutil.copyfile(os.path.join(args.ci_dir, name), os.path.join(vp, name))
    meta = build_meta(args, patch_commits)
    with open(os.path.join(vp, "build-meta.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
        fh.write("\n")

    ap_mod.git(repo, "add", "-A", "--", ".github", ".vp-ci")
    date = ap_mod.commit_date(repo, args.base)
    env = ap_mod.identity_env(date)
    msg = (f"build: wrapper workflow and metadata for {args.tag}-vp.{args.n}\n\n"
           f"upstream-commit: {args.base}\nseries-sha256: {args.series_sha}\n")
    ap_mod.git(repo, "commit", "--no-gpg-sign", "-q", "-m", msg, env=env)
    tip = ap_mod.git(repo, "rev-parse", "HEAD").stdout.strip()
    extra = set(os.listdir(wf_dir)) - KEEP_WORKFLOWS - {WRAPPER_NAME}
    if extra:
        return v.fail("unexpected workflow files remain after stripping")
    print(tip)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ci-dir", required=True, help="directory holding ci/*.py and ci/wrapper/")
    ap.add_argument("--base", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--n", required=True, type=int)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--repo-slug", default="Nonary/Vibepollo")
    ap.add_argument("--release-id", required=True, type=int)
    ap.add_argument("--published-at", required=True)
    ap.add_argument("--series-sha", required=True)
    ap.add_argument("--patch-count", required=True, type=int)
    ap.add_argument("--groups", default="posture")
    ap.add_argument("--gate", default="auto")
    ap.add_argument("--sensitive-count", default="0")
    ap.add_argument("--sensitive-file", default="", help="file listing sensitive upstream paths (one per line)")
    ap.add_argument("--sensitive-since", default="", help="previous upstream tag the diff was taken against")
    ap.add_argument("--trial", action="store_true")
    args = ap.parse_args(argv)
    try:
        return assemble(args)
    except (v.ValidationError, subprocess.SubprocessError, OSError, ValueError) as exc:
        return v.fail(f"assemble_build: {type(exc).__name__}: {str(exc)[:200]}")


if __name__ == "__main__":
    raise SystemExit(main())

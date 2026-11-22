#!/usr/bin/env python3
"""Break-glass / first-release path: do what the ``prepare`` job of upstream-watch does, locally.

Run it from a clean checkout of the ``automation`` branch on the operator machine::

    python ci/prepare_build_branch.py --dry-run                 # nothing pushed, prints branch + tree hash
    python ci/prepare_build_branch.py                           # newest eligible upstream release
    python ci/prepare_build_branch.py --tag 2.20.1 --remote fork

Steps (same code as the workflow): policy + hold check -> list upstream releases and select the newest
numeric one (or ``--tag``) -> validate -> dereference the annotated tag -> check the moved-tag pins ->
plan (the next build number ``N``) -> fetch the tag and verify the commit -> ``git am --3way`` the patch
series -> sensitive-path report -> assemble ``build/<tag>-nN`` (upstream workflows stripped, ``ci-windows.yml``
must be ``workflow_call`` only, wrapper + ``.vp-ci`` + metadata) -> record ``upstream-seen.json`` (local commit
on ``automation``) -> push the lock commit, then the build branch.

Only the two ``git push`` calls need credentials (your own ssh/https git credentials for the remote);
everything before them reads public data anonymously.  The work tree must be clean.  ``--dry-run`` stops
before any change to the repository or the remote.  Pushing ``build/*`` with a token that cannot create workflow
files will be refused by GitHub: use ssh as the repository owner.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from typing import Any, Callable, Dict, List, Optional

import apply_series
import assemble_build
import deref_commit
import lock_update
import plan_build
import policy as policy_mod
import select_release
import sensitive_paths
import series as series_mod
import vpcommon as v

API = "https://api.github.com"
UP_REPO = "Nonary/Vibepollo"


def http_get_json(url: str) -> Any:
    """Anonymous GET of a public GitHub API URL (no credentials are ever sent)."""
    if not url.startswith(API + "/repos/"):
        raise v.ValidationError("refusing to fetch a non-API URL")
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "vp-prepare-build-branch"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read().decode("utf-8"))


def run_git(cwd: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=check, timeout=1800)
    return proc.stdout.strip()


def slug_from_url(url: str) -> str:
    m = re.fullmatch(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?", url.strip())
    if not m:
        raise v.ValidationError("cannot derive owner/repo from the remote URL")
    return v.require(v.REPO_SLUG_RE, m.group(1), "fork repo")


def runs_from_api(payload: Any) -> List[Dict[str, Any]]:
    """Map the REST workflow-runs payload to the shape plan_build expects (gh run list --json)."""
    out = []
    for r in (payload or {}).get("workflow_runs", []) if isinstance(payload, dict) else []:
        out.append({"databaseId": r.get("id"), "headBranch": r.get("head_branch"), "status": r.get("status"),
                    "conclusion": r.get("conclusion") or "", "attempt": r.get("run_attempt") or 1})
    return out


def prepare(args: argparse.Namespace, get_json: Callable[[str], Any] = http_get_json,
            now: Optional[str] = None) -> int:
    root = os.path.abspath(args.root)
    ci_dir = os.path.join(root, "ci")
    # -- guard rails -------------------------------------------------------------------------
    if run_git(root, "status", "--porcelain"):
        return v.fail("work tree is not clean: commit or stash first")
    branch_now = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch_now != "automation" and not args.allow_any_branch:
        return v.fail(f"run this from the automation branch (currently on {branch_now[:60]})")
    remote_url = run_git(root, "remote", "get-url", args.remote)
    fork = args.fork_repo or slug_from_url(remote_url)

    pol = policy_mod.load(os.path.join(root, "policy.yml"))
    hold = bool(pol.get("hold")) and not args.override_hold
    if hold:
        return v.fail("policy.yml hold is true (use --override-hold to ignore it once)")

    # -- select, validate, dereference (anonymous API calls) -------------------------------------
    per_page = 100 if args.tag else 20
    releases = get_json(f"{API}/repos/{UP_REPO}/releases?per_page={per_page}")
    chosen = select_release.select(releases, slug=UP_REPO, allow=pol["allow_upstream_owners"], tag=args.tag, top=20)
    sel = chosen["selected"]
    if not sel:
        return v.fail("no eligible upstream release found")
    tag = sel["tag"]
    commit = deref_commit.deref(lambda p: get_json(f"{API}/repos/{UP_REPO}/{p}"), tag)
    print(f"upstream release {tag} commit {commit} release_id {sel['release_id']}")

    # -- pins and plan ------------------------------------------------------------------------------
    lock_path = os.path.join(root, "upstream-seen.json")
    lock = lock_update.load(lock_path)
    pin = lock_update.reference_pin(os.path.join(root, "upstream.lock"))
    fp = series_mod.fingerprint(os.path.join(root, "patches", "series"), os.path.join(root, "patches"))
    our_releases = get_json(f"{API}/repos/{fork}/releases?per_page=100")
    runs = runs_from_api(get_json(f"{API}/repos/{fork}/actions/workflows/patched-windows-release.yml/runs?per_page=100"))
    plan = plan_build.plan(tag=tag, commit=commit, lock=lock, our_releases=our_releases, runs=runs,
                           series_sha=fp["series_sha256"], hold=False, force_rebuild=args.force_rebuild,
                           manual_tag=bool(args.tag), pin=pin)
    print(f"plan: {plan['action']} n={plan['n']} ({plan_build._clean(plan['reason'])})")
    if plan["action"] != "build":
        return 0 if plan["action"] == "noop" and not plan["stuck"] else 1
    n = int(plan["n"])
    branch = v.branch_name(tag, n)

    # -- sensitive-path base (last built tag, else previous upstream release) ---------------------------
    prev = lock_update.last_built_before(lock, tag) or (select_release.previous_of(
        select_release.numeric_candidates(releases), tag) or "")

    # -- scratch clone: fetch + verify + apply + assemble -------------------------------------------------
    work = args.workdir or tempfile.mkdtemp(prefix="vp-prepare-")
    src = os.path.join(work, "src")
    os.makedirs(src, exist_ok=True)
    run_git(src, "init", "-q")
    upstream_url = args.upstream_url or f"https://github.com/{UP_REPO}.git"
    run_git(src, "fetch", "-q", "--no-tags", upstream_url, f"refs/tags/{tag}:refs/tags/upstream-{tag}")
    if run_git(src, "rev-parse", f"refs/tags/upstream-{tag}^{{commit}}") != commit:
        return v.fail("upstream tag moved while preparing the build")
    run_git(src, "checkout", "-q", "--detach", commit)

    gate, count, names_file = "auto", 0, os.path.join(work, "sensitive.md")
    open(names_file, "w").close()
    if prev:
        prev_commit = deref_commit.deref(lambda p: get_json(f"{API}/repos/{UP_REPO}/{p}"), prev)
        run_git(src, "fetch", "-q", "--no-tags", upstream_url, f"refs/tags/{prev}:refs/tags/upstream-{prev}")
        names = run_git(src, "diff", "--name-only", prev_commit, commit).splitlines()
        hits = sensitive_paths.sensitive(names)
        count = len(hits)
        with open(names_file, "w", encoding="utf-8") as fh:
            for p in hits[:200]:
                fh.write(f"- `{p}`\n")
        if count:
            print(f"SENSITIVE upstream paths changed since {prev}: {count} file(s) (report only unless enforced)")

    rc = apply_series.apply(src, commit, os.path.join(root, "patches", "series"), os.path.join(root, "patches"),
                            report_path=os.path.join(work, "conflict.md"))
    if rc != 0:
        return v.fail("patch series did not apply (conflict report: %s)" % os.path.join(work, "conflict.md"))
    ns = argparse.Namespace(repo=src, ci_dir=ci_dir, base=commit, tag=tag, n=n, branch=branch, repo_slug=UP_REPO,
                            release_id=sel["release_id"], published_at=sel["published_at"],
                            series_sha=fp["series_sha256"], patch_count=fp["count"], groups=",".join(fp["groups"]),
                            gate=gate, sensitive_count=str(count), sensitive_since=prev,
                            sensitive_file=names_file, trial=False)
    if assemble_build.assemble(ns) != 0:
        return 1
    tip = run_git(src, "rev-parse", "HEAD")
    tree = run_git(src, "rev-parse", "HEAD^{tree}")
    v.sha1(tip)
    print(f"BRANCH {branch}\nTIP {tip}\nTREE {tree}")
    push_cmds = [f"git -C {src} push {remote_url} {tip}:refs/heads/{branch}"]
    if args.dry_run:
        print("dry run: nothing recorded, nothing pushed. The real run will execute:")
        print(f"  git -C <repo> push {args.remote} HEAD:refs/heads/automation   (upstream-seen.json lock commit)")
        for c in push_cmds:
            print("  " + c)
        return 0

    # -- record the lock FIRST (local commit on automation), then push lock, then the build branch -------------
    stamp = now or subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True).stdout.strip()
    if plan["record_lock"]:
        lock_update.record(lock, tag, commit, sel["release_id"], sel["published_at"], stamp)
    lock_update.add_build(lock, tag, n, fp["series_sha256"], tip)
    lock_update.save(lock, lock_path)
    run_git(root, "add", "upstream-seen.json")
    run_git(root, "-c", "user.name=vp-automation", "-c", "user.email=vp-automation@users.noreply.github.com",
            "commit", "-q", "-m", f"lock: pin upstream {tag} and record build n{n} (manual path)")
    print(f"pushing lock commit to {args.remote} automation ...")
    run_git(root, "push", args.remote, "HEAD:refs/heads/automation")
    print(f"pushing {branch} ...")
    run_git(src, "push", remote_url, f"{tip}:refs/heads/{branch}")  # no --force: a collision must fail loudly
    print(f"done: {branch} pushed; the wrapper workflow starts from the push event.")
    return 0


def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.dirname(here), help="checkout of the automation branch")
    ap.add_argument("--tag", help="upstream tag X.Y.Z (default: newest eligible release)")
    ap.add_argument("--remote", default="fork", help="git remote that receives the pushes (default: fork)")
    ap.add_argument("--fork-repo", default="", help="owner/repo of the fork (default: derived from the remote URL)")
    ap.add_argument("--dry-run", action="store_true", help="stop before changing anything; print branch and tree hash")
    ap.add_argument("--override-hold", action="store_true")
    ap.add_argument("--force-rebuild", action="store_true")
    ap.add_argument("--allow-any-branch", action="store_true", help="testing only")
    ap.add_argument("--workdir", default="", help="scratch directory (default: a new temp dir)")
    ap.add_argument("--upstream-url", default="", help="testing only")
    args = ap.parse_args(argv)
    try:
        if args.tag:
            v.upstream_tag(args.tag)
        v.require(re.compile(r"[A-Za-z0-9._-]{1,50}"), args.remote, "remote name")
        return prepare(args)
    except (v.ValidationError, subprocess.SubprocessError, OSError, lock_update.TagMoved, ValueError) as exc:
        return v.fail(f"prepare_build_branch: {type(exc).__name__}: {str(exc)[:300]}")


if __name__ == "__main__":
    raise SystemExit(main())

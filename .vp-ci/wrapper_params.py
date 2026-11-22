#!/usr/bin/env python3
"""Validate a build branch and derive the wrapper workflow parameters.

Runs in the wrapper's ``validate`` job, inside the build tree, with only
``contents: read``.  Every value comes from (a) the ref name, (b) workflow
inputs passed through ``env:``, or (c) ``.vp-ci/build-meta.json`` -- and each is
regex-validated and cross-checked before it is exported as an output.

Environment (all plain ``env:`` variables, never interpolated into the script):
  EVENT_NAME, REF, SHA, INPUT_DRY_RUN, INPUT_TESTS, META_PATH (default .vp-ci/build-meta.json)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Dict

import vpcommon as v
import version_map
import workflow_check
import manifest_gen

REF_RE = re.compile(r"refs/heads/(build|ci-trial)/(" + v._NUM + r"\." + v._NUM + r"\." + v._NUM + r")-n(0|" + v._BN + ")")


def derive(env: Dict[str, str], meta: Dict[str, Any]) -> Dict[str, str]:
    m = REF_RE.fullmatch(env.get("REF", ""))
    if not m:
        raise v.ValidationError("refusing to run: ref is not refs/heads/build/* or refs/heads/ci-trial/*")
    kind, tag, n_ref = m.group(1), m.group(2), int(m.group(3))
    v.upstream_tag(tag)
    v.sha1(env.get("SHA", ""), "github.sha")

    if meta.get("schema") != 1:
        raise v.ValidationError("build-meta schema mismatch")
    up, bd, pt = meta["upstream"], meta["build"], meta["patches"]
    if up["tag"] != tag:
        raise v.ValidationError("build-meta tag does not match the branch name")
    trial = bd.get("trial") is True
    if (kind == "ci-trial") != trial:
        raise v.ValidationError("trial flag and branch kind disagree")
    n_eff = max(n_ref, 1) if trial else n_ref
    if bd["n"] != n_eff or (not trial and n_ref < 1):
        raise v.ValidationError("build-meta build number does not match the branch name")
    if bd["branch"] != env["REF"][len("refs/heads/"):]:
        raise v.ValidationError("build-meta branch does not match the ref")

    ver = version_map.compute(tag, n_eff)
    for key, meta_key in (("release_tag", "release_tag"), ("build_version", "version"),
                          ("msi_product_version", "msi_product_version")):
        if bd[meta_key] != ver[key]:
            raise v.ValidationError("build-meta version fields do not match the version mapper")

    commit = v.sha1(up["commit"], "upstream commit")
    release_id = up["release_id"]
    if not isinstance(release_id, int) or release_id <= 0:
        raise v.ValidationError("bad release_id")
    v.require(v.ISO_TS_RE, up["published_at"], "published_at")
    v.sha256(pt["series_sha256"], "series sha")
    if not isinstance(pt["count"], int) or not (0 <= pt["count"] <= 99):
        raise v.ValidationError("bad patch count")
    groups = pt["groups"]
    if not isinstance(groups, list) or not all(re.fullmatch(r"[a-z][a-z0-9-]{0,30}", g or "") for g in groups):
        raise v.ValidationError("bad patch groups")
    gate = meta.get("gate")
    if gate not in ("auto", "approval"):
        raise v.ValidationError("bad gate")
    if not isinstance(meta.get("patch_commits"), int) or meta["patch_commits"] < 0:
        raise v.ValidationError("bad patch_commits")

    sens = manifest_gen.sanitize_sensitive(meta.get("sensitive"))  # strict; also re-checked at publish
    dry = trial or env.get("INPUT_DRY_RUN", "") == "true"
    tests = env.get("INPUT_TESTS", "") != "false"
    if not tests and not dry:
        raise v.ValidationError("tests may only be disabled for a dry run")

    return {
        "tag": tag, "n": str(n_eff), "release_tag": ver["release_tag"],
        "build_version": ver["build_version"], "msi_product_version": ver["msi_product_version"],
        "upstream_msi_version": ver["upstream_msi_version"],
        "next_upstream_msi_version": version_map.upstream_msi_version(
            "{}.{}.{}".format(tag.split(".")[0], tag.split(".")[1], int(tag.split(".")[2]) + 1)),
        "upstream_commit": commit, "release_id": str(release_id),
        "series_sha256": pt["series_sha256"], "patch_count": str(pt["count"]),
        "patch_groups": ",".join(groups), "patch_commits": str(meta["patch_commits"]),
        "gate": gate, "sensitive_count": str(sens["count"]),
        "environment": "release-approval" if gate == "approval" else "release-auto",
        "dry_run": "true" if dry else "false", "tests": "true" if tests else "false",
        "trial": "true" if trial else "false",
        "source_ref": env["REF"], "source_sha": env["SHA"],
    }


def verify_ancestry(out: Dict[str, str]) -> None:
    """HEAD must be exactly ``patch_commits + 1`` commits above the upstream commit."""
    want = int(out["patch_commits"]) + 1
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != out["source_sha"]:
        raise v.ValidationError("checked-out HEAD is not github.sha")
    base = subprocess.run(["git", "rev-parse", f"HEAD~{want}"], capture_output=True, text=True,
                          check=True).stdout.strip()
    if base != out["upstream_commit"]:
        raise v.ValidationError("build branch is not upstream commit plus the recorded patch commits")


def check_workflows(path: str = ".github/workflows") -> None:
    allowed = {"ci-windows.yml", "patched-windows-release.yml"}
    present = set(os.listdir(path))
    if not present <= allowed:
        raise v.ValidationError("unexpected workflow files present in the build tree")
    if "ci-windows.yml" not in present:
        raise v.ValidationError("ci-windows.yml missing from the build tree")
    workflow_check.require_workflow_call_only(os.path.join(path, "ci-windows.yml"))


def main() -> int:
    env = {k: os.environ.get(k, "") for k in
           ("EVENT_NAME", "REF", "SHA", "INPUT_DRY_RUN", "INPUT_TESTS")}
    try:
        with open(os.environ.get("META_PATH", ".vp-ci/build-meta.json"), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        out = derive(env, meta)
        verify_ancestry(out)
        check_workflows()
    except (v.ValidationError, KeyError, TypeError, json.JSONDecodeError, OSError,
            subprocess.SubprocessError) as exc:
        return v.fail(f"wrapper_params: {type(exc).__name__}: {str(exc)[:200]}")
    v.write_outputs(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Independent QA / Security Gate tests, contributed by the QA reviewer (Phase 2 review of
automation-ci @ 6c97da767) and adapted by the CI/Release Engineer: scenarios whose defect was
fixed are now covered by test_review_fixes.py; the rest are kept as written."""
import json
import os
import subprocess
import sys

import pytest

import apply_series as aps
import assemble_build as asb
import lock_update as lu
import manifest_gen as mg
import plan_build as pb
import select_release as sel
import sensitive_paths as sp
import stage_assets as sa
import version_map as vm
import vpcommon as v
from test_series_apply_assemble import GIT_ENV, git, write, CI_DIR  # reuse helpers

C1 = "a" * 40
S1 = "1" * 64
TS = "2026-11-22T06:38:51Z"


# ---------------------------------------------------------------- (3) rerun loop
# (superseded by test_review_fixes.py: test_QA_rerun_budget_is_never_consumed_by_gh_run_rerun)


# (superseded by test_review_fixes.py: test_QA_dry_run_success_run_is_treated_as_failed_attempt)


# ---------------------------------------------------------------- (7) version mapper edges
@pytest.mark.parametrize("tag", ["2.20.0", "2.18.9", "2.18.99", "1.0.9", "2.20.653", "255.255.653", "2.2.0"])
def test_QA_window_all_n(tag):
    floor = vm.msi_tuple(vm.upstream_msi_version(tag))
    x, y, z = map(int, tag.split("."))
    ceil = vm.msi_tuple(vm.upstream_msi_version(f"{x}.{y}.{z + 1}")) if z + 1 <= 654 else None
    seen = set()
    last = floor
    for n in range(1, 88):
        pv = vm.msi_tuple(vm.msi_product_version(tag, n))
        assert pv > floor and (ceil is None or pv < ceil)
        assert pv > last, f"not strictly increasing at n={n}"
        assert pv[2] <= 65535
        last = pv
        seen.add(pv)
    assert len(seen) == 87


def test_QA_limit_Z654():
    with pytest.raises(v.ValidationError):
        vm.compute("2.20.654", 1)
    vm.compute("2.20.653", 87)  # ProductVersion 2.20.65489.0
    with pytest.raises(v.ValidationError):
        vm.compute("256.0.0", 1)


def test_QA_N_boundaries_label_numbers():
    assert vm.build_version("2.20.0", 29) == "2.20.1-alpha.29"
    assert vm.build_version("2.20.0", 30) == "2.20.1-beta.1"
    assert vm.build_version("2.20.0", 58) == "2.20.1-beta.29"
    assert vm.build_version("2.20.0", 59) == "2.20.1-rc.1"
    assert vm.msi_product_version("2.20.0", 29) == "2.20.129.0"
    assert vm.msi_product_version("2.20.0", 30) == "2.20.131.0"  # ORD 31, skips 130


# (superseded by test_review_fixes.py: test_QA_leading_zero_tags_accepted_and_collide)


# (superseded by test_review_fixes.py: test_QA_newest_unbuildable_tag_wedges_selection)


# ---------------------------------------------------------------- (7) sensitive-path gate coverage
@pytest.mark.parametrize("p", ["package-lock.json", "package.json", "CMakeLists.txt", "cmake/dependencies/x.cmake",
                               "scripts/install.ps1", "src_assets/windows/misc/service/install.bat",
                               "vcpkg.json", "src/platform/windows/service.cpp", "tools/foo.cmd"])
def test_QA_sensitive_gate_misses(p):
    missed = not sp.matches(p)
    print(p, "NOT flagged" if missed else "flagged")


# (superseded by test_review_fixes.py: test_QA_sensitive_gate_root_package_lock_missed)


# ---------------------------------------------------------------- stage_assets: missing provenance bypass
# (superseded by test_review_fixes.py: test_QA_provenance_optional_even_with_require_msi)


def test_QA_provenance_lies_not_checked_against_installer_content(tmp_path):
    import hashlib
    b = tmp_path / "b"; m = tmp_path / "m"; d = tmp_path / "d"
    b.mkdir(); m.mkdir()
    inst = b"MZ-installer-embedding-DIFFERENT-msi"
    (b / "VibepolloSetup.exe").write_bytes(inst)
    msi = b"msi-bytes"
    (m / "x.msi").write_bytes(msi)
    prov = {"assets": {"VibepolloSetup.exe": hashlib.sha256(inst).hexdigest()},
            "embedded_msi": {"sha256": hashlib.sha256(msi).hexdigest(), "verified": True,
                             "upgrade_table_verified": True,
                             "msi_product_version": "2.20.101.0"}}
    (b / "release-provenance.json").write_text(json.dumps(prov))
    assert "VibepolloSetup.exe" in sa.stage(str(b), str(m), str(d), True, "2.20.101.0")  # accepted


# ---------------------------------------------------------------- assemble: stripping / planted files
def mk_up(tmp_path, files):
    repo = tmp_path / "up"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo, git(repo, "rev-parse", "HEAD").stdout.strip()


def run_assemble(repo, base, tmp_path):
    ci = tmp_path / "ci"
    (ci / "wrapper").mkdir(parents=True)
    (ci / "wrapper" / "patched-windows-release.yml").write_text("name: w\non: push\njobs: {}\n")
    for n in os.listdir(CI_DIR):
        if n.endswith(".py"):
            (ci / n).write_bytes(open(os.path.join(CI_DIR, n), "rb").read())
    series = tmp_path / "series"; series.write_text("")
    patches = tmp_path / "patches"; patches.mkdir()
    assert aps.apply(str(repo), base, str(series), str(patches)) == 0
    rc = asb.main(["--repo", str(repo), "--ci-dir", str(ci), "--base", base, "--tag", "2.20.0", "--n", "1",
                   "--branch", "build/2.20.0-n1", "--release-id", "5", "--published-at", TS,
                   "--series-sha", S1, "--patch-count", "0"])
    return rc


def listing(repo):
    return sorted(git(repo, "ls-files").stdout.split())


def test_QA_assemble_strips_yaml_nested_and_planted_files(tmp_path):
    repo, base = mk_up(tmp_path, {
        ".github/workflows/ci-windows.yml": "name: ci\non: workflow_call\njobs: {}\n",
        ".github/workflows/evil.yaml": "name: e\non: push\njobs: {}\n",
        ".github/workflows/Evil.YML": "name: e\non: push\njobs: {}\n",
        ".github/workflows/sub/x.yml": "name: e\non: push\njobs: {}\n",
        ".github/workflows/patched-windows-release.yml": "name: planted\non: push\njobs: {}\n",
        ".vp-ci/json.py": "raise SystemExit('planted')\n",
        ".vp-ci/sitecustomize.py": "x=1\n",
        "README.md": "x\n"})
    assert run_assemble(repo, base, tmp_path) == 0
    files = listing(repo)
    assert [f for f in files if f.startswith(".github/workflows/")] == [
        ".github/workflows/ci-windows.yml", ".github/workflows/patched-windows-release.yml"]
    assert ".vp-ci/json.py" not in files and ".vp-ci/sitecustomize.py" not in files
    assert open(repo / ".github/workflows/patched-windows-release.yml").read().startswith("name: w")


def test_QA_assemble_keeps_ci_windows_with_push_trigger(tmp_path):
    repo, base = mk_up(tmp_path, {
        ".github/workflows/ci-windows.yml": "name: ci\non: [push, workflow_dispatch]\njobs: {}\n"})
    # fixed (QA P2-2): a ci-windows.yml with push/workflow_dispatch triggers is refused (fail closed)
    assert run_assemble(repo, base, tmp_path) == 1


def test_QA_assemble_rejects_symlinked_workflows_dir(tmp_path):
    if os.name == "nt":
        pytest.skip("symlink")
    repo, base = mk_up(tmp_path, {"x": "1"})

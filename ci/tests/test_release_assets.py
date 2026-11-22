import json
import os
import subprocess

import pytest

import manifest_gen as mg
import msi_summary as ms
import notify_text as nt
import sensitive_paths as sp
import stage_assets as st
import vpcommon as v
import wrapper_params as wp

C1 = "a" * 40
S1 = "1" * 64
TS = "2026-11-22T06:38:51Z"

PROPERTY_IDT = "Property\tValue\ns72\tl0\nProperty\tProperty\nProductVersion\t2.20.101\nProductName\tVibepollo\n"
CA_IDT = ("Action\tType\tSource\tTarget\tExtendedType\ns72\ti2\tS72\tS255\tI4\nCustomAction\tAction\n"
          "RunInstall\t3074\tAPP\t[Apollo]install.ps1 -x\t\n"
          "Evil\x07Act\t1\tBin\tcmd /c é\t\n")
SVC_IDT = "ServiceInstall\tName\ns72\ts255\nServiceInstall\tServiceInstall\nid1\tSunshineService\n"


# --- msi summary ------------------------------------------------------------------------

def test_msi_summary_parses_and_sanitizes():
    s = ms.summarize({"Property": PROPERTY_IDT, "CustomAction": CA_IDT, "ServiceInstall": SVC_IDT})
    assert s["product_version"] == "2.20.101" and s["custom_action_count"] == 2
    assert s["services"] == ["SunshineService"]
    names = [a["action"] for a in s["custom_actions"]]
    assert "RunInstall" in names and all(all(32 <= ord(c) < 127 for c in n) for n in names)
    assert len(s["custom_action_table_sha256"]) == 64
    assert ms.summarize({"Property": PROPERTY_IDT, "CustomAction": CA_IDT, "ServiceInstall": SVC_IDT}) == s


def test_msi_window_enforced_incident_case():
    # Live incident: 2.20.0-posture.1 produced 2.20.90.0 which is BELOW upstream 2.20.99.0.
    with pytest.raises(v.ValidationError):
        ms.check_window("2.20.90", "2.20.101.0", "2.20.99.0", "2.20.199.0")
    with pytest.raises(v.ValidationError):  # equal to upstream
        ms.check_window("2.20.99.0", "2.20.99.0", "2.20.99.0", "2.20.199.0")
    with pytest.raises(v.ValidationError):  # matches window but not the expected value
        ms.check_window("2.20.150.0", "2.20.101.0", "2.20.99.0", "2.20.199.0")
    with pytest.raises(v.ValidationError):  # at/above the next upstream
        ms.check_window("2.20.199.0", "2.20.199.0", "2.20.99.0", "2.20.199.0")
    ms.check_window("2.20.101", "2.20.101.0", "2.20.99.0", "2.20.199.0")
    ms.check_window("2.20.101.0", "2.20.101.0", "2.20.99.0", "2.20.199.0")


def test_msi_cli_with_fake_msiinfo(monkeypatch, tmp_path):
    tables = {"Property": PROPERTY_IDT, "CustomAction": CA_IDT, "ServiceInstall": SVC_IDT}
    monkeypatch.setattr(ms, "run_export", lambda msi, t: tables[t])
    out = tmp_path / "s.json"
    base = ["--msi", "x.msi", "--floor", "2.20.99.0", "--ceiling", "2.20.199.0", "--out", str(out)]
    assert ms.main(base + ["--expected", "2.20.101.0"]) == 0
    assert json.loads(out.read_text())["status"] == "ok"
    out.unlink()
    assert ms.main(base + ["--expected", "2.20.102.0"]) == 1 and not out.exists()
    tables["Property"] = ""
    assert ms.main(base + ["--expected", "2.20.101.0"]) == 1


# --- stage + manifest + sums -----------------------------------------------------------------

def make_dist_inputs(tmp_path, extra_build=None, msi_count=1):
    b = tmp_path / "in" / "build"
    m = tmp_path / "in" / "msi"
    b.mkdir(parents=True)
    m.mkdir(parents=True)
    (b / "VibepolloSetup.exe").write_bytes(b"MZ-installer")
    import hashlib
    inst = b"MZ-installer"
    msi = b"msi-bytes"
    (b / "release-provenance.json").write_text(json.dumps({
        "schema_version": 1, "assets": {"VibepolloSetup.exe": hashlib.sha256(inst).hexdigest()},
        "embedded_msi": {"verified": True, "upgrade_table_verified": True, "sha256": hashlib.sha256(msi).hexdigest(),
                         "msi_product_version": "2.20.101.0"}}))
    for name in (extra_build or []):
        (b / name).write_bytes(b"x")
    for i in range(msi_count):
        (m / f"Vibepollo-2.20.1-alpha.1-{i}.msi").write_bytes(b"msi-bytes")
    return b, m


def gen_args(dist, **over):
    import argparse
    ns = argparse.Namespace(dist=str(dist), installer="VibepolloSetup.exe", tag="2.20.0-vp.1",
                            upstream_owner="Nonary", upstream_repo="Vibepollo", upstream_tag="2.20.0",
                            upstream_commit=C1, release_id=200, n=1, build_version="2.20.1-alpha.1",
                            msi_product_version="2.20.101.0", run_id="123", source_ref="refs/heads/build/2.20.0-n1",
                            source_sha=C1, series_sha=S1, patch_count=8, groups="posture", created_at=TS,
                            msi_summary=None)
    for k, val in over.items():
        setattr(ns, k, val)
    return ns


def test_stage_and_manifest_roundtrip(tmp_path):
    b, m = make_dist_inputs(tmp_path)
    dist = tmp_path / "dist"
    assert st.stage(str(b), str(m), str(dist), True) == ["VibepolloSetup.exe", "release-provenance.json", "Vibepollo.msi"]
    summ = tmp_path / "summary.json"
    summ.write_text(json.dumps(ms.summarize({"Property": PROPERTY_IDT, "CustomAction": CA_IDT,
                                             "ServiceInstall": SVC_IDT})))
    manifest = mg.build_manifest(gen_args(dist, msi_summary=str(summ)))
    assert manifest["msi"]["custom_action_count"] == 2 and manifest["build"]["msi_product_version"] == "2.20.101.0"
    rc = mg.main(["generate", "--dist", str(dist), "--tag", "2.20.0-vp.1", "--upstream-tag", "2.20.0",
                  "--upstream-commit", C1, "--release-id", "200", "--n", "1", "--build-version", "2.20.1-alpha.1",
                  "--msi-product-version", "2.20.101.0", "--run-id", "123", "--source-ref",
                  "refs/heads/build/2.20.0-n1", "--source-sha", C1, "--series-sha", S1, "--patch-count", "8",
                  "--created-at", TS, "--msi-summary", str(summ)])
    assert rc == 0
    sums = (dist / "SHA256SUMS").read_text().splitlines()
    assert [ln.split("  ")[1] for ln in sums] == ["Vibepollo.msi", "VibepolloSetup.exe", "manifest.json",
                                                    "release-provenance.json"]
    assert "SHA256SUMS" not in "".join(sums) and "sigstore" not in "".join(sums)
    assert mg.main(["verify", "--dist", str(dist)]) == 0
    saved = json.loads((dist / "manifest.json").read_text())
    for a in saved["assets"]:
        assert f"{a['sha256']}  {a['name']}" in sums
    # tamper -> verify fails
    (dist / "VibepolloSetup.exe").write_bytes(b"tampered")
    assert mg.main(["verify", "--dist", str(dist)]) == 1


def test_stage_rejects_unexpected_files_and_msi_counts(tmp_path):
    b, m = make_dist_inputs(tmp_path, extra_build=["evil.dll"])
    with pytest.raises(v.ValidationError):
        st.stage(str(b), str(m), str(tmp_path / "d"), True)
    b2, m2 = make_dist_inputs(tmp_path / "x", msi_count=2)
    with pytest.raises(v.ValidationError):
        st.stage(str(b2), str(m2), str(tmp_path / "d2"), True)
    b3, m3 = make_dist_inputs(tmp_path / "y", msi_count=0)
    with pytest.raises(v.ValidationError):
        st.stage(str(b3), str(m3), str(tmp_path / "d3"), True)
    b4, m4 = make_dist_inputs(tmp_path / "z")
    (b4 / "release-provenance.json").write_text('{"k": "-----BEGIN RSA PRIVATE KEY-----"}')
    with pytest.raises(v.ValidationError):
        st.stage(str(b4), str(m4), str(tmp_path / "d4"), True)


def test_stage_cross_checks_upstream_provenance(tmp_path):
    b, m = make_dist_inputs(tmp_path)
    st.stage(str(b), str(m), str(tmp_path / "ok"), True, "2.20.101.0")
    with pytest.raises(v.ValidationError):  # wrong expected ProductVersion
        st.stage(str(b), str(m), str(tmp_path / "bad1"), True, "2.20.90.0")
    b2, m2 = make_dist_inputs(tmp_path / "x")
    (b2 / "VibepolloSetup.exe").write_bytes(b"tampered")
    with pytest.raises(v.ValidationError):  # installer hash differs from provenance
        st.stage(str(b2), str(m2), str(tmp_path / "bad2"), True)
    b3, m3 = make_dist_inputs(tmp_path / "y")
    (m3 / "Vibepollo-2.20.1-alpha.1-0.msi").write_bytes(b"other-msi")
    with pytest.raises(v.ValidationError):  # embedded MSI differs from standalone MSI
        st.stage(str(b3), str(m3), str(tmp_path / "bad3"), True)


def test_stage_rejects_symlink(tmp_path):
    b, m = make_dist_inputs(tmp_path)
    try:
        os.symlink(str(b / "VibepolloSetup.exe"), str(b / "link.exe"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(v.ValidationError):
        st.stage(str(b), str(m), str(tmp_path / "d"), True)


def test_manifest_validation_rejects_inconsistency(tmp_path):
    b, m = make_dist_inputs(tmp_path)
    dist = tmp_path / "dist"
    st.stage(str(b), str(m), str(dist), True)
    for over in ({"build_version": "2.20.1-alpha.2"}, {"msi_product_version": "2.20.90.0"},
                 {"tag": "2.20.0-vp.2"}, {"upstream_commit": "xyz"}, {"source_ref": "refs/tags/2.20.0"},
                 {"run_id": "1; rm"}, {"installer": "../evil.exe"}, {"created_at": "yesterday"}):
        with pytest.raises((v.ValidationError, KeyError)):
            mg.build_manifest(gen_args(dist, **over))


def test_msi_summary_revalidation_blocks_hostile_content():
    good = ms.summarize({"Property": PROPERTY_IDT, "CustomAction": CA_IDT, "ServiceInstall": SVC_IDT})
    assert mg.sanitize_msi_summary(good) is not None
    evil = json.loads(json.dumps(good))
    evil["custom_actions"][0]["target"] = "line1\nline2"
    assert mg.sanitize_msi_summary(evil) is None
    evil = json.loads(json.dumps(good))
    evil["custom_actions"] = [evil["custom_actions"][0]] * 401
    assert mg.sanitize_msi_summary(evil) is None
    assert mg.sanitize_msi_summary({"status": "error"}) is None


def test_sums_refuses_self_and_bundle(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"1")
    for bad in ("SHA256SUMS", "attestation.sigstore.json", "../x"):
        with pytest.raises(v.ValidationError):
            mg.write_sums(str(tmp_path), ["a.bin", bad])


# --- wrapper params ----------------------------------------------------------------------------

def meta(tag="2.20.0", n=1, trial=False, branch=None, gate="auto"):
    import version_map
    ver = version_map.compute(tag, n)
    return {"schema": 1,
            "upstream": {"owner": "Nonary", "repo": "Vibepollo", "tag": tag, "commit": C1, "release_id": 200,
                         "published_at": TS},
            "build": {"n": n, "release_tag": ver["release_tag"], "version": ver["build_version"],
                      "msi_product_version": ver["msi_product_version"],
                      "branch": branch or f"{'ci-trial' if trial else 'build'}/{tag}-n{n}", "trial": trial},
            "patches": {"series_sha256": S1, "count": 8, "groups": ["posture"]},
            "patch_commits": 8, "gate": gate, "sensitive_count": 0,
            "sensitive": {"since": "", "count": 0, "paths": [], "enforced": False}}


def env(ref="refs/heads/build/2.20.0-n1", **kw):
    e = {"EVENT_NAME": "push", "REF": ref, "SHA": "b" * 40, "INPUT_DRY_RUN": "", "INPUT_TESTS": ""}
    e.update(kw)
    return e


def test_wrapper_params_release_and_dry_run():
    out = wp.derive(env(), meta())
    assert out["dry_run"] == "false" and out["tests"] == "true" and out["environment"] == "release-auto"
    assert out["build_version"] == "2.20.1-alpha.1" and out["next_upstream_msi_version"] == "2.20.199.0"
    assert wp.derive(env(INPUT_DRY_RUN="true"), meta())["dry_run"] == "true"
    assert wp.derive(env(), meta(gate="approval"))["environment"] == "release-approval"


def test_wrapper_params_trial_is_always_dry_and_maps_n0():
    out = wp.derive(env(ref="refs/heads/ci-trial/2.20.0-n0"), meta(n=1, trial=True, branch="ci-trial/2.20.0-n0"))
    assert out["dry_run"] == "true" and out["n"] == "1" and out["trial"] == "true"


@pytest.mark.parametrize("ref", ["refs/tags/2.20.0", "refs/heads/canary", "refs/heads/build/2.20.0-n1/x",
                                 "refs/heads/build/$(id)-n1", "refs/heads/build/2.20.0-n1\n", "refs/pull/1/merge",
                                 "refs/heads/automation", ""])
def test_wrapper_params_refuses_foreign_refs(ref):
    with pytest.raises(v.ValidationError):
        wp.derive(env(ref=ref), meta())


def test_wrapper_params_cross_checks():
    with pytest.raises(v.ValidationError):  # tag mismatch
        wp.derive(env(ref="refs/heads/build/2.20.1-n1"), meta())
    with pytest.raises(v.ValidationError):  # n mismatch
        wp.derive(env(ref="refs/heads/build/2.20.0-n2"), meta())
    with pytest.raises(v.ValidationError):  # trial flag vs branch kind
        wp.derive(env(ref="refs/heads/ci-trial/2.20.0-n1"), meta())
    with pytest.raises(v.ValidationError):  # release n=0
        wp.derive(env(ref="refs/heads/build/2.20.0-n0"), meta(n=1))
    bad = meta()
    bad["build"]["version"] = "2.20.0-posture.1"
    with pytest.raises(v.ValidationError):
        wp.derive(env(), bad)
    bad = meta()
    bad["build"]["msi_product_version"] = "2.20.90.0"
    with pytest.raises(v.ValidationError):
        wp.derive(env(), bad)
    bad = meta()
    bad["upstream"]["commit"] = "nothex"
    with pytest.raises(v.ValidationError):
        wp.derive(env(), bad)
    bad = meta()
    bad["patches"]["groups"] = ["x; rm"]
    with pytest.raises(v.ValidationError):
        wp.derive(env(), bad)
    with pytest.raises(v.ValidationError):  # tests off only in dry run
        wp.derive(env(INPUT_TESTS="false"), meta())
    assert wp.derive(env(INPUT_TESTS="false", INPUT_DRY_RUN="true"), meta())["tests"] == "false"


def test_wrapper_workflow_set_check(tmp_path):
    d = tmp_path / "wf"
    d.mkdir()
    (d / "ci-windows.yml").write_text("\n".join(["name: x", "on:", "  workflow_call:", "    inputs: {}", "jobs: {}", ""]))
    (d / "patched-windows-release.yml").write_text("x")
    wp.check_workflows(str(d))
    (d / "ci.yml").write_text("x")
    with pytest.raises(v.ValidationError):
        wp.check_workflows(str(d))


# --- notifications ---------------------------------------------------------------------------------

def test_notify_sanitizes_everything():
    out = nt.compose({"KIND": "failure", "TAG": "$(id)", "RUN_ID": "123", "REPO": "emilwojcik93/Vibepollo",
                      "DETAIL": "bad\n@everyone <script>alert(1)</script> `rm -rf` ${{ secrets.X }} " + "x" * 400})
    for key in ("text", "issue_body"):
        assert "<" not in out[key] and "`rm" not in out[key] and "${{" not in out[key] and "\n@" not in out[key]
    assert "$(id)" not in out["text"] and "(invalid tag)" in out["text"]
    assert len(out["text"]) < 500
    assert "https://github.com/emilwojcik93/Vibepollo/actions/runs/123" in out["text"]


def test_notify_rejects_bad_fields():
    with pytest.raises(v.ValidationError):
        nt.compose({"KIND": "nope", "REPO": "a/b"})
    with pytest.raises(v.ValidationError):
        nt.compose({"KIND": "failure", "REPO": "a/b", "RUN_ID": "12; id"})
    with pytest.raises(v.ValidationError):
        nt.compose({"KIND": "failure", "REPO": "not a repo"})


def test_notify_cli_writes_json(tmp_path, monkeypatch):
    for k, val in {"KIND": "success", "TAG": "2.20.0-vp.1", "RUN_ID": "9", "REPO": "o/r", "DETAIL": ""}.items():
        monkeypatch.setenv(k, val)
    g, i = tmp_path / "g.json", tmp_path / "i.md"
    assert nt.main(["--gchat", str(g), "--issue-body", str(i)]) == 0
    assert json.loads(g.read_text())["text"].startswith("Vibepollo patch pipeline: Release published")


# --- sensitive paths --------------------------------------------------------------------------------

def test_sensitive_paths():
    flagged = [".github/workflows/ci.yml", "packaging/windows/x.ps1", "third-party/libvirtualgamepad",
               "x/package-lock.json", ".gitmodules", "src_assets/windows/drivers/vhf-gamepad/install.ps1",
               "foo.wxs", "src/nvhttp.cpp", "src/rtsp.h", "src/confighttp.cpp", "src/stream.cpp", "src/crypto.cpp",
               "package-lock.json", "package.json", "CMakeLists.txt", "cmake/dependencies/x.cmake",
               "scripts/install.ps1", "src_assets/windows/misc/service/install.bat", "vcpkg.json", "tools/foo.cmd"]
    quiet = ["README.md", "docs/a.md", "src/a.cpp", "src/platform/linux/x.cpp", "tests/unit/test_a.cpp", ""]
    assert sp.sensitive(flagged + quiet) == sorted(flagged)
    assert sp.sensitive(quiet) == []


def test_sensitive_cli(tmp_path, monkeypatch):
    names = tmp_path / "n.txt"
    names.write_text("src/a.cpp\n.github/workflows/x.yml\n")
    out = tmp_path / "o"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert sp.main(["--names", str(names), "--github-output", "--summary", str(tmp_path / "s.md")]) == 0
    assert "gate=approval" in out.read_text() and "sensitive_count=1" in out.read_text()

"""Regression tests for the independent Security Gate review (QA) findings.

Several scenarios were first reproduced by the QA reviewer (ci/tests/test_qa_extra.py, kept with
attribution); these tests pin the fixed behaviour.
"""
import json

import pytest

import lock_update as lu
import manifest_gen as mg
import plan_build as pb
import select_release as sel
import stage_assets as sa
import version_map as vm
import vpcommon as v
import workflow_check as wc

C1 = "a" * 40
S1 = "1" * 64
TS = "2026-11-22T06:38:51Z"
BR = "build/2.20.0-n1"


def lock_with_build():
    lock = lu.empty()
    lock["entries"]["2.20.0"] = {"commit": C1, "release_id": 1, "published_at": TS, "first_seen": TS,
                                 "builds": [{"n": 1, "series_sha256": S1, "branch": BR, "source_sha": "b" * 40}]}
    return lock


def plan(runs, **kw):
    base = dict(tag="2.20.0", commit=C1, lock=lock_with_build(), our_releases=[], runs=runs, series_sha=S1)
    base.update(kw)
    return pb.plan(**base)


def run(attempt, conclusion="failure", status="completed", rid=77):
    return {"headBranch": BR, "status": status, "conclusion": conclusion, "databaseId": rid, "attempt": attempt}


# --- P1-1: `gh run rerun` keeps the run id and only bumps `attempt` ------------------------

def test_rerun_budget_uses_attempt_field_and_stops():
    assert plan([run(1)])["action"] == "rerun"
    assert plan([run(2)])["action"] == "rerun"
    r3 = plan([run(3)])
    assert r3["action"] == "noop" and r3["stuck"] is True and r3["rerun_run_id"] == 0
    assert plan([run(9)])["stuck"] is True  # never an unbounded loop


def test_rerun_budget_counts_all_runs_of_the_branch():
    r = plan([run(1, rid=5), run(2, rid=6)])
    assert r["stuck"] is True  # 3 attempts in total


def test_rerun_in_flight_attempt_is_a_noop():
    r = plan([run(2, conclusion="", status="in_progress")])
    assert (r["action"], r["reason"]) == ("noop", "build in flight")


def test_successful_or_cancelled_run_without_release_is_stuck_not_rerun():
    for conclusion in ("success", "cancelled", "skipped"):
        r = plan([run(1, conclusion=conclusion)])
        assert r["action"] == "noop" and r["stuck"] is True, conclusion


def test_missing_attempt_field_counts_as_one():
    r = plan([{"headBranch": BR, "status": "completed", "conclusion": "failure", "databaseId": 3}])
    assert r["action"] == "rerun"


# --- P1-2: every plan reason must survive the $GITHUB_OUTPUT writer --------------------------

def reason_cases():
    out = []
    out.append(plan([]))  # stuck: branch recorded, no run
    out.append(plan([run(3)]))  # stuck: attempts
    out.append(plan([run(1, conclusion="success")]))
    out.append(plan([run(1)]))  # rerun
    out.append(plan([run(1, conclusion="", status="in_progress")]))
    out.append(pb.plan(tag="2.20.0", commit="b" * 40, lock=lock_with_build(), our_releases=[], runs=[],
                       series_sha=S1))  # halt
    out.append(pb.plan(tag="2.20.0", commit=C1, lock=lu.empty(), our_releases=[{"tag_name": "2.20.0-vp.1",
                                                                              "draft": False}], runs=[], series_sha=S1))
    out.append(pb.plan(tag="2.20.0", commit=C1, lock=lu.empty(), our_releases=[], runs=[], series_sha=S1, hold=True))
    out.append(pb.plan(tag="2.20.0", commit=C1, lock=lock_with_build(), our_releases=[], runs=[], series_sha="2" * 64,
                       force_rebuild=True))
    return out


def test_every_reason_is_a_valid_output_value(tmp_path):
    for res in reason_cases():
        v.write_outputs({"reason": pb._clean(res["reason"])}, str(tmp_path / "o"))


@pytest.mark.parametrize("runs_payload", [[], [run(3)], [run(1, conclusion="success")]])
def test_cli_stuck_paths_do_not_crash(tmp_path, monkeypatch, runs_payload):
    out = tmp_path / "gho"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    lock = tmp_path / "lock.json"
    lock.write_text(lu.dumps(lock_with_build()))
    rel = tmp_path / "rel.json"
    rel.write_text("[]")
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps(runs_payload))
    rc = pb.main(["--tag", "2.20.0", "--commit", C1, "--lock", str(lock), "--our-releases", str(rel),
                  "--runs", str(runs), "--series-sha", S1, "--github-output"])
    assert rc == 0
    text = out.read_text()
    assert "stuck=true\n" in text and "action=noop\n" in text


def test_cli_write_failure_is_a_clean_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pb, "_clean", lambda t, limit=200: "bad;value")
    lock = tmp_path / "lock.json"
    lock.write_text(lu.dumps(lock_with_build()))
    (tmp_path / "e.json").write_text("[]")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "gho"))
    rc = pb.main(["--tag", "2.20.0", "--commit", C1, "--lock", str(lock), "--our-releases", str(tmp_path / "e.json"),
                  "--runs", str(tmp_path / "e.json"), "--series-sha", S1, "--github-output"])
    assert rc == 1 and "::error::" in capsys.readouterr().err


def test_release_without_lock_entry_is_an_alert_not_a_silent_noop():
    r = pb.plan(tag="2.20.0", commit=C1, lock=lu.empty(), our_releases=[{"tag_name": "2.20.0-vp.1", "draft": False}],
                runs=[], series_sha=S1)
    assert r["action"] == "noop" and r["stuck"] is True


# --- P2-2: upstream ci-windows.yml must be workflow_call only ---------------------------------

CALL_ONLY = "name: x\non:\n  workflow_call:\n    inputs:\n      a:\n        type: string\njobs: {}\n"


@pytest.mark.parametrize("text,expected", [
    (CALL_ONLY, {"workflow_call"}),
    ("on: push\njobs: {}\n", {"push"}),
    ("on: [push, workflow_call]\n", {"push", "workflow_call"}),
    ("on: {workflow_call: {}, schedule: [{cron: '1 2 * * *'}]}\n", {"workflow_call", "schedule"}),
    ("on:\n  workflow_call:\n  push:\n    branches: [main]\n", {"workflow_call", "push"}),
    ("on:\n  - push\n  - workflow_call\n", {"push", "workflow_call"}),
    ('"on":\n  workflow_dispatch:\n', {"workflow_dispatch"}),
])
def test_workflow_triggers_parser(text, expected):
    assert wc.triggers(text) == expected


def test_workflow_check_fails_closed(tmp_path):
    ok = tmp_path / "ok.yml"
    ok.write_text(CALL_ONLY)
    wc.require_workflow_call_only(str(ok))
    for bad in ("on: push\n", "on:\n  workflow_call:\n  pull_request:\n", "name: no triggers\njobs: {}\n"):
        p = tmp_path / "bad.yml"
        p.write_text(bad)
        with pytest.raises(v.ValidationError):
            wc.require_workflow_call_only(str(p))


def test_real_upstream_shape_is_accepted():
    text = "---\nname: CI-Windows\npermissions: {}\n\non:\n  workflow_call:\n    inputs:\n      use_self_hosted:\n" \
           "        required: false\n        type: boolean\n    secrets:\n      symbol_token:\n        required: false\n"
    assert wc.triggers(text) == {"workflow_call"}


# --- P2-3: provenance is mandatory and cross-checked --------------------------------------------

def make_inputs(tmp_path, provenance):
    b, m = tmp_path / "b", tmp_path / "m"
    b.mkdir(parents=True)
    m.mkdir(parents=True)
    (b / "VibepolloSetup.exe").write_bytes(b"MZ-installer")
    if provenance is not None:
        (b / "release-provenance.json").write_text(json.dumps(provenance))
    (m / "x.msi").write_bytes(b"msi-bytes")
    return b, m


def test_missing_provenance_is_rejected(tmp_path):
    b, m = make_inputs(tmp_path, None)
    with pytest.raises(v.ValidationError):
        sa.stage(str(b), str(m), str(tmp_path / "d"), True, "2.20.101.0")


def test_provenance_without_embedded_msi_or_unverified_is_rejected(tmp_path):
    import hashlib
    inst = hashlib.sha256(b"MZ-installer").hexdigest()
    msi = hashlib.sha256(b"msi-bytes").hexdigest()
    for prov in ({"assets": {"VibepolloSetup.exe": inst}},
                 {"assets": {"VibepolloSetup.exe": inst},
                  "embedded_msi": {"verified": False, "upgrade_table_verified": True, "sha256": msi}},
                 {"assets": {"VibepolloSetup.exe": inst},
                  "embedded_msi": {"verified": True, "upgrade_table_verified": False, "sha256": msi}}):
        b, m = make_inputs(tmp_path / str(abs(hash(json.dumps(prov)))), prov)
        with pytest.raises(v.ValidationError):
            sa.stage(str(b), str(m), str(tmp_path / "d" / str(abs(hash(json.dumps(prov))))), True, "")


def test_manifest_records_embedded_msi_and_sensitive_paths(tmp_path):
    import hashlib
    import argparse
    inst, msi = b"MZ-installer", b"msi-bytes"
    prov = {"assets": {"VibepolloSetup.exe": hashlib.sha256(inst).hexdigest()},
            "embedded_msi": {"verified": True, "upgrade_table_verified": True, "sha256": hashlib.sha256(msi).hexdigest(),
                             "msi_product_version": "2.20.201.0", "setup_file_version": "2.20.201.501"}}
    b, m = make_inputs(tmp_path, prov)
    dist = tmp_path / "dist"
    sa.stage(str(b), str(m), str(dist), True, "2.20.201.0")
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"sensitive": {"since": "2.20.0", "count": 2, "paths": [".github/x.yml", "src/nvhttp.cpp"],
                                              "enforced": False}}))
    ns = argparse.Namespace(dist=str(dist), installer="VibepolloSetup.exe", tag="2.20.1-vp.1", upstream_owner="Nonary",
                            upstream_repo="Vibepollo", upstream_tag="2.20.1", upstream_commit=C1, release_id=9, n=1,
                            build_version="2.20.2-alpha.1", msi_product_version="2.20.201.0", run_id="5",
                            source_ref="refs/heads/build/2.20.1-n1", source_sha=C1, series_sha=S1, patch_count=8,
                            groups="posture", created_at=TS, msi_summary=None, meta=str(meta))
    man = mg.build_manifest(ns)
    assert man["installer_embeds"]["msi_sha256"] == hashlib.sha256(msi).hexdigest()
    assert man["sensitive_paths_touched"] == {"since": "2.20.0", "count": 2,
                                              "paths": [".github/x.yml", "src/nvhttp.cpp"], "enforced": False}
    ns.msi_product_version = "2.20.201.0"
    prov["embedded_msi"]["msi_product_version"] = "2.20.90.0"
    (dist / "release-provenance.json").write_text(json.dumps(prov))
    with pytest.raises(v.ValidationError):  # provenance version != claimed version (provenance mismatch caught earlier)
        sa.verify_provenance(str(dist), "2.20.201.0")


def test_sensitive_line_cli(tmp_path, capsys):
    meta = tmp_path / "m.json"
    meta.write_text(json.dumps({"sensitive": {"since": "2.20.0", "count": 3, "paths": ["a"], "enforced": False}}))
    assert mg.main(["sensitive-line", "--meta", str(meta)]) == 0
    out = capsys.readouterr().out
    assert "SENSITIVE UPSTREAM PATHS TOUCHED since 2.20.0: 3 file(s)" in out
    meta.write_text(json.dumps({"sensitive": {"since": "", "count": 0, "paths": [], "enforced": False}}))
    assert mg.main(["sensitive-line", "--meta", str(meta)]) == 0
    assert "none" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [{"since": "x", "count": 0, "paths": [], "enforced": False},
                                 {"since": "", "count": -1, "paths": [], "enforced": False},
                                 {"since": "", "count": 1, "paths": ["a\nb"], "enforced": False},
                                 {"since": "", "count": 1, "paths": ["a"] * 51, "enforced": False},
                                 {"since": "", "count": 1, "paths": [], "enforced": "yes"}, None, []])
def test_sensitive_block_is_validated(bad):
    with pytest.raises(v.ValidationError):
        mg.sanitize_sensitive(bad)


# --- P3: canonical tags only, unbuildable tags never wedge the selection -----------------------------

@pytest.mark.parametrize("tag", ["2.20.00", "2.020.0", "02.20.0", "00.0.0", "2.20.0-vp.01", "2.20.0-vp.0"])
def test_non_canonical_tags_rejected(tag):
    with pytest.raises(v.ValidationError):
        if "-vp." in tag:
            v.release_tag(tag)
        else:
            v.upstream_tag(tag)


def test_canonical_zero_components_still_accepted():
    for t in ("0.0.0", "2.0.0", "2.20.0", "10.0.10"):
        assert v.upstream_tag(t) == t
    assert v.parse_branch("build/2.20.0-n01") is None
    assert v.parse_branch("build/2.20.0-n0") is None


def test_unbuildable_newest_tag_is_skipped_not_wedging():
    rels = [{"draft": False, "published_at": TS, "tag_name": t, "id": i + 1}
            for i, t in enumerate(["2.20.0", "9999.0.0", "2.20.654", "256.0.0"])]
    res = sel.select(rels, slug="Nonary/Vibepollo", allow=["Nonary"])
    assert res["selected"]["tag"] == "2.20.0"
    vm.compute(res["selected"]["tag"], 1)


# --- first automatic run: newest eligible release only, no backfill -----------------------------------

def test_first_run_selects_newest_and_builds_only_that_one():
    rels = [{"draft": False, "published_at": TS, "tag_name": t, "id": i + 10, "prerelease": True}
            for i, t in enumerate(["2.20.1", "2.20.0", "2.19.0"])]
    res = sel.select(rels, slug="Nonary/Vibepollo", allow=["Nonary"])
    assert res["selected"]["tag"] == "2.20.1"
    r = pb.plan(tag="2.20.1", commit=C1, lock=lu.empty(), our_releases=[], runs=[], series_sha=S1)
    assert (r["action"], r["n"]) == ("build", 1)
    # once 2.20.1 is recorded, the older releases are never selected again (select always returns the newest)
    lock = lu.empty()
    lu.record(lock, "2.20.1", C1, 11, TS, TS)
    older = pb.plan(tag="2.20.0", commit="b" * 40, lock=lock, our_releases=[], runs=[], series_sha=S1)
    assert older["action"] == "noop"  # no backfill even if someone asks for the older tag without `tag=`


def test_last_built_before():
    lock = lu.empty()
    for t, builds in (("2.19.0", [1]), ("2.20.0", []), ("2.20.1", [1])):
        lu.record(lock, t, C1, 1, TS, TS)
        for n in builds:
            lu.add_build(lock, t, n, S1, "b" * 40)
    assert lu.last_built_before(lock, "2.20.1") == "2.19.0"  # 2.20.0 was seen but never built
    assert lu.last_built_before(lock, "2.19.0") == ""
    assert lu.last_built_before(lock, "2.21.0") == "2.20.1"

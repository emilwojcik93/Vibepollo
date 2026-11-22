import json

import pytest

import lock_update as lu
import plan_build as pb
import policy
import vpcommon as v

C1 = "a" * 40
C2 = "b" * 40
S1 = "1" * 64
S2 = "2" * 64
TS = "2026-11-22T06:38:51Z"


def mklock(tag="2.20.0", commit=C1, builds=()):
    lock = lu.empty()
    lock["entries"][tag] = {"commit": commit, "release_id": 200, "published_at": TS, "first_seen": TS,
                            "builds": list(builds)}
    return lock


def build(n, sha=S1, tag="2.20.0"):
    return {"n": n, "series_sha256": sha, "branch": f"build/{tag}-n{n}", "source_sha": C2}


def rel(tag, draft=False):
    return {"tag_name": tag, "draft": draft}


def plan(lock, **kw):
    base = dict(tag="2.20.0", commit=C1, lock=lock, our_releases=[], runs=[], series_sha=S1)
    base.update(kw)
    return pb.plan(**base)


# --- lock ---------------------------------------------------------------------------

def test_lock_record_new_then_same(tmp_path):
    p = tmp_path / "upstream-seen.json"
    lock = lu.load(str(p))  # absent file
    assert lu.record(lock, "2.20.0", C1, 200, TS, TS) == "new"
    lu.save(lock, str(p))
    lock2 = lu.load(str(p))
    assert lu.record(lock2, "2.20.0", C1, 200, TS, TS) == "same"
    assert p.read_text().endswith("\n") and json.loads(p.read_text())["schema"] == 1


def test_lock_moved_tag_halts():
    lock = mklock()
    with pytest.raises(lu.TagMoved):
        lu.check(lock, "2.20.0", C2)
    with pytest.raises(lu.TagMoved):
        lu.record(lock, "2.20.0", C2, 200, TS, TS)


def test_lock_cli_exit_codes(tmp_path, capsys):
    p = str(tmp_path / "l")
    args = ["record", "--lock", p, "--tag", "2.20.0", "--commit", C1, "--release-id", "5",
            "--published-at", TS, "--now", TS]
    assert lu.main(args) == 0
    assert lu.main(args) == 0
    moved = ["check", "--lock", p, "--tag", "2.20.0", "--commit", C2]
    assert lu.main(moved) == lu.EXIT_MOVED
    assert lu.main(["add-build", "--lock", p, "--tag", "2.20.0", "--n", "1", "--series-sha", S1,
                    "--source-sha", C2]) == 0
    assert lu.main(["add-build", "--lock", p, "--tag", "2.20.0", "--n", "1", "--series-sha", S1,
                    "--source-sha", C2]) == 1  # duplicate n


def test_lock_validation_rejects_garbage(tmp_path):
    p = tmp_path / "l"
    for bad in ('{"schema": 2}', '{"schema":1,"upstream":"a/b","entries":{"$(id)":{}}}',
                '{"schema":1,"upstream":"a/b","entries":{"2.20.0":{"commit":"x","release_id":1}}}'):
        p.write_text(bad)
        with pytest.raises(v.ValidationError):
            lu.load(str(p))


# --- plan ---------------------------------------------------------------------------

def test_plan_first_sight_builds_n1():
    lock = lu.empty()
    r = plan(lock)
    assert (r["action"], r["n"], r["record_lock"]) == ("build", 1, True)


def test_plan_hold_and_override():
    assert plan(lu.empty(), hold=True)["action"] == "hold"
    assert plan(lu.empty(), hold=True, override_hold=True)["action"] == "build"


def test_plan_moved_tag_halts_even_when_built():
    lock = mklock(builds=[build(1)])
    r = plan(lock, commit=C2, our_releases=[rel("2.20.0-vp.1")])
    assert r["action"] == "halt"
    assert plan(lock, commit=C2, hold=True)["action"] == "hold"  # hold wins, still no build


def test_plan_already_built_is_noop():
    lock = mklock(builds=[build(1)])
    r = plan(lock, our_releases=[rel("2.20.0-vp.1")])
    assert (r["action"], r["n"]) == ("noop", 1)
    assert "already built" in r["reason"]


def test_plan_draft_release_does_not_count_as_built():
    lock = mklock(builds=[build(1)])
    runs = [{"headBranch": "build/2.20.0-n1", "status": "completed", "conclusion": "failure", "databaseId": 77}]
    r = plan(lock, our_releases=[rel("2.20.0-vp.1", draft=True)], runs=runs)
    assert (r["action"], r["rerun_run_id"]) == ("rerun", 77)


def test_plan_in_flight_noop():
    lock = mklock(builds=[build(1)])
    runs = [{"headBranch": "build/2.20.0-n1", "status": "in_progress", "databaseId": 5}]
    assert plan(lock, runs=runs)["reason"] == "build in flight"


def test_plan_retry_budget_then_stuck():
    lock = mklock(builds=[build(1)])
    runs = [{"headBranch": "build/2.20.0-n1", "status": "completed", "conclusion": "failure", "databaseId": i}
            for i in (5, 6, 7)]
    r = plan(lock, runs=runs)
    assert r["action"] == "noop" and r["stuck"] is True


def test_plan_branch_recorded_but_no_run_is_stuck():
    r = plan(mklock(builds=[build(1)]))
    assert r["action"] == "noop" and r["stuck"] is True


def test_plan_runs_of_other_branches_ignored():
    lock = mklock(builds=[build(1)])
    runs = [{"headBranch": "build/2.19.0-n1", "status": "in_progress", "databaseId": 5}]
    assert plan(lock, runs=runs)["stuck"] is True


def test_plan_series_change_bumps_n():
    lock = mklock(builds=[build(1, S1), build(2, S1)])
    r = plan(lock, series_sha=S2, our_releases=[rel("2.20.0-vp.1"), rel("2.20.0-vp.2")])
    assert (r["action"], r["n"]) == ("build", 3)


def test_plan_force_rebuild_bumps_n_even_if_same_series():
    lock = mklock(builds=[build(1)])
    r = plan(lock, force_rebuild=True, our_releases=[rel("2.20.0-vp.1")])
    assert (r["action"], r["n"]) == ("build", 2)


def test_plan_n_counts_published_releases_beyond_lock():
    lock = mklock(builds=[build(1, S1)])
    r = plan(lock, series_sha=S2, our_releases=[rel("2.20.0-vp.1"), rel("2.20.0-vp.7")])
    assert r["n"] == 8


def test_plan_counter_exhausted_halts():
    lock = mklock(builds=[build(87, S1)])
    assert plan(lock, series_sha=S2)["action"] == "halt"


def test_plan_release_without_lock_entry_is_noop():
    r = plan(lu.empty(), our_releases=[rel("2.20.0-vp.1")])
    assert r["action"] == "noop" and r["record_lock"] is True


def test_plan_older_than_newest_seen_is_skipped_unless_manual():
    lock = mklock(tag="2.21.0")
    assert plan(lock, tag="2.20.0")["action"] == "noop"
    assert plan(lock, tag="2.20.0", manual_tag=True)["action"] == "build"


def test_plan_ignores_other_tags_releases():
    r = plan(lu.empty(), our_releases=[rel("2.19.0-vp.3"), rel("2.20.0-vp.1x"), rel("22.20.0-vp.1")])
    assert r["action"] == "build" and r["n"] == 1


def test_plan_cli(tmp_path, capsys, monkeypatch):
    out = tmp_path / "o"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    (tmp_path / "l").write_text("")
    (tmp_path / "r.json").write_text("[]")
    rc = pb.main(["--tag", "2.20.0", "--commit", C1, "--lock", str(tmp_path / "l"),
                  "--our-releases", str(tmp_path / "r.json"), "--runs", str(tmp_path / "r.json"),
                  "--series-sha", S1, "--github-output"])
    assert rc == 0
    assert "action=build\n" in out.read_text() and "n=1\n" in out.read_text()


# --- policy -------------------------------------------------------------------------

POLICY = """\
schema: 1
allow_upstream_owners: [Nonary]
cooling_off_hours: {stable: 24, canary: 0}
hold: false
deny: []          # list of tags that must never install
keep_releases: 10
"""


def test_policy_parses_contract_file():
    p = policy.parse(POLICY)
    assert p["allow_upstream_owners"] == ["Nonary"] and p["hold"] is False
    assert p["cooling_off_hours"] == {"stable": 24, "canary": 0} and p["keep_releases"] == 10


def test_policy_deny_and_hold():
    p = policy.parse(POLICY.replace("deny: []", "deny: [2.20.0-vp.1, 2.20.0-vp.2]").replace("hold: false", "hold: true"))
    assert p["deny"] == ["2.20.0-vp.1", "2.20.0-vp.2"] and p["hold"] is True


@pytest.mark.parametrize("mutate", [
    lambda s: s.replace("schema: 1", "schema: 2"),
    lambda s: s.replace("[Nonary]", "[]"),
    lambda s: s.replace("[Nonary]", "[No nary;x]"),
    lambda s: s.replace("hold: false", "hold: maybe"),
    lambda s: s + "  nested: x\n",
    lambda s: s + "garbage line\n",
    lambda s: s.replace("deny: []", "deny: [$(id)]"),
    lambda s: s.replace("deny: []", "deny: [2.20.0]"),
])
def test_policy_fails_closed(mutate):
    with pytest.raises(v.ValidationError):
        policy.parse(mutate(POLICY))

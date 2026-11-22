import hashlib

import pytest

import lock_update as lu
import plan_build as pb
import series as sr
import vpcommon as v

C1 = "a" * 40
C2 = "b" * 40
S1 = "1" * 64


def test_reference_pin_reads_patch_engineer_lock(tmp_path):
    p = tmp_path / "upstream.lock"
    p.write_text("# c\nupstream:\n  repository: Nonary/Vibepollo\n  tag: 2.20.0\n  commit: " + C1 +
                 "   # x\n  release_id: null\npatch_series:\n  count: 8\n")
    assert lu.reference_pin(str(p)) == ("2.20.0", C1)
    assert lu.reference_pin(str(tmp_path / "missing")) is None
    p.write_text("upstream:\n  tag: $(id)\n  commit: " + C1 + "\n")
    with pytest.raises(v.ValidationError):
        lu.reference_pin(str(p))


def _plan(**kw):
    base = dict(tag="2.20.0", commit=C1, lock=lu.empty(), our_releases=[], runs=[], series_sha=S1)
    base.update(kw)
    return pb.plan(**base)


def test_plan_halts_when_tag_moved_vs_reference_pin():
    assert _plan(commit=C2, pin=("2.20.0", C1))["action"] == "halt"
    assert _plan(commit=C1, pin=("2.20.0", C1))["action"] == "build"
    assert _plan(commit=C2, pin=("2.19.0", C1))["action"] == "build"  # pin belongs to another tag


def test_series_fingerprint_definition_matches_patch_engineer(tmp_path):
    """sha256( series with CR removed || raw bytes of each patch in order ), see tools/series-sha.sh."""
    d = tmp_path / "p"
    d.mkdir()
    a = b"AAA\r\nBBB\n"
    (d / "0001-a.patch").write_bytes(a)
    (d / "0002-b.patch").write_bytes(b"CCC")
    series = b"# c\r\n0001-a.patch\r\n\n0002-b.patch  # t\n"
    (d / "series").write_bytes(series)
    expect = hashlib.sha256(series.replace(b"\r", b"") + a + b"CCC").hexdigest()
    assert sr.fingerprint(str(d / "series"), str(d))["series_sha256"] == expect

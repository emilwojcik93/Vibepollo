import json
import os

import pytest

import select_release as sr
import validate_tag
import vpcommon as v

HERE = os.path.dirname(os.path.abspath(__file__))

MALICIOUS = [
    "$(id)", "`id`", "2.20.0\n", "2.20.0\n3.0.0", "../2.20.0", "../../etc/passwd", "2.20.0;rm -rf /",
    "2.20.0 && curl x", "2.20.0|id", "2.20.0\r\n", "٢.20.0", "２.２０.０", "2.20.0‮",
    "12345.1.1", "1.2", "1.2.3.4", "-1.2.3", "v2.20.0", "2.20.0-vp.1", "", " 2.20.0", "2.20.0 ",
    "2.20.0\x00", "${{ github.token }}", "2.20.0'", '2.20.0"', "2.20.0#", "2.20.0%0a",
]


@pytest.mark.parametrize("tag", ["0.0.0", "2.20.0", "2.18.19", "9999.9999.9999"])
def test_upstream_tag_accepts(tag):
    assert v.upstream_tag(tag) == tag


@pytest.mark.parametrize("tag", MALICIOUS)
def test_upstream_tag_rejects(tag):
    with pytest.raises(v.ValidationError):
        v.upstream_tag(tag)


@pytest.mark.parametrize("tag", ["2.20.0-vp.1", "2.20.0-vp.999", "0.0.0-vp.1"])
def test_release_tag_accepts(tag):
    assert v.release_tag(tag) == tag


@pytest.mark.parametrize("tag", MALICIOUS + ["2.20.0-vp.", "2.20.0-vp.1000", "2.20.0-vp.1\n", "2.20.0-posture.1",
                                            "2.20.0-VP.1", "2.20.0-vp.-1"])
def test_release_tag_rejects_bad(tag):
    if tag == "2.20.0-vp.1":
        return
    with pytest.raises(v.ValidationError):
        v.release_tag(tag)


def test_non_string_rejected():
    for bad in (None, 5, b"2.20.0", ["2.20.0"]):
        with pytest.raises(v.ValidationError):
            v.upstream_tag(bad)


def test_error_message_does_not_echo_payload():
    try:
        v.upstream_tag("$(curl evil)\n::set-output name=x::y")
    except v.ValidationError as exc:
        msg = str(exc)
        assert "\n" not in msg
        assert "::set-output" not in msg and "curl" not in msg


def test_ordering_key():
    assert v.release_tag_key("2.20.0") == (2, 20, 0, 0)
    assert v.release_tag_key("2.20.0-vp.1") == (2, 20, 0, 1)
    keys = [v.release_tag_key(t) for t in ("2.20.0", "2.20.0-vp.1", "2.20.0-vp.2", "2.20.1-vp.1", "2.21.0")]
    assert keys == sorted(keys)
    assert v.release_tag_key("2.20.0-vp.10") > v.release_tag_key("2.20.0-vp.9")  # numeric, not lexical


def test_branch_names():
    assert v.branch_name("2.20.0", 3) == "build/2.20.0-n3"
    assert v.parse_branch("build/2.20.0-n3") == ("2.20.0", 3)
    for bad in ("build/2.20.0-n", "build/../x-n1", "build/2.20.0-n1\n", "feature/x", "build/2.20.0-n1/extra"):
        assert v.parse_branch(bad) is None
    with pytest.raises(v.ValidationError):
        v.branch_name("2.20.0", 0)
    with pytest.raises(v.ValidationError):
        v.branch_name("2.20.0", 88)


def test_write_outputs_blocks_injection(tmp_path):
    out = tmp_path / "o"
    v.write_outputs({"tag": "2.20.0", "ref": "refs/heads/build/2.20.0-n1"}, str(out))
    assert out.read_text() == "tag=2.20.0\nref=refs/heads/build/2.20.0-n1\n"
    for bad in ("x\ny=z", "a;b", "$(id)", "`id`", "a\rb", "x" * 301):
        with pytest.raises(v.ValidationError):
            v.write_outputs({"k": bad}, str(out))
    with pytest.raises(v.ValidationError):
        v.write_outputs({"Bad Key": "x"}, str(out))
    with pytest.raises(v.ValidationError):
        v.write_outputs({"k\nj": "x"}, str(out))


def test_validate_tag_cli(capsys):
    assert validate_tag.main(["upstream-tag", "2.20.0"]) == 0
    assert validate_tag.main(["upstream-tag", "$(id)"]) == 1
    err = capsys.readouterr().err
    assert "::error::" in err and "$(id)" not in err
    assert validate_tag.main(["nope", "x"]) == 2
    assert validate_tag.main(["sha1", "a" * 40]) == 0
    assert validate_tag.main(["sha1", "A" * 40]) == 1
    assert validate_tag.main(["run-id", "123456789"]) == 0
    assert validate_tag.main(["run-id", "12a"]) == 1
    assert validate_tag.main(["repo-slug", "emilwojcik93/Vibepollo"]) == 0
    assert validate_tag.main(["repo-slug", "a/b/c"]) == 1


# --- release selection -------------------------------------------------------------

def load_fixture():
    with open(os.path.join(HERE, "fixtures", "releases.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_select_picks_newest_numeric_published():
    res = sr.select(load_fixture(), slug="Nonary/Vibepollo", allow=["Nonary"])
    # 9.9.9 is a valid published numeric tag and therefore the max; drafts and junk are skipped.
    assert res["selected"]["tag"] == "9.9.9"
    tags = [c["tag"] for c in sr.numeric_candidates(load_fixture())]
    assert tags == ["9.9.9", "2.20.0", "2.19.0", "2.18.19"]


def test_select_skips_every_malicious_tag():
    tags = {c["tag"] for c in sr.numeric_candidates(load_fixture())}
    for t in tags:
        assert v.UPSTREAM_TAG_RE.fullmatch(t)
    assert "2.21.0" not in tags  # draft
    assert "2.22.0" not in tags  # unpublished
    assert "2.20.0-vp.1" not in tags  # our own tags are not upstream releases


def test_select_explicit_tag_and_missing():
    rels = load_fixture()
    res = sr.select(rels, slug="Nonary/Vibepollo", allow=["Nonary"], tag="2.19.0")
    assert res["selected"]["release_id"] == 190
    assert sr.select(rels, slug="Nonary/Vibepollo", allow=["Nonary"], tag="2.21.0")["selected"] is None
    with pytest.raises(v.ValidationError):
        sr.select(rels, slug="Nonary/Vibepollo", allow=["Nonary"], tag="$(id)")


def test_select_owner_allowlist():
    with pytest.raises(v.ValidationError):
        sr.select(load_fixture(), slug="evil/Vibepollo", allow=["Nonary"])
    with pytest.raises(v.ValidationError):
        sr.select(load_fixture(), slug="Nonary/Vibepollo", allow=["Nonary;x"])
    with pytest.raises(v.ValidationError):
        sr.select(load_fixture(), slug="Nonary/Vibepollo/extra", allow=["Nonary"])


def test_select_is_numeric_not_lexical():
    rels = [{"id": i + 1, "tag_name": t, "draft": False, "published_at": "2026-01-01T00:00:00Z"}
            for i, t in enumerate(["2.9.0", "2.10.0", "2.2.30"])]
    assert sr.select(rels, slug="Nonary/Vibepollo", allow=["Nonary"])["selected"]["tag"] == "2.10.0"


def test_select_rejects_garbage_payload():
    with pytest.raises(v.ValidationError):
        sr.numeric_candidates({"message": "Not Found"})
    assert sr.numeric_candidates([1, "x", None, {"tag_name": 5}]) == []
    bad_id = [{"id": "5", "tag_name": "2.20.0", "draft": False, "published_at": "2026-01-01T00:00:00Z"},
              {"id": True, "tag_name": "2.20.1", "draft": False, "published_at": "2026-01-01T00:00:00Z"}]
    assert sr.numeric_candidates(bad_id) == []


def test_previous_of():
    cands = sr.numeric_candidates(load_fixture())
    assert sr.previous_of(cands, "2.20.0") == "2.19.0"
    assert sr.previous_of(cands, "2.18.19") is None


def test_select_cli(tmp_path, capsys, monkeypatch):
    out = tmp_path / "gho"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    rc = sr.main(["--releases", os.path.join(HERE, "fixtures", "releases.json"), "--repo", "Nonary/Vibepollo",
                  "--allow", "Nonary,Other", "--tag", "2.20.0", "--github-output"])
    assert rc == 0
    text = out.read_text()
    assert "tag=2.20.0\n" in text and "release_id=200\n" in text and "prev_tag=2.19.0\n" in text
    assert sr.main(["--releases", "nonexistent.json", "--repo", "Nonary/Vibepollo", "--allow", "Nonary"]) == 1

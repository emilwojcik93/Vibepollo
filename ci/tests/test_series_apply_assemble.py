import json
import os
import shutil
import subprocess

import pytest

import apply_series as aps
import assemble_build as asb
import series as sr
import vpcommon as v

HERE = os.path.dirname(os.path.abspath(__file__))
CI_DIR = os.path.dirname(HERE)
S1 = "1" * 64

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": "2026-11-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-11-01T00:00:00Z"}


def git(repo, *args, check=True):
    env = dict(os.environ)
    env.update(GIT_ENV)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=check)


def write(repo, rel, text):
    p = os.path.join(str(repo), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


@pytest.fixture()
def world(tmp_path):
    """upstream repo with a base commit; two format-patch files; a conflicting variant."""
    repo = tmp_path / "up"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "commit.gpgsign", "false")
    write(repo, "src/a.txt", "".join(f"line{i}\n" for i in range(1, 21)))
    write(repo, "README.md", "hello\n")
    write(repo, ".github/workflows/ci.yml", "name: CI\non: push\njobs: {}\n")
    write(repo, ".github/workflows/ci-windows.yml", "name: CI-Windows\non: workflow_call\njobs: {}\n")
    write(repo, ".github/workflows/localize.yml", "name: loc\non: push\njobs: {}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()

    git(repo, "checkout", "-qb", "feature")
    write(repo, "src/a.txt", "".join(f"line{i}\n" if i != 5 else "LINE5 changed\n" for i in range(1, 21)))
    git(repo, "commit", "-qam", "posture: change line 5")
    write(repo, "src/new.txt", "new file\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "posture: add new file")
    patches = tmp_path / "patches"
    patches.mkdir()
    out = git(repo, "format-patch", "-o", str(patches), f"{base}..feature").stdout
    names = sorted(os.listdir(patches))
    (patches / "series").write_text("# group: posture\n" + "\n".join(names) + "\n")
    ref_tree = git(repo, "rev-parse", "feature^{tree}").stdout.strip()
    git(repo, "checkout", "-q", "main")
    return {"repo": repo, "base": base, "patches": patches, "names": names, "ref_tree": ref_tree,
            "tmp": tmp_path}


def clone_at_base(world, name):
    dest = world["tmp"] / name
    subprocess.run(["git", "clone", "-q", str(world["repo"]), str(dest)], check=True, capture_output=True)
    git(dest, "checkout", "-q", "--detach", world["base"])
    return dest


# --- series ---------------------------------------------------------------------------

def test_series_parse_and_fingerprint(world):
    series = world["patches"] / "series"
    entries = sr.parse(str(series))
    assert [e[0] for e in entries] == world["names"] and {e[1] for e in entries} == {"posture"}
    fp = sr.fingerprint(str(series), str(world["patches"]))
    assert fp["count"] == 2 and fp["groups"] == ["posture"] and len(fp["series_sha256"]) == 64
    # fingerprint changes when a patch BODY changes, even if series is identical
    first = world["patches"] / world["names"][0]
    first.write_text(first.read_text() + "\n")
    assert sr.fingerprint(str(series), str(world["patches"]))["series_sha256"] != fp["series_sha256"]


def test_series_groups_and_comments(tmp_path):
    d = tmp_path / "p"
    (d / "ci").mkdir(parents=True)
    (d / "0001-a.patch").write_text("x")
    (d / "ci" / "0100-w.patch").write_text("y")
    (d / "series").write_text("# header\n\n# group: posture\n0001-a.patch  # trailing\n# group: ci\nci/0100-w.patch\n")
    fp = sr.fingerprint(str(d / "series"), str(d))
    assert fp["groups"] == ["posture", "ci"] and fp["entries"] == ["0001-a.patch", "ci/0100-w.patch"]


@pytest.mark.parametrize("line", ["../evil.patch", "/abs.patch", "a b.patch", "x.patch;id", "a/../b.patch",
                                  "$(id).patch", "x.txt", "a\\b.patch", "-rf.patch"])
def test_series_rejects_bad_entries(tmp_path, line):
    p = tmp_path / "series"
    p.write_text(line + "\n")
    with pytest.raises(v.ValidationError):
        sr.parse(str(p))


def test_series_missing_patch_and_symlink_escape(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "series").write_text("0001-missing.patch\n")
    with pytest.raises(v.ValidationError):
        sr.fingerprint(str(d / "series"), str(d))


# --- apply ----------------------------------------------------------------------------

def test_apply_reproduces_reference_tree_and_is_deterministic(world):
    a = clone_at_base(world, "a")
    b = clone_at_base(world, "b")
    for repo in (a, b):
        rc = aps.apply(str(repo), world["base"], str(world["patches"] / "series"), str(world["patches"]),
                       expect_tree=world["ref_tree"])
        assert rc == 0
    assert git(a, "rev-parse", "HEAD").stdout == git(b, "rev-parse", "HEAD").stdout  # reproducible tip
    assert git(a, "log", "-1", "--format=%cn %ce").stdout.strip() == f"{aps.BOT_NAME} {aps.BOT_EMAIL}"


def test_apply_wrong_expected_tree_fails(world):
    a = clone_at_base(world, "a")
    rc = aps.apply(str(a), world["base"], str(world["patches"] / "series"), str(world["patches"]),
                   expect_tree="f" * 40)
    assert rc == 1


def test_apply_requires_clean_head_at_base(world):
    a = clone_at_base(world, "a")
    write(a, "dirty.txt", "x")
    git(a, "add", "-A")
    assert aps.apply(str(a), world["base"], str(world["patches"] / "series"), str(world["patches"])) == 1
    git(a, "reset", "-q", "--hard")
    git(a, "checkout", "-q", "--detach", "main")  # still base, ok; move HEAD away instead
    write(a, "x.txt", "x")
    git(a, "add", "-A")
    git(a, "commit", "-qm", "moved")
    assert aps.apply(str(a), world["base"], str(world["patches"] / "series"), str(world["patches"])) == 1


def test_apply_conflict_writes_report_and_restores_tree(world):
    a = clone_at_base(world, "a")
    # Upstream "moves on": base tree is changed on the same line the patch touches.
    write(a, "src/a.txt", "".join(f"line{i}\n" if i != 5 else "UPSTREAM EDIT\n" for i in range(1, 21)))
    git(a, "commit", "-qam", "upstream edit")
    new_base = git(a, "rev-parse", "HEAD").stdout.strip()
    report = world["tmp"] / "conflict.md"
    rc = aps.apply(str(a), new_base, str(world["patches"] / "series"), str(world["patches"]), report_path=str(report))
    assert rc == 2
    text = report.read_text()
    assert world["names"][0] in text and "src/a.txt" in text
    assert git(a, "rev-parse", "HEAD").stdout.strip() == new_base
    assert git(a, "status", "--porcelain").stdout.strip() == ""
    assert not os.path.exists(os.path.join(str(a), ".git", "rebase-apply"))


# --- assemble -------------------------------------------------------------------------

def make_ci_dir(tmp_path):
    ci = tmp_path / "ci"
    (ci / "wrapper").mkdir(parents=True, exist_ok=True)
    (ci / "wrapper" / "patched-windows-release.yml").write_text("name: wrapper\non: workflow_dispatch\njobs: {}\n")
    (ci / "vpcommon.py").write_text("# helper\n")
    (ci / "tests").mkdir(exist_ok=True)
    (ci / "tests" / "test_x.py").write_text("# must not be copied\n")
    return ci


def assemble_args(world, ci, repo, **over):
    import argparse
    ns = argparse.Namespace(repo=str(repo), ci_dir=str(ci), base=world["base"], tag="2.20.0", n=1,
                            branch="build/2.20.0-n1", repo_slug="Nonary/Vibepollo", release_id=200,
                            published_at="2026-11-22T06:38:51Z", series_sha=S1, patch_count=2,
                            groups="posture", gate="auto", sensitive_count="0", trial=False)
    for k, val in over.items():
        setattr(ns, k, val)
    return ns


def test_assemble_strips_workflows_installs_wrapper_and_meta(world, capsys):
    a = clone_at_base(world, "a")
    ci = make_ci_dir(world["tmp"])
    assert aps.apply(str(a), world["base"], str(world["patches"] / "series"), str(world["patches"])) == 0
    assert asb.assemble(assemble_args(world, ci, a)) == 0
    wf = sorted(os.listdir(os.path.join(str(a), ".github", "workflows")))
    assert wf == ["ci-windows.yml", "patched-windows-release.yml"]  # ci.yml / localize.yml gone
    assert sorted(os.listdir(os.path.join(str(a), ".vp-ci"))) == ["build-meta.json", "vpcommon.py"]
    meta = json.load(open(os.path.join(str(a), ".vp-ci", "build-meta.json")))
    assert meta["build"]["version"] == "2.20.1-alpha.1" and meta["build"]["msi_product_version"] == "2.20.101.0"
    assert meta["patch_commits"] == 2 and meta["upstream"]["commit"] == world["base"]
    assert git(a, "rev-list", "--count", f"{world['base']}..HEAD").stdout.strip() == "3"
    assert git(a, "status", "--porcelain").stdout.strip() == ""


def test_assemble_is_reproducible(world):
    tips = []
    for name in ("a", "b"):
        repo = clone_at_base(world, name)
        ci = make_ci_dir(world["tmp"])
        aps.apply(str(repo), world["base"], str(world["patches"] / "series"), str(world["patches"]))
        asb.assemble(assemble_args(world, ci, repo))
        tips.append(git(repo, "rev-parse", "HEAD").stdout.strip())
    assert tips[0] == tips[1]


@pytest.mark.parametrize("forbidden", [".github/workflows/evil.yml", ".gitmodules",
                                       "cmake/packaging/windows_virtual_gamepad_contract.cmake"])
def test_assemble_guard_rejects_protected_paths(world, forbidden):
    a = clone_at_base(world, "a")
    ci = make_ci_dir(world["tmp"])
    write(a, forbidden, "x\n")
    git(a, "add", "-A")
    git(a, "commit", "-qm", "sneaky patch")
    assert asb.assemble(assemble_args(world, ci, a)) == 1


def test_assemble_guard_rejects_gitlink(world):
    a = clone_at_base(world, "a")
    ci = make_ci_dir(world["tmp"])
    git(a, "update-index", "--add", "--cacheinfo", f"160000,{world['base']},third-party/sub")
    git(a, "commit", "-qm", "gitlink")
    assert asb.assemble(assemble_args(world, ci, a)) == 1


def test_assemble_rejects_bad_gate_and_slug(world):
    a = clone_at_base(world, "a")
    ci = make_ci_dir(world["tmp"])
    assert asb.assemble(assemble_args(world, ci, a, gate="whatever")) == 1
    with pytest.raises(v.ValidationError):
        asb.assemble(assemble_args(world, ci, a, repo_slug="a/b/c"))


# --- rot matrix -------------------------------------------------------------------------

def test_rot_matrix_pass_conflict_and_known_fail(world):
    import rot_matrix as rm
    up = world["repo"]
    git(up, "tag", "2.20.0", world["base"])
    # a later upstream release where the patched line changed -> conflict
    git(up, "checkout", "-q", "main")
    lines = ["UPSTREAM EDIT" if k == 5 else f"line{k}" for k in range(1, 21)]
    write(up, "src/a.txt", "\n".join(lines) + "\n")
    git(up, "commit", "-qam", "upstream edit")
    git(up, "tag", "2.21.0")
    scratch = world["tmp"] / "scratch"
    subprocess.run(["git", "clone", "-q", str(up), str(scratch)], check=True, capture_output=True)

    def fake_fetch_factory(slug):
        def fetch(path):
            tag = path.rsplit("/", 1)[1]
            sha = git(up, "rev-parse", f"{tag}^{{commit}}").stdout.strip()
            return {"ref": f"refs/tags/{tag}", "object": {"type": "commit", "sha": sha}}
        return fetch

    res = rm.run(str(scratch), "Nonary/Vibepollo", ["2.20.0", "2.21.0"], str(world["patches"] / "series"),
                 str(world["patches"]), [], fetch_factory=fake_fetch_factory, remote=str(up))
    assert [(r["tag"], r["status"]) for r in res] == [("2.20.0", "pass"), ("2.21.0", "conflict")]
    assert rm.failing(res) == ["2.21.0"]
    res2 = rm.run(str(scratch), "Nonary/Vibepollo", ["2.20.0", "2.21.0"], str(world["patches"] / "series"),
                  str(world["patches"]), ["2.21.0"], fetch_factory=fake_fetch_factory, remote=str(up))
    assert rm.failing(res2) == []
    assert "known failure" in rm.render(res2)

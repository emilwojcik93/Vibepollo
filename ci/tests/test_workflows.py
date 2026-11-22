"""Structural security checks over the workflow files (complements actionlint/zizmor)."""
import glob
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOWS = sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")))
WRAPPER = os.path.join(ROOT, "ci", "wrapper", "patched-windows-release.yml")
ALL = WORKFLOWS + [WRAPPER]


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def uses_lines(text):
    for ln in text.splitlines():
        m = re.match(r"\s*(?:-\s*)?uses:\s*(\S+)(?:\s*#\s*(.*))?$", ln)
        if m:
            yield m.group(1), m.group(2) or ""


def run_blocks(text):
    """Yield the text of every ``run:`` value (block scalar or single line)."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"(\s*)(?:-\s*)?run:\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent, rest = len(m.group(1)), m.group(2)
        if rest.startswith(("|", ">")):
            block = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                block.append(lines[i])
                i += 1
            yield "\n".join(block)
        else:
            yield rest
            i += 1


def jobs(text):
    """Return {job_name: text} for the top-level jobs mapping (2-space indented keys)."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


@pytest.mark.parametrize("path", ALL, ids=os.path.basename)
def test_top_level_permissions_empty(path):
    assert re.search(r"(?m)^permissions:\s*\{\}\s*$", read(path))


@pytest.mark.parametrize("path", ALL, ids=os.path.basename)
def test_no_dangerous_triggers(path):
    text = read(path)
    for bad in ("pull_request_target", "issue_comment", "workflow_run", "pull_request_review", "discussion"):
        assert not re.search(rf"(?m)^\s*{bad}\b", text), bad


@pytest.mark.parametrize("path", ALL, ids=os.path.basename)
def test_every_action_pinned_by_sha_with_version_comment(path):
    for ref, comment in uses_lines(read(path)):
        if ref.startswith("./"):
            continue
        assert re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}", ref), ref
        assert re.match(r"v[0-9]+(\.[0-9]+)*", comment), f"missing version comment: {ref}"


def test_same_action_same_sha_everywhere():
    seen = {}
    for path in ALL:
        for ref, _ in uses_lines(read(path)):
            if ref.startswith("./"):
                continue
            name, sha = ref.split("@")
            assert seen.setdefault(name, sha) == sha, f"{name} pinned to different SHAs"


@pytest.mark.parametrize("path", ALL, ids=os.path.basename)
def test_no_expression_interpolation_in_run_scripts(path):
    for block in run_blocks(read(path)):
        assert "${{" not in block, block[:120]


@pytest.mark.parametrize("path", ALL, ids=os.path.basename)
def test_checkout_never_persists_credentials(path):
    text = read(path)
    for m in re.finditer(r"uses:\s*actions/checkout@[^\n]*\n((?:\s+[a-z-]+:[^\n]*\n)+)", text):
        assert "persist-credentials: false" in m.group(1)


@pytest.mark.parametrize("path", ALL, ids=os.path.basename)
def test_every_job_has_a_timeout_or_is_reusable_call(path):
    for name, body in jobs(read(path)).items():
        assert "timeout-minutes:" in body or re.search(r"(?m)^    uses:", body), name


def test_wrapper_privilege_separation():
    j = jobs(read(WRAPPER))
    for name in ("validate", "build", "package"):
        assert "write" not in re.sub(r"#.*", "", j[name].split("steps:")[0]), name
        assert "id-token" not in j[name]
    assert "permissions: {}" in j["package"]
    pub = j["publish"]
    assert "id-token: write" in pub and "attestations: write" in pub and "contents: write" in pub
    assert "environment:" in pub
    assert "dry_run == 'false'" in pub
    assert "secrets." not in read(WRAPPER)
    # build job passes no secrets to upstream's reusable workflow and never runs on a tag ref
    assert "secrets:" not in j["build"] and "inherit" not in j["build"]


def test_wrapper_uses_secret_free_inputs():
    build = jobs(read(WRAPPER))["build"]
    for expect in ("build_only: false", "daily_unsigned: false", "release_channel: ''", "release_tag: ''",
                   "publish_symbols: false", "upload_symbols_artifact: false", "release_artifact_retention_days: 7",
                   "build_tests: ${{ needs.validate.outputs.tests == 'true' }}",
                   "release_version: ${{ needs.validate.outputs.build_version }}"):
        assert expect in build, expect


def test_wrapper_triggers_only_build_branches():
    text = read(WRAPPER)
    assert "'build/**'" in text and "'ci-trial/**'" in text
    assert not re.search(r"(?m)^\s*(tags|schedule):", text)


def test_upstream_watch_privileges():
    j = jobs(read(os.path.join(ROOT, ".github", "workflows", "upstream-watch.yml")))
    assert "write" not in re.sub(r"#.*", "", j["resolve"].split("steps:")[0])
    assert "environment: build-push" in j["prepare"]
    assert "create-github-app-token" in j["prepare"]
    # no fallback to a broader token for the branch push
    assert "secrets.GITHUB_TOKEN" not in read(os.path.join(ROOT, ".github", "workflows", "upstream-watch.yml"))
    assert "--force" not in j["prepare"].replace("# No --force", "")


def test_wrapper_template_pins_match_workflows():
    """Dependabot does not scan ci/wrapper; fail when its pins drift from the real workflows."""
    wf_pins = {}
    for path in WORKFLOWS:
        for ref, _ in uses_lines(read(path)):
            if not ref.startswith("./"):
                name, sha = ref.split("@")
                wf_pins.setdefault(name, set()).add(sha)
    for ref, _ in uses_lines(read(WRAPPER)):
        if ref.startswith("./"):
            continue
        name, sha = ref.split("@")
        if name in wf_pins:
            assert sha in wf_pins[name], f"{name}: wrapper pin differs from .github/workflows (sync after Dependabot)"

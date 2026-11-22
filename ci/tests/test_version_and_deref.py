import pytest

import deref_commit as dc
import version_map as vm
import vpcommon as v


# --- version mapper -----------------------------------------------------------------

def test_contract_examples():
    assert vm.build_version("2.20.0", 1) == "2.20.1-alpha.1"
    assert vm.msi_product_version("2.20.0", 1) == "2.20.101.0"
    assert vm.upstream_msi_version("2.20.0") == "2.20.99.0"
    assert vm.compute("2.20.0", 1)["release_tag"] == "2.20.0-vp.1"
    assert vm.compute("2.20.0", 1)["branch"] == "build/2.20.0-n1"


@pytest.mark.parametrize("n,label,k,ord_", [
    (1, "alpha", 1, 1), (29, "alpha", 29, 29),
    (30, "beta", 1, 31), (58, "beta", 29, 59),
    (59, "rc", 1, 61), (87, "rc", 29, 89),
])
def test_prerelease_boundaries(n, label, k, ord_):
    assert vm.prerelease_for(n) == (label, k, ord_)
    assert vm.build_version("2.20.0", n) == f"2.20.1-{label}.{k}"
    assert vm.msi_product_version("2.20.0", n) == f"2.20.{100 + ord_}.0"


@pytest.mark.parametrize("n", [0, -1, 88, 1000, True, "1", None])
def test_n_out_of_range(n):
    with pytest.raises(v.ValidationError):
        vm.prerelease_for(n)


@pytest.mark.parametrize("tag", ["0.0.0", "2.20.0", "2.20.5", "2.18.19", "255.255.653", "1.0.99"])
def test_strictly_between_upstream_neighbours_for_every_n(tag):
    """AC-BLD-3: above upstream X.Y.Z, below upstream X.Y.(Z+1), monotonic in N."""
    x, y, z = v.tag_tuple(tag)
    floor = vm.msi_tuple(vm.upstream_msi_version(tag))
    ceiling = vm.msi_tuple(vm.upstream_msi_version(f"{x}.{y}.{z + 1}"))
    prev = floor
    for n in range(1, 88):
        cur = vm.msi_tuple(vm.msi_product_version(tag, n))
        assert prev < cur < ceiling
        prev = cur


def test_limits():
    with pytest.raises(v.ValidationError):
        vm.compute("256.0.0", 1)
    with pytest.raises(v.ValidationError):
        vm.compute("2.256.0", 1)
    with pytest.raises(v.ValidationError):
        vm.compute("2.20.654", 1)  # Z+1 = 655
    assert vm.compute("2.20.653", 87)["msi_product_version"].startswith("2.20.65489")
    with pytest.raises(v.ValidationError):
        vm.compute("$(id)", 1)


def test_incident_posture_label_would_be_lower():
    # The rejected scheme: unknown label -> ORD 90 -> 2.20.90.0 < upstream 2.20.99.0.
    assert vm.msi_tuple("2.20.90.0") < vm.msi_tuple(vm.upstream_msi_version("2.20.0"))
    assert vm.msi_tuple(vm.msi_product_version("2.20.0", 1)) > vm.msi_tuple("2.20.99.0")


def test_msi_tuple():
    assert vm.msi_tuple("2.20.101") == (2, 20, 101, 0)
    assert vm.msi_tuple("2.20.101.0") == (2, 20, 101, 0)
    for bad in ("2.20", "2.20.1.2.3", "a.b.c", "2.20.-1", "2.20.1\n"):
        with pytest.raises(v.ValidationError):
            vm.msi_tuple(bad)


def test_version_cli(capsys):
    assert vm.main(["--tag", "2.20.0", "--n", "2"]) == 0
    out = capsys.readouterr().out
    assert "build_version=2.20.1-alpha.2" in out and "msi_product_version=2.20.102.0" in out
    assert vm.main(["--tag", "bad", "--n", "1"]) == 1


# --- deref ---------------------------------------------------------------------------

C1 = "a" * 40
T1 = "b" * 40
T2 = "c" * 40


def fake_fetch(table):
    def fetch(path):
        return table[path]
    return fetch


def test_deref_lightweight():
    f = fake_fetch({"git/ref/tags/2.20.0": {"ref": "refs/tags/2.20.0", "object": {"type": "commit", "sha": C1}}})
    assert dc.deref(f, "2.20.0") == C1


def test_deref_annotated_and_nested():
    f = fake_fetch({
        "git/ref/tags/2.20.0": {"ref": "refs/tags/2.20.0", "object": {"type": "tag", "sha": T1}},
        f"git/tags/{T1}": {"object": {"type": "tag", "sha": T2}},
        f"git/tags/{T2}": {"object": {"type": "commit", "sha": C1}},
    })
    assert dc.deref(f, "2.20.0") == C1


def test_deref_rejects_wrong_ref_and_types():
    wrong = fake_fetch({"git/ref/tags/2.20.0": {"ref": "refs/tags/2.20.0-rc1", "object": {"type": "commit", "sha": C1}}})
    with pytest.raises(v.ValidationError):
        dc.deref(wrong, "2.20.0")
    tree = fake_fetch({"git/ref/tags/2.20.0": {"ref": "refs/tags/2.20.0", "object": {"type": "tree", "sha": C1}}})
    with pytest.raises(v.ValidationError):
        dc.deref(tree, "2.20.0")
    badsha = fake_fetch({"git/ref/tags/2.20.0": {"ref": "refs/tags/2.20.0", "object": {"type": "commit", "sha": "zz"}}})
    with pytest.raises(v.ValidationError):
        dc.deref(badsha, "2.20.0")
    with pytest.raises(v.ValidationError):
        dc.deref(wrong, "$(id)")
    garbage = fake_fetch({"git/ref/tags/2.20.0": [{"ref": "x"}]})
    with pytest.raises(v.ValidationError):
        dc.deref(garbage, "2.20.0")


def test_deref_loop_is_bounded():
    table = {"git/ref/tags/2.20.0": {"ref": "refs/tags/2.20.0", "object": {"type": "tag", "sha": T1}},
             f"git/tags/{T1}": {"object": {"type": "tag", "sha": T1}}}
    with pytest.raises(v.ValidationError):
        dc.deref(fake_fetch(table), "2.20.0")

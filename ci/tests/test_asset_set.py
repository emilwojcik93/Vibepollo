import json
import os

import pytest

import manifest_gen as mg
import stage_assets as st
import vpcommon as v
from test_release_assets import C1, S1, TS, gen_args, make_dist_inputs

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WRAPPER = os.path.join(ROOT, "ci", "wrapper", "patched-windows-release.yml")


def build_dist(tmp_path):
    b, m = make_dist_inputs(tmp_path)
    dist = tmp_path / "dist"
    st.stage(str(b), str(m), str(dist), True, "2.20.101.0")
    manifest = mg.build_manifest(gen_args(dist))
    (dist / "manifest.json").write_text(json.dumps(manifest))
    mg.write_sums(str(dist), [a["name"] for a in manifest["assets"]] + ["manifest.json"])
    (dist / "attestation.sigstore.json").write_text("{}")
    return dist


def test_complete_set_includes_release_provenance(tmp_path):
    dist = build_dist(tmp_path)
    names = mg.check_asset_set(str(dist))
    assert names == sorted(["VibepolloSetup.exe", "Vibepollo.msi", "release-provenance.json", "SHA256SUMS",
                            "manifest.json", "attestation.sigstore.json"])


def test_missing_listed_asset_is_caught_before_publish(tmp_path):
    """Defect from the first real release: provenance listed in manifest/SHA256SUMS but not uploaded."""
    dist = build_dist(tmp_path)
    os.remove(dist / "release-provenance.json")
    with pytest.raises(v.ValidationError, match="missing"):
        mg.check_asset_set(str(dist))


def test_unlisted_extra_file_is_caught(tmp_path):
    dist = build_dist(tmp_path)
    (dist / "surprise.bin").write_bytes(b"x")
    with pytest.raises(v.ValidationError, match="extra"):
        mg.check_asset_set(str(dist))


def test_missing_bundle_is_caught(tmp_path):
    dist = build_dist(tmp_path)
    os.remove(dist / "attestation.sigstore.json")
    with pytest.raises(v.ValidationError):
        mg.check_asset_set(str(dist))


def test_sums_must_match_manifest_assets(tmp_path):
    dist = build_dist(tmp_path)
    lines = (dist / "SHA256SUMS").read_text().splitlines()
    (dist / "SHA256SUMS").write_text("\n".join(ln for ln in lines if "release-provenance" not in ln) + "\n")
    with pytest.raises(v.ValidationError, match="SHA256SUMS"):
        mg.check_asset_set(str(dist))


def test_tampered_file_is_caught(tmp_path):
    dist = build_dist(tmp_path)
    (dist / "Vibepollo.msi").write_bytes(b"tampered")
    with pytest.raises(v.ValidationError):
        mg.check_asset_set(str(dist))


def test_assets_list_cli(tmp_path, capsys):
    dist = build_dist(tmp_path)
    assert mg.main(["assets-list", "--dist", str(dist)]) == 0
    assert "release-provenance.json" in capsys.readouterr().out
    os.remove(dist / "release-provenance.json")
    assert mg.main(["assets-list", "--dist", str(dist)]) == 1


def test_wrapper_uploads_exactly_the_checked_list_and_attests_provenance():
    text = open(WRAPPER, encoding="utf-8").read()
    assert "manifest_gen.py assets-list --dist dist" in text
    assert 'files+=("dist/$n")' in text and '"${files[@]}"' in text
    assert "dist/release-provenance.json" in text  # attest subject
    assert "uploaded asset set differs from the manifest asset set" in text
    # the assertion step precedes the draft creation
    assert text.index("Assert the upload set") < text.index("Create DRAFT release")
    assert "gh variable set IMMUTABLE_CONFIRMED --body true" in text

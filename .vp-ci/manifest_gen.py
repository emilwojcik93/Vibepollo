#!/usr/bin/env python3
"""Generate ``manifest.json`` (CONTRACT section 3, schema 1) and ``SHA256SUMS``.

Both files are written into the ``--dist`` directory next to the assets.

* ``assets[]`` lists the payload files (installer, optional ``Vibepollo.msi``,
  upstream ``release-provenance.json``) with sha256 + size.
* ``SHA256SUMS`` covers every payload asset **and** ``manifest.json`` (but not
  itself and not the attestation bundle) -- ``sha256  filename`` lines.
* An optional ``--msi-summary`` JSON (from ``msi_summary.py``, produced by an
  unprivileged job) is re-validated here and embedded under ``msi`` (additive key;
  consumers ignore unknown keys).

``validate_manifest`` is the schema check used by the unit tests and by the
publish job right before attesting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

import vpcommon as v

WORKFLOW = ".github/workflows/patched-windows-release.yml"
INSTALLER_DEFAULT = "VibepolloSetup.exe"
MSI_ASSET = "Vibepollo.msi"
PROVENANCE_ASSET = "release-provenance.json"
BUNDLE = "attestation.sigstore.json"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def asset_entry(dist: str, name: str) -> Dict[str, Any]:
    v.require(v.ASSET_NAME_RE, name, "asset name")
    path = os.path.join(dist, name)
    if not os.path.isfile(path) or os.path.islink(path):
        raise v.ValidationError(f"asset missing or not a regular file: {name}")
    return {"name": name, "sha256": sha256_file(path), "size": os.path.getsize(path)}


def write_sums(dist: str, names: List[str]) -> str:
    lines = []
    for name in sorted(set(names)):
        v.require(v.ASSET_NAME_RE, name, "asset name")
        if name in ("SHA256SUMS", BUNDLE):
            raise v.ValidationError("SHA256SUMS must not list itself or the bundle")
        lines.append(f"{sha256_file(os.path.join(dist, name))}  {name}\n")
    text = "".join(lines)
    with open(os.path.join(dist, "SHA256SUMS"), "w", encoding="ascii", newline="\n") as fh:
        fh.write(text)
    return text


def verify_sums(dist: str) -> List[str]:
    """Return a list of problems (empty = every listed file matches)."""
    problems: List[str] = []
    with open(os.path.join(dist, "SHA256SUMS"), "r", encoding="ascii") as fh:
        for ln in fh.read().splitlines():
            m = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]{0,99})", ln)
            if not m:
                problems.append("malformed line")
                continue
            path = os.path.join(dist, m.group(2))
            if not os.path.isfile(path):
                problems.append(f"missing {m.group(2)}")
            elif sha256_file(path) != m.group(1):
                problems.append(f"mismatch {m.group(2)}")
    return problems


def sanitize_msi_summary(raw: Any) -> Optional[Dict[str, Any]]:
    """Re-validate the untrusted summary; return a normalised copy (or None)."""
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        return None
    safe = re.compile(r"[\x20-\x7e]{0,160}")
    out: Dict[str, Any] = {"tool": "msiinfo"}
    for key in ("product_version", "product_name"):
        val = raw.get(key)
        if not isinstance(val, str) or safe.fullmatch(val) is None:
            return None
        out[key] = val
    cnt = raw.get("custom_action_count")
    if not isinstance(cnt, int) or not (0 <= cnt <= 100000):
        return None
    out["custom_action_count"] = cnt
    h = raw.get("custom_action_table_sha256")
    if not isinstance(h, str) or v.SHA256_RE.fullmatch(h) is None:
        return None
    out["custom_action_table_sha256"] = h
    acts = raw.get("custom_actions")
    if not isinstance(acts, list) or len(acts) > 400:
        return None
    norm = []
    for a in acts:
        if not isinstance(a, dict) or not isinstance(a.get("type"), int):
            return None
        for k in ("action", "source", "target"):
            if not isinstance(a.get(k), str) or safe.fullmatch(a[k]) is None:
                return None
        norm.append({"action": a["action"], "type": a["type"], "source": a["source"], "target": a["target"]})
    out["custom_actions"] = norm
    svc = raw.get("services", [])
    if not isinstance(svc, list) or len(svc) > 50 or any(
            not isinstance(s, str) or safe.fullmatch(s) is None for s in svc):
        return None
    out["services"] = svc
    return out


def sanitize_sensitive(block: Any) -> Dict[str, Any]:
    """Validate the ``sensitive`` block of build-meta.json (strict; raises on anything odd)."""
    if not isinstance(block, dict):
        raise v.ValidationError("bad sensitive block")
    since = block.get("since", "")
    if since != "":
        v.upstream_tag(since)
    count = block.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or not (0 <= count <= 1000000):
        raise v.ValidationError("bad sensitive count")
    paths = block.get("paths")
    if not isinstance(paths, list) or len(paths) > 50:
        raise v.ValidationError("bad sensitive paths")
    for p in paths:
        if not isinstance(p, str) or re.fullmatch(r"[ -~]{1,200}", p) is None:
            raise v.ValidationError("bad sensitive path entry")
    enforced = block.get("enforced")
    if not isinstance(enforced, bool):
        raise v.ValidationError("bad sensitive enforced flag")
    return {"since": since, "count": count, "paths": list(paths), "enforced": enforced}


def expected_release_assets(dist: str) -> List[str]:
    """Every file that must be uploaded: manifest.assets + SHA256SUMS + manifest.json + the bundle."""
    with open(os.path.join(dist, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    names = {a["name"] for a in manifest["assets"]}
    names |= {"SHA256SUMS", "manifest.json", BUNDLE}
    for n in names:
        v.require(v.ASSET_NAME_RE, n, "asset name")
    return sorted(names)


def check_asset_set(dist: str) -> List[str]:
    """Fail closed (immutable releases cannot be repaired): the files in ``dist``, the manifest asset list and
    SHA256SUMS must describe exactly the same set.  Returns the sorted upload list."""
    expected = expected_release_assets(dist)
    present = sorted(n for n in os.listdir(dist) if os.path.isfile(os.path.join(dist, n)))
    if present != expected:
        extra = sorted(set(present) - set(expected))
        missing = sorted(set(expected) - set(present))
        raise v.ValidationError(f"dist differs from manifest assets: extra={extra} missing={missing}")
    with open(os.path.join(dist, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    want_sums = sorted({a["name"] for a in manifest["assets"]} | {"manifest.json"})
    with open(os.path.join(dist, "SHA256SUMS"), "r", encoding="ascii") as fh:
        got_sums = sorted(ln.split("  ", 1)[1] for ln in fh.read().splitlines() if ln)
    if got_sums != want_sums:
        raise v.ValidationError("SHA256SUMS does not list exactly the manifest assets plus manifest.json")
    problems = verify_sums(dist)
    if problems:
        raise v.ValidationError("SHA256SUMS verification failed: " + "; ".join(problems[:3]))
    return expected


def validate_manifest(m: Dict[str, Any]) -> None:
    """Strict schema-1 validation (raises ``ValidationError``)."""
    if m.get("schema") != 1 or m.get("product") != "vibepollo":
        raise v.ValidationError("bad schema/product")
    v.release_tag(m["tag"])
    up = m["upstream"]
    v.require(v.OWNER_RE, up["owner"], "upstream owner")
    v.require(v.REPO_RE, up["repo"], "upstream repo")
    v.upstream_tag(up["tag"])
    v.sha1(up["commit"], "upstream commit")
    if not isinstance(up["release_id"], int) or up["release_id"] <= 0:
        raise v.ValidationError("bad release_id")
    if m["tag"] != f"{up['tag']}-vp.{m['build']['n']}":
        raise v.ValidationError("tag does not match upstream tag + build n")
    b = m["build"]
    if not isinstance(b["n"], int) or not (1 <= b["n"] <= v.MAX_BUILD_N):
        raise v.ValidationError("bad build n")
    import version_map
    ver = version_map.compute(up["tag"], b["n"])
    if b["version"] != ver["build_version"] or b["msi_product_version"] != ver["msi_product_version"]:
        raise v.ValidationError("build version fields do not match the version mapper")
    v.require(v.RUN_ID_RE, str(b["run_id"]), "run id")
    if b["workflow"] != WORKFLOW:
        raise v.ValidationError("bad workflow path")
    if not re.fullmatch(r"refs/heads/(build|ci-trial)/[0-9.]+-n[0-9]{1,3}", b["source_ref"]):
        raise v.ValidationError("bad source_ref")
    v.sha1(b["source_sha"], "source sha")
    p = m["patches"]
    v.sha256(p["series_sha256"], "series sha")
    if not isinstance(p["count"], int) or p["count"] < 0:
        raise v.ValidationError("bad patch count")
    if not isinstance(p["groups"], list):
        raise v.ValidationError("bad patch groups")
    v.require(v.ASSET_NAME_RE, m["installer"], "installer name")
    names = [a["name"] for a in m["assets"]]
    if m["installer"] not in names:
        raise v.ValidationError("installer is not listed in assets")
    for a in m["assets"]:
        v.require(v.ASSET_NAME_RE, a["name"], "asset name")
        v.sha256(a["sha256"], "asset sha256")
        if not isinstance(a["size"], int) or a["size"] <= 0:
            raise v.ValidationError("bad asset size")
    if m["attestation"] != [BUNDLE]:
        raise v.ValidationError("bad attestation list")
    v.require(v.ISO_TS_RE, m["created_at"], "created_at")
    emb = m.get("installer_embeds")
    if emb is not None:
        v.sha256(emb["msi_sha256"], "embedded msi sha256")
        if version_map.msi_tuple(emb["msi_product_version"]) != version_map.msi_tuple(
                m["build"]["msi_product_version"]):
            raise v.ValidationError("embedded MSI version differs from build.msi_product_version")
    if "sensitive_paths_touched" in m:
        sanitize_sensitive(m["sensitive_paths_touched"])


def version_text(value: Any) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,5}(\.[0-9]{1,5}){2,3}", value) is None:
        raise v.ValidationError("bad version text in provenance")
    return value


def build_manifest(args: argparse.Namespace) -> Dict[str, Any]:
    dist = args.dist
    names = [args.installer]
    if os.path.isfile(os.path.join(dist, MSI_ASSET)):
        names.append(MSI_ASSET)
    if os.path.isfile(os.path.join(dist, PROVENANCE_ASSET)):
        names.append(PROVENANCE_ASSET)
    manifest: Dict[str, Any] = {
        "schema": 1,
        "product": "vibepollo",
        "tag": args.tag,
        "upstream": {"owner": args.upstream_owner, "repo": args.upstream_repo, "tag": args.upstream_tag,
                     "commit": args.upstream_commit, "release_id": args.release_id},
        "build": {"n": args.n, "version": args.build_version, "msi_product_version": args.msi_product_version,
                  "run_id": int(v.require(v.RUN_ID_RE, str(args.run_id), "run id")), "workflow": WORKFLOW, "source_ref": args.source_ref,
                  "source_sha": args.source_sha},
        "patches": {"series_sha256": args.series_sha, "count": args.patch_count,
                    "groups": [g for g in args.groups.split(",") if g]},
        "installer": args.installer,
        "assets": [asset_entry(dist, n) for n in names],
        "attestation": [BUNDLE],
        "created_at": args.created_at,
    }
    prov_path = os.path.join(dist, PROVENANCE_ASSET)
    if not os.path.isfile(prov_path):
        raise v.ValidationError("release-provenance.json is mandatory")
    with open(prov_path, "r", encoding="utf-8") as fh:
        prov = json.load(fh)
    emb = prov.get("embedded_msi") if isinstance(prov, dict) else None
    if not isinstance(emb, dict) or emb.get("verified") is not True:
        raise v.ValidationError("provenance lacks a verified embedded MSI record")
    manifest["installer_embeds"] = {
        "msi_sha256": v.sha256(emb.get("sha256"), "embedded msi sha256"),
        "msi_product_version": version_text(emb.get("msi_product_version")),
        "setup_file_version": version_text(emb.get("setup_file_version", "")),
        "source": "release-provenance.json (upstream finalize job)",
    }
    if getattr(args, "meta", None):
        with open(args.meta, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        sens = sanitize_sensitive(meta.get("sensitive"))
        manifest["sensitive_paths_touched"] = sens
    if args.msi_summary:
        with open(args.msi_summary, "r", encoding="utf-8") as fh:
            summary = sanitize_msi_summary(json.load(fh))
        if summary is None:
            raise v.ValidationError("msi summary failed validation")
        manifest["msi"] = summary
    validate_manifest(manifest)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--dist", required=True)
    g.add_argument("--installer", default=INSTALLER_DEFAULT)
    g.add_argument("--tag", required=True)
    g.add_argument("--upstream-owner", default="Nonary")
    g.add_argument("--upstream-repo", default="Vibepollo")
    g.add_argument("--upstream-tag", required=True)
    g.add_argument("--upstream-commit", required=True)
    g.add_argument("--release-id", required=True, type=int)
    g.add_argument("--n", required=True, type=int)
    g.add_argument("--build-version", required=True)
    g.add_argument("--msi-product-version", required=True)
    g.add_argument("--run-id", required=True)
    g.add_argument("--source-ref", required=True)
    g.add_argument("--source-sha", required=True)
    g.add_argument("--series-sha", required=True)
    g.add_argument("--patch-count", required=True, type=int)
    g.add_argument("--groups", default="posture")
    g.add_argument("--created-at", required=True)
    g.add_argument("--msi-summary")
    g.add_argument("--meta", help="build-meta.json (sensitive upstream paths)")
    al = sub.add_parser("assets-list")
    al.add_argument("--dist", required=True)
    sl = sub.add_parser("sensitive-line")
    sl.add_argument("--meta", required=True)
    vv = sub.add_parser("verify")
    vv.add_argument("--dist", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "assets-list":
            for name in check_asset_set(args.dist):
                print(name)
            return 0
        if args.cmd == "sensitive-line":
            with open(args.meta, "r", encoding="utf-8") as fh:
                sens = sanitize_sensitive(json.load(fh).get("sensitive"))
            if sens["count"] == 0:
                print("sensitive upstream paths touched: none")
            else:
                since = sens["since"] or "unknown"
                print(f"SENSITIVE UPSTREAM PATHS TOUCHED since {since}: {sens['count']} file(s) "
                      f"(gate enforced: {str(sens['enforced']).lower()}; list in manifest.json)")
            return 0
        if args.cmd == "verify":
            problems = verify_sums(args.dist)
            if problems:
                return v.fail("SHA256SUMS verification failed: " + "; ".join(problems[:5]))
            return 0
        manifest = build_manifest(args)
        with open(os.path.join(args.dist, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")
        write_sums(args.dist, [a["name"] for a in manifest["assets"]] + ["manifest.json"])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return v.fail(f"manifest_gen: {type(exc).__name__}: {str(exc)[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

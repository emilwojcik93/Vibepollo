#!/usr/bin/env python3
"""Version mapper (PLAN 5.5 / ADR-3, research/01 section 2).

Upstream numeric tag ``X.Y.Z`` and rebuild number ``N`` (1..87) map to

    release tag          X.Y.Z-vp.N
    build version        X.Y.(Z+1)-alpha.N        N in   1..29   (ORD = N)
                         X.Y.(Z+1)-beta.(N-29)    N in  30..58   (ORD = 30 + (N-29) = N + 1)
                         X.Y.(Z+1)-rc.(N-58)      N in  59..87   (ORD = 60 + (N-58) = N + 2)
    MSI ProductVersion   X.Y.(100*(Z+1)+ORD).0

which is strictly above upstream ``X.Y.Z`` (=> ``X.Y.(100*Z+99).0``) and
strictly below upstream ``X.Y.(Z+1)`` (=> ``X.Y.(100*(Z+1)+99).0``).

Hard limits of the MSI encoding: X, Y <= 255, Z+1 <= 654.
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, Tuple

import vpcommon as v


def prerelease_for(n: int) -> Tuple[str, int, int]:
    """Return ``(label, label_number, ORD)`` for rebuild ``n``."""
    if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= v.MAX_BUILD_N):
        raise v.ValidationError("build number must be an integer in 1..87")
    if n <= 29:
        return "alpha", n, n
    if n <= 58:
        k = n - 29
        return "beta", k, 30 + k
    k = n - 58
    return "rc", k, 60 + k


def build_version(tag: str, n: int) -> str:
    x, y, z = v.tag_tuple(tag)
    _check_limits(x, y, z)
    label, k, _ = prerelease_for(n)
    return f"{x}.{y}.{z + 1}-{label}.{k}"


def msi_product_version(tag: str, n: int) -> str:
    x, y, z = v.tag_tuple(tag)
    _check_limits(x, y, z)
    _, _, ord_ = prerelease_for(n)
    return f"{x}.{y}.{100 * (z + 1) + ord_}.0"


def upstream_msi_version(tag: str) -> str:
    """ProductVersion upstream itself would produce for the plain tag."""
    x, y, z = v.tag_tuple(tag)
    return f"{x}.{y}.{100 * z + 99}.0"


def _check_limits(x: int, y: int, z: int) -> None:
    if x > v.MAX_MAJOR_MINOR or y > v.MAX_MAJOR_MINOR:
        raise v.ValidationError("major/minor exceed the MSI limit of 255")
    if z + 1 > v.MAX_PATCH_PLUS_ONE:
        raise v.ValidationError("patch+1 exceeds the MSI limit of 654")


def compute(tag: str, n: int) -> Dict[str, str]:
    v.upstream_tag(tag)
    out = {
        "release_tag": f"{tag}-vp.{n}",
        "build_version": build_version(tag, n),
        "msi_product_version": msi_product_version(tag, n),
        "upstream_msi_version": upstream_msi_version(tag),
        "branch": v.branch_name(tag, n),
    }
    v.release_tag(out["release_tag"])
    return out


def msi_tuple(version: str) -> Tuple[int, int, int, int]:
    """Parse an MSI ProductVersion (3 or 4 numeric parts) into a 4-tuple."""
    parts = version.split(".")
    if not (3 <= len(parts) <= 4) or not all(p.isascii() and p.isdigit() for p in parts):
        raise v.ValidationError("not an MSI ProductVersion")
    nums = [int(p) for p in parts] + [0] * (4 - len(parts))
    return tuple(nums)  # type: ignore[return-value]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="upstream tag X.Y.Z")
    ap.add_argument("--n", required=True, type=int, help="rebuild number 1..87")
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = compute(args.tag, args.n)
    except v.ValidationError as exc:
        return v.fail(f"version_map: {exc}")
    if args.github_output:
        v.write_outputs(result)
    else:
        for key, value in result.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

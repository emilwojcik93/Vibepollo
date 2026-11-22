#!/usr/bin/env python3
"""Parse ``patches/series`` and compute the series fingerprint.

``series`` format (maintained by the Patch Engineer): one patch path per line,
relative to the ``patches/`` directory; blank lines and ``#`` comments are
ignored.  An optional ``# group: NAME`` comment assigns the following entries to
a logical group (default group: ``posture``).

``series_sha256`` is the SHA-256 over the canonical text

    <sha256(series file)>  series
    <sha256(patch 1)>  <relative path 1>
    ...

(one line each, ``\\n`` terminated), i.e. it covers the order *and* the content
of every patch: a changed patch body changes the fingerprint, unlike a hash of
the series file alone.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from typing import Dict, List, Tuple

import vpcommon as v

GROUP_RE = re.compile(r"#\s*group:\s*([a-z][a-z0-9-]{0,30})\s*")
DEFAULT_GROUP = "posture"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse(series_path: str) -> List[Tuple[str, str]]:
    """Return ``[(relative_patch_path, group), ...]`` in order."""
    entries: List[Tuple[str, str]] = []
    group = DEFAULT_GROUP
    with open(series_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.rstrip("\r\n").strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                m = GROUP_RE.fullmatch(stripped)
                if m:
                    group = m.group(1)
                continue
            name = stripped.split("#", 1)[0].strip()
            v.require(v.PATCH_NAME_RE, name, "series entry")
            if ".." in name.split("/"):
                raise v.ValidationError("series entry escapes the patches directory")
            entries.append((name, group))
    return entries


def fingerprint(series_path: str, patches_dir: str) -> Dict[str, object]:
    entries = parse(series_path)
    lines = [f"{_sha256_file(series_path)}  series\n"]
    root = os.path.realpath(patches_dir)
    for name, _ in entries:
        full = os.path.join(patches_dir, name)
        if not os.path.realpath(full).startswith(root + os.sep):
            raise v.ValidationError("patch resolves outside the patches directory")
        if not os.path.isfile(full):
            raise v.ValidationError(f"patch listed in series is missing: {name}")
        lines.append(f"{_sha256_file(full)}  {name}\n")
    digest = hashlib.sha256("".join(lines).encode("ascii")).hexdigest()
    groups: List[str] = []
    for _, g in entries:
        if g not in groups:
            groups.append(g)
    return {"series_sha256": digest, "count": len(entries), "groups": groups,
            "entries": [n for n, _ in entries]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--series", default="patches/series")
    ap.add_argument("--patches-dir", default="patches")
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args(argv)
    try:
        info = fingerprint(args.series, args.patches_dir)
    except (v.ValidationError, OSError) as exc:
        return v.fail(f"series: {exc}")
    if args.github_output:
        v.write_outputs({"series_sha256": str(info["series_sha256"]),
                         "patch_count": str(info["count"]),
                         "patch_groups": ",".join(info["groups"])})  # type: ignore[arg-type]
    else:
        print(info["series_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

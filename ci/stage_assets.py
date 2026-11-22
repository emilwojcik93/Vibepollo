#!/usr/bin/env python3
"""Stage the build artifacts into the release ``dist`` directory.

* ``--build-dir``  contents of the ``build-Windows`` artifact: must hold exactly
  ``VibepolloSetup.exe`` and optionally ``release-provenance.json``;
* ``--msi-dir``    contents of ``unsigned-msi-package-Windows``: exactly one
  ``*.msi`` regular file (optional; ``--require-msi`` makes it mandatory);
* the MSI is staged under the contract name ``Vibepollo.msi``.

Files are only hashed / copied, never executed.  Symlinks, unexpected files,
oversized files and secret-looking text in the JSON assets are rejected.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from typing import List

import vpcommon as v

INSTALLER = "VibepolloSetup.exe"
PROVENANCE = "release-provenance.json"
MSI_ASSET = "Vibepollo.msi"
MAX_BYTES = 400 * 1024 * 1024
SECRET_RE = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}")


def _regular_files(directory: str) -> List[str]:
    out = []
    for root, dirs, files in os.walk(directory):
        for name in files + dirs:
            p = os.path.join(root, name)
            if os.path.islink(p):
                raise v.ValidationError("symlink in artifact")
        for name in files:
            out.append(os.path.relpath(os.path.join(root, name), directory).replace("\\", "/"))
    return sorted(out)


def stage(build_dir: str, msi_dir: str, dist: str, require_msi: bool) -> List[str]:
    os.makedirs(dist, exist_ok=True)
    build_files = _regular_files(build_dir)
    allowed = {INSTALLER, PROVENANCE}
    if INSTALLER not in build_files or not set(build_files) <= allowed:
        raise v.ValidationError("build-Windows artifact content is not exactly the expected set")
    staged = []
    for name in build_files:
        src = os.path.join(build_dir, name)
        if os.path.getsize(src) > MAX_BYTES or os.path.getsize(src) == 0:
            raise v.ValidationError(f"unexpected size for {name}")
        if name.endswith(".json"):
            with open(src, "rb") as fh:
                data = fh.read()
            if SECRET_RE.search(data):
                raise v.ValidationError(f"secret-like content in {name}")
            json.loads(data.decode("utf-8"))
        shutil.copyfile(src, os.path.join(dist, name))
        staged.append(name)
    if os.path.isdir(msi_dir):
        msis = [f for f in _regular_files(msi_dir) if f.lower().endswith(".msi")]
        if len(msis) != 1:
            if require_msi or msis:
                raise v.ValidationError("expected exactly one MSI in the MSI artifact")
        else:
            src = os.path.join(msi_dir, msis[0])
            if os.path.getsize(src) > MAX_BYTES or os.path.getsize(src) == 0:
                raise v.ValidationError("unexpected MSI size")
            shutil.copyfile(src, os.path.join(dist, MSI_ASSET))
            staged.append(MSI_ASSET)
    elif require_msi:
        raise v.ValidationError("MSI artifact missing")
    return staged


def find_msi(msi_dir: str) -> str:
    msis = [f for f in _regular_files(msi_dir) if f.lower().endswith(".msi")]
    if len(msis) != 1:
        raise v.ValidationError("expected exactly one MSI")
    return os.path.join(msi_dir, msis[0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir", required=True)
    ap.add_argument("--msi-dir", required=True)
    ap.add_argument("--dist", required=True)
    ap.add_argument("--require-msi", action="store_true")
    ap.add_argument("--print-msi", action="store_true", help="only print the MSI path (package job)")
    args = ap.parse_args(argv)
    try:
        if args.print_msi:
            print(find_msi(args.msi_dir))
            return 0
        staged = stage(args.build_dir, args.msi_dir, args.dist, args.require_msi)
    except (v.ValidationError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return v.fail(f"stage_assets: {type(exc).__name__}: {str(exc)[:200]}")
    print("staged: " + ", ".join(staged))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

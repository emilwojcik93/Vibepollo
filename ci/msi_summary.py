#!/usr/bin/env python3
"""Inspect the built MSI with ``msiinfo`` (package ``msitools``) and enforce the
ProductVersion window (AC-BLD-3, mandatory release gate).

Gate: ``floor < ProductVersion == expected < ceiling`` where

* ``expected`` = ``version_map.msi_product_version(tag, n)``      e.g. 2.20.101.0
* ``floor``    = ProductVersion upstream builds for plain ``X.Y.Z``  e.g. 2.20.99.0
* ``ceiling``  = ProductVersion upstream builds for ``X.Y.(Z+1)``   e.g. 2.20.199.0

A ProductVersion at or below the floor would make Windows Installer treat the
build as a downgrade of the upstream release it patches (live incident
2.20.0-posture.1 -> 2.20.90.0 destroyed an install).

The summary (custom-action table, services) is *untrusted data* derived from an
upstream-built binary: every string is reduced to printable ASCII and truncated;
counts are capped.  It is parsed by an unprivileged job and only embedded in
``manifest.json`` after schema re-validation by the privileged job.

``msiinfo`` is run on an Ubuntu runner, never on the Windows build host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from typing import Any, Dict, List, Tuple

import vpcommon as v
import version_map

MAX_ROWS = 400
MAX_STR = 160
SAFE_RE = re.compile(r"[^\x20-\x7e]")


def clean(s: str, limit: int = MAX_STR) -> str:
    return SAFE_RE.sub("?", s)[:limit]


def parse_idt(text: str) -> Tuple[List[str], List[List[str]]]:
    """Parse ``msiinfo export`` output (IDT): header, types, table line, rows."""
    lines = text.replace("\r\n", "\n").split("\n")
    lines = [ln for ln in lines if ln != ""]
    if len(lines) < 3:
        return [], []
    cols = lines[0].split("\t")
    rows = [ln.split("\t") for ln in lines[3:]]
    return cols, rows


def run_export(msi: str, table: str) -> str:
    proc = subprocess.run(["msiinfo", "export", msi, table], capture_output=True, text=True,
                          errors="replace", timeout=120)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def check_window(actual: str, expected: str, floor: str, ceiling: str) -> None:
    a = version_map.msi_tuple(actual)
    if a != version_map.msi_tuple(expected):
        raise v.ValidationError(f"MSI ProductVersion {clean(actual, 30)} != expected {expected}")
    if not a > version_map.msi_tuple(floor):
        raise v.ValidationError(f"MSI ProductVersion {clean(actual, 30)} is not above upstream {floor}")
    if not a < version_map.msi_tuple(ceiling):
        raise v.ValidationError(f"MSI ProductVersion {clean(actual, 30)} is not below next upstream {ceiling}")


def summarize(tables: Dict[str, str]) -> Dict[str, Any]:
    cols, rows = parse_idt(tables.get("Property", ""))
    props = {r[0]: r[1] for r in rows if len(r) >= 2 and cols[:2] == ["Property", "Value"]}
    ca_cols, ca_rows = parse_idt(tables.get("CustomAction", ""))
    actions = []
    for r in ca_rows[:MAX_ROWS]:
        if len(r) < 4:
            continue
        try:
            atype = int(r[1])
        except ValueError:
            atype = -1
        actions.append({"action": clean(r[0], 80), "type": atype,
                        "source": clean(r[2], 80), "target": clean(r[3])})
    actions.sort(key=lambda a: a["action"])
    canon = json.dumps(actions, sort_keys=True).encode("ascii")
    si_cols, si_rows = parse_idt(tables.get("ServiceInstall", ""))
    services = sorted({clean(r[1], 80) for r in si_rows[:50] if len(r) > 1})
    return {
        "status": "ok",
        "tool": "msiinfo",
        "product_version": clean(props.get("ProductVersion", ""), 30),
        "product_name": clean(props.get("ProductName", ""), 80),
        "custom_action_count": len(ca_rows),
        "custom_action_table_sha256": hashlib.sha256(canon).hexdigest(),
        "custom_actions": actions,
        "services": services,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--msi", required=True)
    ap.add_argument("--expected", required=True)
    ap.add_argument("--floor", required=True)
    ap.add_argument("--ceiling", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        tables = {t: run_export(args.msi, t) for t in ("Property", "CustomAction", "ServiceInstall")}
        if not tables["Property"]:
            return v.fail("msiinfo could not read the MSI Property table")
        summary = summarize(tables)
        check_window(summary["product_version"], args.expected, args.floor, args.ceiling)
    except (v.ValidationError, OSError, subprocess.SubprocessError) as exc:
        return v.fail(f"msi_summary: {exc}")
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"MSI ProductVersion {summary['product_version']} is within ({args.floor}, {args.ceiling}); "
          f"{summary['custom_action_count']} custom actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

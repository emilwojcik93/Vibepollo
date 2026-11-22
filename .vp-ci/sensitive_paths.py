#!/usr/bin/env python3
"""Sensitive-path diff gate (PLAN R-SEC-5).

Given the list of files changed between the previous and the new upstream
release, report whether any touches build/installer/driver/CI surface that
runs as SYSTEM or executes in CI.  The result is ALWAYS reported (run summary, release notes
and ``manifest.json`` key ``sensitive_paths_touched``).  Only when the repository variable
``ENFORCE_SENSITIVE_GATE`` is ``true`` does a non-empty match route the release through the
``release-approval`` environment (required reviewer = Owner); the default is report-only because
the Owner wants fully automatic installs.

Patterns are conservative prefixes / suffixes; extend, never loosen.
"""
from __future__ import annotations

import argparse
import sys
from typing import Iterable, List

import vpcommon as v

PREFIXES = (
    ".github/", ".signpath/", "docs/signpath/",
    "packaging/",                    # WiX, bootstrapper, installer custom actions
    "cmake/",                        # build logic, dependency downloads, pinned artifacts
    "src_assets/windows/",           # install-time scripts, drivers, services
    "third-party/", "third_party/",  # pinned dependencies and submodules
    "scripts/",                      # release / download / signing scripts
    "src/nvhttp", "src/rtsp", "src/confighttp", "src/httpcommon", "src/stream",
    "src/crypto", "src/network", "src/upnp", "src/webrtc",   # network listeners and crypto
)
EXACT = (".gitmodules", "CMakeLists.txt", "package.json", "package-lock.json", "vcpkg.json")
SUFFIXES = ("/package.json", "/package-lock.json", "/CMakeLists.txt", ".cmake", ".wxs", ".wxi", ".wxl",
            ".vbs", ".ps1", ".bat", ".cmd", ".inf", ".cat")


def matches(path: str) -> bool:
    return path in EXACT or path.startswith(PREFIXES) or path.endswith(SUFFIXES)


def sensitive(paths: Iterable[str]) -> List[str]:
    return sorted({p for p in paths if p and matches(p)})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--names", required=True, help="file with one changed path per line ('-' = stdin)")
    ap.add_argument("--github-output", action="store_true")
    ap.add_argument("--summary", help="write the (truncated) matching list here")
    args = ap.parse_args(argv)
    fh = sys.stdin if args.names == "-" else open(args.names, "r", encoding="utf-8", errors="replace")
    with fh:
        hits = sensitive(line.rstrip("\r\n") for line in fh)
    gate = "approval" if hits else "auto"
    if args.summary:
        with open(args.summary, "w", encoding="utf-8", newline="\n") as out:
            for p in hits[:200]:
                safe = "".join(c if 32 <= ord(c) < 127 else "?" for c in p)[:200]
                out.write(f"- `{safe.replace('`', '')}`\n")
            if len(hits) > 200:
                out.write(f"- ... and {len(hits) - 200} more\n")
    if args.github_output:
        v.write_outputs({"gate": gate, "sensitive_count": str(len(hits))})
    else:
        print(f"gate={gate} count={len(hits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

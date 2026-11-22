"""Shared validation helpers for the Vibepollo patch-pipeline automation.

Everything that reaches a shell, a git ref, a file name or a GitHub output in
the workflows is validated here first.  All patterns are matched with
``re.fullmatch`` (so a trailing newline cannot slip through ``$``) and use
explicit ASCII classes (so unicode digits are rejected).

Stdlib only; runs on any python3 >= 3.8.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Dict, Optional, Tuple

# --- Patterns (CONTRACT section 1) -------------------------------------------------
UPSTREAM_TAG_RE = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
RELEASE_TAG_RE = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}-vp\.[0-9]{1,3}")
BUILD_BRANCH_RE = re.compile(r"build/([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})-n([0-9]{1,3})")
SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
REPO_SLUG_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
RUN_ID_RE = re.compile(r"[0-9]{1,20}")
ASSET_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
PATCH_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*\.patch")
ISO_TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

# Single-line, printable, shell-inert text allowed in GitHub step outputs.
OUTPUT_VALUE_RE = re.compile(r"[A-Za-z0-9._/@:+=, -]{0,300}")

# Limits that come from the MSI ProductVersion encoding (research/01 section 2).
MAX_MAJOR_MINOR = 255
MAX_PATCH_PLUS_ONE = 654
MAX_BUILD_N = 87


class ValidationError(ValueError):
    """Raised when an untrusted string does not match its expected shape."""


def require(pattern: "re.Pattern[str]", value: object, what: str) -> str:
    """Return ``value`` if it fully matches ``pattern``; raise otherwise.

    The offending value is never echoed back (it may be attacker controlled
    and end up in logs / annotations / workflow commands); only its length is
    reported.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{what}: not a string")
    if pattern.fullmatch(value) is None:
        raise ValidationError(f"{what}: invalid value (len={len(value)})")
    return value


def upstream_tag(value: object) -> str:
    return require(UPSTREAM_TAG_RE, value, "upstream tag")


def release_tag(value: object) -> str:
    return require(RELEASE_TAG_RE, value, "release tag")


def sha1(value: object, what: str = "commit sha") -> str:
    return require(SHA1_RE, value, what)


def sha256(value: object, what: str = "sha256") -> str:
    return require(SHA256_RE, value, what)


def tag_tuple(tag: str) -> Tuple[int, int, int]:
    """``'2.20.0'`` -> ``(2, 20, 0)`` (validated)."""
    upstream_tag(tag)
    x, y, z = tag.split(".")
    return int(x), int(y), int(z)


def release_tag_key(tag: str) -> Tuple[int, int, int, int]:
    """Ordering key from CONTRACT section 1.

    ``X.Y.Z-vp.N`` -> ``(X, Y, Z, N)``; a plain upstream marker ``X.Y.Z`` ->
    ``(X, Y, Z, 0)``.
    """
    if RELEASE_TAG_RE.fullmatch(tag):
        base, n = tag.split("-vp.")
        return tag_tuple(base) + (int(n),)
    return tag_tuple(tag) + (0,)


def branch_name(tag: str, n: int) -> str:
    upstream_tag(tag)
    if not (1 <= n <= MAX_BUILD_N):
        raise ValidationError("build number out of range 1..87")
    return f"build/{tag}-n{n}"


def parse_branch(name: str) -> Optional[Tuple[str, int]]:
    m = BUILD_BRANCH_RE.fullmatch(name)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def safe_output_value(value: str) -> str:
    if OUTPUT_VALUE_RE.fullmatch(value) is None or "\n" in value or "\r" in value:
        raise ValidationError("refusing to write unsafe value to GITHUB_OUTPUT")
    return value


def write_outputs(values: Dict[str, str], path: Optional[str] = None) -> None:
    """Append ``key=value`` lines to ``$GITHUB_OUTPUT`` (or ``path``).

    Keys and values are validated (single line, conservative charset), so no
    output injection is possible even if a caller forgets to validate.
    """
    target = path or os.environ.get("GITHUB_OUTPUT")
    lines = []
    for key, value in values.items():
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None:
            raise ValidationError("invalid output key")
        lines.append(f"{key}={safe_output_value(str(value))}\n")
    if target:
        with open(target, "a", encoding="utf-8", newline="\n") as fh:
            fh.writelines(lines)
    else:
        sys.stdout.writelines(lines)


def fail(message: str, code: int = 1) -> "int":
    """Print a workflow error annotation (message must be developer text)."""
    safe = "".join(c if 32 <= ord(c) < 127 else "?" for c in message)[:500]
    print(f"::error::{safe}", file=sys.stderr)
    return code

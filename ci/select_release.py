#!/usr/bin/env python3
"""Pick the upstream release to build from the GitHub releases API JSON.

Input is the (untrusted) JSON array returned by
``GET /repos/<owner>/<repo>/releases?per_page=20``.

Rules (PLAN R-TRK-1/2/4):
* ``draft`` must be false and ``published_at`` non-null;
* ``tag_name`` must fully match ``X.Y.Z`` (numeric) -- anything else, including
  ``-daily``/``-nightly`` legacy tags, shell metacharacters, newlines, ``../``
  or unicode digits, is silently skipped (never echoed);
* the repo owner must be on the allow-list;
* newest = highest numeric tuple; ``target_commitish`` is ignored (the commit
  is resolved by ``deref_commit.py``).

CLI:
  select_release.py --releases FILE --repo Nonary/Vibepollo --allow Nonary \
      [--tag X.Y.Z] [--top N] [--github-output]
Prints a JSON document ``{"selected": {...}|null, "candidates": [...]}``.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Iterable, List, Optional

import vpcommon as v


def numeric_candidates(releases: Any) -> List[Dict[str, Any]]:
    if not isinstance(releases, list):
        raise v.ValidationError("releases payload is not a JSON array")
    out: Dict[str, Dict[str, Any]] = {}
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        if rel.get("draft") is not False:
            continue
        if not rel.get("published_at"):
            continue
        tag = rel.get("tag_name")
        if not isinstance(tag, str) or v.UPSTREAM_TAG_RE.fullmatch(tag) is None:
            continue
        x, y, z = v.tag_tuple(tag)
        if x > v.MAX_MAJOR_MINOR or y > v.MAX_MAJOR_MINOR or z + 1 > v.MAX_PATCH_PLUS_ONE:
            continue  # cannot be mapped to an MSI ProductVersion: never let it wedge the selection
        rid = rel.get("id")
        if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
            continue
        published = rel.get("published_at")
        if not isinstance(published, str) or v.ISO_TS_RE.fullmatch(published) is None:
            continue
        if tag in out:
            # Duplicate tag in one page: keep the first (API order) -- ambiguity is
            # harmless because the lock compares the commit SHA anyway.
            continue
        out[tag] = {
            "tag": tag,
            "release_id": rid,
            "published_at": published,
            "prerelease": bool(rel.get("prerelease")),
            "tuple": list(v.tag_tuple(tag)),
        }
    return sorted(out.values(), key=lambda r: tuple(r["tuple"]), reverse=True)


def check_owner(slug: str, allow: Iterable[str]) -> None:
    v.require(v.REPO_SLUG_RE, slug, "upstream repo")
    owner = slug.split("/", 1)[0]
    allowed = list(allow)
    for item in allowed:
        v.require(v.OWNER_RE, item, "allow-listed owner")
    if owner not in allowed:
        raise v.ValidationError("upstream owner is not on the allow-list")


def select(releases: Any, *, slug: str, allow: Iterable[str], tag: Optional[str] = None,
           top: int = 1) -> Dict[str, Any]:
    check_owner(slug, allow)
    cands = numeric_candidates(releases)
    if tag is not None:
        v.upstream_tag(tag)
        picked = [c for c in cands if c["tag"] == tag]
        return {"selected": picked[0] if picked else None, "candidates": picked}
    top = max(1, min(int(top), 20))
    return {"selected": cands[0] if cands else None, "candidates": cands[:top]}


def previous_of(cands: List[Dict[str, Any]], tag: str) -> Optional[str]:
    """Highest candidate tag strictly lower than ``tag`` (for the sensitive-path diff)."""
    t = v.tag_tuple(tag)
    lower = [c["tag"] for c in cands if tuple(c["tuple"]) < t]
    return lower[0] if lower else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--releases", required=True)
    ap.add_argument("--repo", required=True, help="upstream owner/repo")
    ap.add_argument("--allow", action="append", required=True,
                    help="allowed upstream owner(s); repeatable and/or comma separated")
    ap.add_argument("--tag")
    ap.add_argument("--top", type=int, default=1)
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args(argv)
    try:
        with open(args.releases, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        allow = [a for item in args.allow for a in item.split(",") if a]
        result = select(data, slug=args.repo, allow=allow, tag=args.tag, top=args.top)
        sel_tag = result["selected"]["tag"] if result["selected"] else None
        all_cands = numeric_candidates(data)
        prev = previous_of(all_cands, sel_tag) if sel_tag else None
    except (v.ValidationError, OSError, json.JSONDecodeError) as exc:
        return v.fail(f"select_release: {exc}")
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if args.github_output:
        sel = result["selected"]
        v.write_outputs({
            "found": "true" if sel else "false",
            "tag": sel["tag"] if sel else "",
            "release_id": str(sel["release_id"]) if sel else "",
            "published_at": sel["published_at"] if sel else "",
            "prev_tag": prev or "",
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

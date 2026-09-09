"""Who still names this element — read-only, and the reason `delete` can refuse.

A slug is a reference other files make: a shot list's ``elements`` array, a creator brief's
b-roll or invariant decisions. Deleting an element those files still point at turns a resolvable
reference into a silent nothing at render time, which is the failure the cross-shot-reference
rule exists to catch elsewhere. So deletion asks here first.

Deliberately a text scan over the tenant's own tree rather than an index: an index is a second
source of truth that goes stale, and the tree is small.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..paths import _safe_segment, resolve_content_root

__all__ = ["references_to"]


def _slug_in(value: object, slug: str) -> bool:
    if isinstance(value, str):
        return value == slug
    if isinstance(value, list):
        return any(_slug_in(v, slug) for v in value)
    if isinstance(value, dict):
        return any(_slug_in(v, slug) for v in value.values())
    return False


def references_to(profile: str, slug: str, *, content_root: Path | None = None) -> list[str]:
    """Repo-relative-ish paths of files naming ``slug``, or ``[]``.

    Scans the two places a slug is written: shot lists (``scripts/*.shots.json``, the ``elements``
    array on a shot) and creator briefs (``video/*/brief.json``, decisions 6 and 7).
    """
    root = content_root if content_root is not None else resolve_content_root()
    base = root / _safe_segment(profile, "profile")
    hits: list[str] = []

    for path in sorted(base.glob("scripts/*.shots.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for shot in doc.get("shots", []) if isinstance(doc, dict) else []:
            if isinstance(shot, dict) and _slug_in(shot.get("elements"), slug):
                hits.append(str(path))
                break

    for path in sorted(base.glob("video/*/brief.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        decisions = doc.get("decisions", {}) if isinstance(doc, dict) else {}
        if any(
            _slug_in((decisions.get(k) or {}).get("value"), slug)
            for k in ("broll_list", "invariant")
            if isinstance(decisions.get(k), dict)
        ):
            hits.append(str(path))

    return hits

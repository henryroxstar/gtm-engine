"""Run-artifact registry (A11) — tree-diff attribution + safe serving helpers.

Attribution model: a run's artifacts are the files that appeared or changed under the profile's
content tree while THAT run held the profile lock. Soundness rests on the runner's existing lock
discipline — same-profile runs serialize on `profile_lock`, different profiles touch disjoint
`content/<profile>/` trees — so a cheap `(rel_path, mtime_ns, size)` snapshot-and-diff is
deterministic. Only changed candidates are sha256-hashed.

POINTER semantics: a registered artifact records (path, sha, size) at
registration time; download serves the file's CURRENT bytes. The recorded sha is
provenance, not a download-time integrity check.

Serving rules (§2.3, enforced here + in the router): resolution is by opaque id
only, the resolved real path must stay inside the workspace content root
(defense in depth vs symlinks/tampered rows), every response is
Content-Disposition: attachment with nosniff, media type comes from a server-side
extension allowlist — agent-produced HTML is never rendered inline in a browser
origin.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# Ledger/control files are never artifacts (normative exclusion list, PRD §2.1):
# ledgers are served by /v1/ledger/*; runs/ holds the runner's own manifests.
_EXCLUDED_TOP_LEVEL = frozenset({"history.jsonl", "costs.jsonl", "settings.json"})
_EXCLUDED_DIRS = frozenset({"runs"})
_EXCLUDED_SUFFIXES = (".tmp", ".partial", ".lock")

# Extension → media type allowlist. Anything else serves as octet-stream; the
# attachment-always rule makes even text/html safe to include here (named, never
# rendered inline).
MEDIA_TYPES: dict[str, str] = {
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html",
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
FALLBACK_MEDIA_TYPE = "application/octet-stream"


def media_type_for(name: str) -> str:
    return MEDIA_TYPES.get(Path(name).suffix.lower(), FALLBACK_MEDIA_TYPE)


def _excluded(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return True
    if parts[0] in _EXCLUDED_DIRS:
        return True
    if len(parts) == 1 and parts[0] in _EXCLUDED_TOP_LEVEL:
        return True
    if any(p.startswith(".") for p in parts):
        return True
    return rel.name.lower().endswith(_EXCLUDED_SUFFIXES)


def snapshot_tree(profile_root: Path) -> dict[str, tuple[int, int]]:
    """Cheap scan: {rel_path: (mtime_ns, size)} for every non-excluded file.

    No hashing here — candidates are hashed only when the diff marks them changed.
    """
    out: dict[str, tuple[int, int]] = {}
    if not profile_root.is_dir():
        return out
    for path in profile_root.rglob("*"):
        try:
            if not path.is_file():
                continue
            rel = path.relative_to(profile_root)
            if _excluded(rel):
                continue
            st = path.stat()
            out[str(rel)] = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue  # a vanished/unreadable file is not an artifact
    return out


@dataclass(frozen=True)
class FoundArtifact:
    rel_path: str  # relative to the WORKSPACE CONTENT ROOT (content/<profile>/…)
    name: str
    size_bytes: int
    sha256: str
    media_type: str


def diff_new_artifacts(
    profile_root: Path,
    profile: str,
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> list[FoundArtifact]:
    """Files created or changed between two snapshots, hashed and typed.

    ``rel_path`` is prefixed with the profile segment so it resolves against the
    workspace content root (the serving side's base), matching the GDPR export's
    path vocabulary.
    """
    found: list[FoundArtifact] = []
    for rel, sig in sorted(after.items()):
        if before.get(rel) == sig:
            continue
        path = profile_root / rel
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
        except OSError:
            continue
        found.append(
            FoundArtifact(
                rel_path=str(Path(profile) / rel),
                name=Path(rel).name,
                size_bytes=size,
                sha256=digest,
                media_type=media_type_for(rel),
            )
        )
    return found


def resolve_contained(content_root: Path, rel_path: str) -> Path | None:
    """Resolve ``rel_path`` under ``content_root`` and verify containment.

    Returns the real path only when it exists AND resolves strictly inside the
    resolved content root — a symlink escape or tampered row yields ``None``
    (the router serves 404, never the file). Defense in depth behind the
    opaque-id-only API surface.
    """
    try:
        base = content_root.resolve(strict=True)
    except OSError:
        return None
    candidate = (content_root / rel_path).resolve()
    if candidate == base or base not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None

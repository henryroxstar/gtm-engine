"""Generated knowledge index (E-3, PRD §5) — the catalog of a profile's ``knowledge/``
corpus, with topic tags + freshness, so a pack's readiness check (``agent.readiness``)
and an operator can see what exists beyond the canonical topic files.

Pure and read-only: this module only *computes* the index from what's on disk. It is
never hand-edited and never itself the source of truth — ``knowledge/*.md`` is. Rebuilt
by simply calling :func:`build_knowledge_index` again; there is no cache to invalidate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

#: Canonical topic files sampled from real profiles (PRD §1.5) — the corpus's "table of
#: contents". Anything else is the messy tail, still indexed, just not "canonical".
CANONICAL_TOPICS = frozenset({"company", "product", "icp-personas", "voice", "case-studies"})

# Non-topic files that live alongside the corpus but aren't a knowledge topic themselves.
_EXCLUDED_FILENAMES = frozenset({"REFRESH.md"})


@dataclass(frozen=True)
class KnowledgeIndexEntry:
    topic: str  # filename stem — the retrieval key resolve_knowledge_file() also uses
    relpath: str  # path relative to the knowledge/ dir (subfolder files included)
    canonical: bool
    age_days: float


def build_knowledge_index(
    knowledge_dir: Path, *, now: float | None = None
) -> tuple[KnowledgeIndexEntry, ...]:
    """Scan ``knowledge_dir`` and return one entry per ``*.md`` file, sorted by topic.

    Every file is indexed, canonical or not — the "messy tail" (e.g. ``hook-matrix.md``,
    ``brand-notes.md``) is exactly as reachable as ``company.md``, just tagged
    ``canonical=False``. Returns ``()`` if the directory doesn't exist (no knowledge yet
    is a valid, non-error state). ``now`` is injectable (epoch seconds) for deterministic
    freshness tests.
    """
    now = time.time() if now is None else now
    if not knowledge_dir.is_dir():
        return ()

    entries = []
    for path in sorted(knowledge_dir.rglob("*.md")):
        if path.name in _EXCLUDED_FILENAMES:
            continue
        topic = path.stem
        age_days = (now - path.stat().st_mtime) / 86400
        entries.append(
            KnowledgeIndexEntry(
                topic=topic,
                relpath=str(path.relative_to(knowledge_dir)),
                canonical=topic in CANONICAL_TOPICS,
                age_days=age_days,
            )
        )
    return tuple(sorted(entries, key=lambda e: e.topic))


def find_in_index(index: tuple[KnowledgeIndexEntry, ...], topic: str) -> KnowledgeIndexEntry | None:
    """Look up ``topic`` in a built index — the retrieval step for a topic gtm-engine
    doesn't already know the canonical filename for."""
    for entry in index:
        if entry.topic == topic:
            return entry
    return None

"""Which sources each section of the status page is built from — and the proof that it is.

A mapping is a claim, so this one ships with its oracle:
``tests/contracts/test_dashboard_section_sources.py`` deletes each source's files in a copy of
a seeded tree, re-renders, and asserts every section that changed is in that source's mapped
set. The direction is "changed ⊆ mapped", so a map that over-claims passes and one that
under-claims fails — which is the safe way round for "can I trust this section?".

**Where it can still be wrong.** The oracle runs on one seeded fixture, so a section that
renders nothing there is mapped by reading the code and pinned in :data:`UNWITNESSED`; the
test fails if one of those starts rendering, which is the cue to witness it properly. The
dossier lookup (``config.NAME_GLOBS``) is a name-only read with no rendered consumer, so it
maps to no section and the test pins that too.
"""

from __future__ import annotations

from .config import OPS_GROUPS
from .provenance import SOURCES

#: Block / tab-level section id -> the SOURCES keys whose files change what it shows.
_BLOCKS: dict[str, frozenset[str]] = {
    k: frozenset(v)
    for k, v in {
        "account-table": ("plans", "pool", "sorted-list"),
        "account-tiles": ("plans", "pool"),
        "accounts-funnel": ("ledger", "sorted-list"),
        "actions-required": ("figures",),
        "angle-heatmap": ("pool",),
        "benchmarks": ("benchmarks", "figures", "plans"),
        "campaign-lines": ("figures", "plans"),
        "campaign-results": ("figures", "plans", "pool"),
        "can-answer": ("pool",),
        "capability-spread": ("copy", "pool"),
        "ceiling-tile": ("plans", "pool"),
        "checks-detail": ("checks", "pool"),
        "compliance": ("ledger",),
        "contacts-by-status": ("plans", "sorted-list"),
        "cross-check": ("sorted-list",),
        "email-portfolio": ("copy", "pool"),
        "email-table": ("checks", "copy", "figures", "pool"),
        "experiment-notes": ("copy", "plans"),
        "filter": ("plans", "pool"),
        "finding-new-people": ("ledger",),
        "forecast": ("figures", "plans"),
        "grid": ("pool",),
        "hand-sent": ("copy", "pool"),
        "holding-up": ("checks", "copy", "plans"),
        "inbound-health": ("ledger",),
        "judge-notes": ("checks", "review", "sorted-list"),
        "learnings": ("figures", "knowledge", "ledger", "plans", "pool"),
        "lede": ("figures", "ledger", "sorted-list"),
        "list-vs-provider": ("figures", "plans", "pool"),
        "load-files": ("figures",),
        "maintenance-lines": ("review",),
        "needs-address": ("plans", "pool", "sorted-list"),
        "nothing-outstanding": ("checks", "pool"),
        "opening-lines": ("pool",),
        "packs-list": ("copy", "pool"),
        "pool": ("knowledge", "plans", "pool"),
        "re-push": ("checks", "pool"),
        "readable-difference": ("figures", "pool"),
        "ready-to-send": ("ledger", "pool", "sorted-list"),
        "reconciliation": ("figures", "plans"),
        "results-figures": ("figures", "plans", "pool"),
        "roster-notes": ("plans", "pool"),
        "section-kinds": (),
        "segment-mix": ("plans", "pool"),
        "sent": ("figures", "plans"),
        "sentiment-triage": ("outcomes",),
        "sequence-table": ("figures", "plans"),
        "setup-tiles": ("figures", "plans", "pool"),
        "shared-sequences": ("figures", "plans"),
        "small-numbers": ("checks", "copy", "pool"),
        "sources-table": tuple(SOURCES),
        "subjects": ("copy", "pool"),
        "unlinked": ("figures", "plans"),
        "varies": ("copy", "pool"),
        "voice-of-market": ("outcomes",),
        "when-we-know": ("figures",),
    }.items()
}

#: Sections that render nothing on the test fixture, so the oracle cannot witness them. Their
#: sets above come from reading what each builder reads; the test pins this list exactly.
UNWITNESSED = frozenset(
    {
        "capability-spread",
        "experiment-notes",
        "forecast",
        "hand-sent",
        "holding-up",
        "judge-notes",
        "list-vs-provider",
        "packs-list",
        "ready-to-send",
        "shared-sequences",
        "unlinked",
    }
)


def _derived() -> dict[str, frozenset[str]]:
    groups: dict[str, frozenset[str]] = {}
    for gid, _title, blocks in OPS_GROUPS:
        groups[gid] = frozenset().union(*(_BLOCKS[b] for b in blocks))
    return groups


#: Every id in ``config.SECTIONS`` -> its source keys. A group is the union of its blocks
#: because it is a container for them — derived so the two cannot disagree.
SECTION_SOURCES: dict[str, frozenset[str]] = {**_BLOCKS, **_derived()}


def sections_for(source: str) -> frozenset[str]:
    """The sections a source feeds — the forward direction of :data:`SECTION_SOURCES`."""
    return frozenset(s for s, srcs in SECTION_SOURCES.items() if source in srcs)


def source_for_glob(glob: str, roster_globs: tuple[str, ...] = ()) -> str | None:
    """The SOURCES key a tracked input glob belongs to, or ``None`` if it has none.

    A campaign manifest's ``roster_globs`` are relative to ``prospects/`` and belong to the
    ``pool`` source whatever their basename — that is what makes a non-conventional roster
    export count toward the pool row. ``None`` is an answer: the dossier lookup is tracked
    by name and feeds no section.
    """
    for key, src in SOURCES.items():
        if glob in src["globs"]:
            return key
    if glob in {f"prospects/{g}" for g in roster_globs}:
        return "pool"
    return None

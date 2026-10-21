"""Which knowledge topics need a refresh — by CLOCK or by SOURCE (knowledge-lifecycle PRD).

The deterministic *selection* half of automated refresh. Two questions, deliberately separate:

``due``
    "Has enough time passed?" Reads the Phase-1 freshness metadata and returns topics whose
    ``review`` cadence has come due. Time-driven; a scheduler runs it on a cadence.

``impact``  (Phase 6, 2026-09-01)
    "A new source just landed — what does it touch?" Time says nothing here, so ``due`` cannot
    answer it and never could. Added because the gap was costing real work: a tenant's REFRESH.md
    had listed "a new product deck supersedes MAY26" and "product names change" as refresh triggers
    for months, both had fired, and nothing selected on either — the stale topics were found by
    hand, and one of them (a matrix on ``review: evergreen``) could not have surfaced in ``due``
    at all. Operators had twice tried to express the missing axis as a cadence value
    (``review: on next deck revision``, ``review: superseded-on-reshoot``), which the schema
    correctly refuses.

    Two modes, because provenance is recorded two ways:
      * ``--source <url|string>`` INVERTS the existing ``source:`` field. Free — the fan-out is
        already in the corpus (one doc-site URL is shared by 17 topics in one live profile).
      * ``--source-kind <kind>`` matches the ``triggers:`` field, for events a ``source:`` string
        cannot express ("a new sales deck", "pricing changed").

    ``impact`` deliberately INCLUDES ``evergreen`` topics, which ``due`` excludes. Evergreen means
    "no clock", not "never review" — and the topics carrying it (voice, brand notes, the hook
    matrix) are exactly the ones a new deck or a rename invalidates.

Both are pure, testable, no LLM, no network, and reusable across every profile — they take
``profiles_root`` + ``profile``, never a global.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from . import knowledge_meta as km
from .paths import PathConfig

#: Statuses that mean "refresh now". ``due-soon`` is included so a scheduled run gets ahead of the
#: cliff rather than waiting until a topic is already overdue.
DUE_STATUSES = frozenset({"overdue", "due-soon"})


def due_topics(
    profiles_root: Path,
    profile: str,
    today: date,
    *,
    include_unknown: bool = False,
) -> list[tuple[km.KnowledgeMeta, str]]:
    """(meta, status) for each managed topic in ``profile`` that is due for refresh.

    ``include_unknown`` also returns topics with no/invalid metadata — useful for a first pass over
    a profile that predates the lifecycle layer, but off by default so scheduled runs stay quiet."""
    out = []
    for meta, status in km.profile_meta(profiles_root, profile, today):
        if status in DUE_STATUSES or (include_unknown and status == "unknown"):
            out.append((meta, status))
    return out


def impact_topics(
    profiles_root: Path,
    profile: str,
    today: date,
    *,
    source: str | None = None,
    source_kind: str | None = None,
) -> list[tuple[km.KnowledgeMeta, str]]:
    """(meta, status) for each managed topic in ``profile`` a given source change touches.

    Exactly one of ``source`` / ``source_kind`` is used; passing neither returns nothing rather
    than everything, because "impacted by nothing" is the honest answer to an empty question.

    ``source`` matches case-insensitively as a SUBSTRING of the topic's ``source:`` field, so a
    doc-site origin (``https://docs.example.com/products/thing/``) selects every topic condensed
    from it without anyone maintaining a list. ``source_kind`` matches an exact entry in the
    topic's ``triggers:``.

    Unlike ``due_topics`` this does not filter on status — a topic coupled to a changed source is
    impacted whether or not its clock has run out, and ``evergreen`` topics are included for the
    same reason.
    """
    if not source and not source_kind:
        return []
    wanted = (source or "").strip().lower()
    out = []
    for meta, status in km.profile_meta(profiles_root, profile, today):
        if source_kind and source_kind in meta.triggers:
            out.append((meta, status))
        elif wanted and meta.source and wanted in meta.source.lower():
            out.append((meta, status))
    return out


def _all_profiles(profiles_root: Path) -> list[str]:
    if not profiles_root.is_dir():
        return []
    return sorted(
        c.name for c in profiles_root.iterdir() if c.is_dir() and (c / "PROFILE.md").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.knowledge_refresh",
        description=(
            "Which knowledge topics need a refresh: `due` (the clock) or "
            "`impact` (a source just changed). Drives the refresh skill/scheduler."
        ),
    )
    parser.add_argument("command", choices=("due", "impact"))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--include-unknown", action="store_true")
    parser.add_argument(
        "--source",
        default=None,
        help="impact: substring of a topic's `source:` (e.g. a doc-site URL that many share)",
    )
    parser.add_argument(
        "--source-kind",
        default=None,
        choices=sorted(km.TRIGGER_KINDS),
        help="impact: an event kind matched against a topic's `triggers:`",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profiles-root", default=None)
    args = parser.parse_args(argv)

    if args.command == "impact" and not (args.source or args.source_kind):
        raise SystemExit(
            "[knowledge-refresh] impact needs --source <url|string> or --source-kind <kind>"
        )
    if args.command == "due" and (args.source or args.source_kind):
        raise SystemExit("[knowledge-refresh] --source/--source-kind apply to `impact`, not `due`")

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else PathConfig.from_env().profiles_root
    )
    if args.all:
        profiles = _all_profiles(profiles_root)
    elif args.profile:
        profiles = [args.profile]
    else:
        raise SystemExit("[knowledge-refresh] pass --profile <slug> or --all")

    today = date.today()
    payload = []
    for profile in profiles:
        if args.command == "impact":
            rows = impact_topics(
                profiles_root, profile, today, source=args.source, source_kind=args.source_kind
            )
        else:
            rows = due_topics(profiles_root, profile, today, include_unknown=args.include_unknown)
        payload.append((profile, rows))

    key = "impacted" if args.command == "impact" else "due"
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "profile": profile,
                        key: [
                            {
                                "topic": m.relpath,
                                "status": status,
                                "refreshed": m.refreshed.isoformat() if m.refreshed else None,
                                "review": m.review,
                                "source": m.source,
                                "reflects": m.reflects,
                                "triggers": list(m.triggers),
                            }
                            for m, status in rows
                        ],
                    }
                    for profile, rows in payload
                ],
                indent=2,
            )
        )
        return 0

    for profile, rows in payload:
        if args.command == "impact":
            what = args.source_kind or args.source
            print(f"\nprofile: {profile} — {len(rows)} topic(s) impacted by {what!r}")
            # `reflects` is the whole point of the report: it says which topics already carry this
            # source's vintage and so need nothing, versus which are genuinely behind.
            for m, status in rows:
                print(f"  {status:<9} {m.relpath}  (reflects: {m.reflects or '—'})")
        else:
            print(f"\nprofile: {profile} — {len(rows)} topic(s) due")
            for m, status in rows:
                print(f"  {status:<9} {m.relpath}  (source: {m.source or '—'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

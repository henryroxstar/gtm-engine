"""Which knowledge topics are due for a refresh (knowledge-lifecycle PRD, Phase 3a).

The deterministic *selection* half of automated refresh: reads the Phase-1 freshness metadata and
returns the topics whose ``review`` cadence has come due. The refresh skill (Phase 3b) asks this
"what's due?" for the active profile, fetches fresh material per each topic's ``source:``, and
stages a candidate; a scheduler runs it on a cadence. Keeping selection here (pure, testable, no
LLM, no network) means the skill only has to do the fetch + re-condense.

Reusable across every profile — takes ``profiles_root`` + ``profile``, never a global.
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


def _all_profiles(profiles_root: Path) -> list[str]:
    if not profiles_root.is_dir():
        return []
    return sorted(
        c.name for c in profiles_root.iterdir() if c.is_dir() and (c / "PROFILE.md").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.knowledge_refresh",
        description="List knowledge topics due for refresh (drives the refresh skill/scheduler).",
    )
    parser.add_argument("command", choices=("due",))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--include-unknown", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profiles-root", default=None)
    args = parser.parse_args(argv)

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
        rows = due_topics(profiles_root, profile, today, include_unknown=args.include_unknown)
        payload.append((profile, rows))

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "profile": profile,
                        "due": [
                            {
                                "topic": m.relpath,
                                "status": status,
                                "refreshed": m.refreshed.isoformat() if m.refreshed else None,
                                "review": m.review,
                                "source": m.source,
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
        print(f"\nprofile: {profile} — {len(rows)} topic(s) due")
        for m, status in rows:
            src = m.source or "—"
            print(f"  {status:<9} {m.relpath}  (source: {src})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

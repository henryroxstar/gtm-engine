"""Unified knowledge-corpus status — the shared, team-visible view (PRD Phase 5).

Ties the four prior tiers into one dashboard so a team collaborating over git on the shared
``profiles/`` corpus can see, per profile: every managed topic with its **owner** (Phase 1
collaboration primitive), **freshness** status (Phase 1), **consuming skills** (Phase 2), and
whether a refresh candidate is **staged** awaiting promotion (Phase 3) — plus summary counts and the
actionable lists (what's due, unowned, orphaned, or awaiting promotion). No DB: it reads the
files each user already has after a ``git pull``; ``owner:`` + ``refreshed:`` + git history are the
async-collaboration primitives.

Reusable across every profile — takes ``profiles_root`` + ``content_root`` + ``profile``, never a
global.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from . import knowledge_meta as km
from . import knowledge_staging as ks
from . import knowledge_usage as ku
from .paths import resolve_content_root, resolve_profiles_root

_ACTIONABLE = ("overdue", "due-soon")


def _consumers(topic: str, topic_skills: dict[str, list[str]]) -> list[str]:
    """Skills that read ``topic`` — directly, or via a directory reference to its parent dir."""
    out = set(topic_skills.get(topic, []))
    if "/" in topic:
        out |= set(topic_skills.get(topic.split("/", 1)[0] + ku._DIR_SUFFIX, []))
    return sorted(out)


def status(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    today: date,
    *,
    skills_root: Path | None = None,
) -> dict:
    """One dashboard dict for ``profile``: per-topic rows + summary counts + actionable lists."""
    topic_skills = ku.topic_to_skills(ku.scan_skills(skills_root or ku.default_skills_root()))
    staged = set(ks.list_staged(content_root, profile))

    rows = []
    for meta, freshness in km.profile_meta(profiles_root, profile, today):
        topic = meta.relpath[:-3] if meta.relpath.endswith(".md") else meta.relpath
        consumers = _consumers(topic, topic_skills)
        rows.append(
            {
                "topic": topic,
                "owner": meta.owner,
                "status": freshness,
                "refreshed": meta.refreshed.isoformat() if meta.refreshed else None,
                "review": meta.review,
                "consumers": consumers,
                "orphan": not consumers,
                "staged": topic in staged,
            }
        )

    owners: dict[str, int] = {}
    for r in rows:
        owners[r["owner"] or "(unowned)"] = owners.get(r["owner"] or "(unowned)", 0) + 1

    learnings_dir = content_root / profile / "learnings"
    learnings_notes = (
        sorted(p.name for p in learnings_dir.glob("*.md")) if learnings_dir.is_dir() else []
    )

    summary = {
        "managed": len(rows),
        "due": sum(1 for r in rows if r["status"] in _ACTIONABLE),
        "unowned": sum(1 for r in rows if not r["owner"]),
        "orphan": sum(1 for r in rows if r["orphan"]),
        "staged": len(staged),
        "learnings_notes": len(learnings_notes),
    }
    return {
        "profile": profile,
        "rows": sorted(rows, key=lambda r: r["topic"]),
        "owners": owners,
        "learnings_notes": learnings_notes,
        "summary": summary,
    }


# --- CLI ----------------------------------------------------------------------


def _all_profiles(profiles_root: Path) -> list[str]:
    if not profiles_root.is_dir():
        return []
    return sorted(
        c.name for c in profiles_root.iterdir() if c.is_dir() and (c / "PROFILE.md").is_file()
    )


def _print_report(d: dict) -> None:
    s = d["summary"]
    print(f"\nprofile: {d['profile']}")
    print(
        f"  {s['managed']} managed · {s['due']} due · {s['unowned']} unowned · "
        f"{s['orphan']} orphan · {s['staged']} staged · {s['learnings_notes']} learnings note(s)"
    )
    print("  owners: " + ", ".join(f"{o}={n}" for o, n in sorted(d["owners"].items())))

    def _names(pred):
        return [r["topic"] for r in d["rows"] if pred(r)]

    for label, names in (
        ("due for review", _names(lambda r: r["status"] in _ACTIONABLE)),
        ("unowned", _names(lambda r: not r["owner"])),
        ("orphan (no skill reads them)", _names(lambda r: r["orphan"])),
        ("staged — awaiting promotion", _names(lambda r: r["staged"])),
    ):
        if names:
            print(f"  {label}: {', '.join(names)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.knowledge_status",
        description="Unified knowledge-corpus dashboard: freshness + ownership + usage + staged.",
    )
    parser.add_argument("command", choices=("report",))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profiles-root", default=None)
    parser.add_argument("--content-root", default=None)
    args = parser.parse_args(argv)

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else resolve_profiles_root()
    )
    content_root = (
        Path(args.content_root).expanduser().resolve()
        if args.content_root
        else resolve_content_root()
    )
    if args.all:
        profiles = _all_profiles(profiles_root)
    elif args.profile:
        profiles = [args.profile]
    else:
        raise SystemExit("[knowledge-status] pass --profile <slug> or --all")

    today = date.today()
    dashboards = [status(profiles_root, content_root, p, today) for p in profiles]
    if args.json:
        print(json.dumps(dashboards, indent=2))
        return 0
    for d in dashboards:
        _print_report(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

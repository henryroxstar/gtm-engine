"""Knowledge-file lifecycle metadata — freshness + provenance (Phase 1).

Every *managed* knowledge topic file carries a small YAML frontmatter block:

    ---
    source: <url | deck name | path | manual>   # provenance (required)
    owner: <person/role>                         # maintainer (optional)
    refreshed: 2026-07-18                         # last human-verified date (required)
    review: 90d                                   # cadence (required)
    ---
    # Title
    ...

``refreshed`` is the *authoritative* freshness signal — a human states when the facts were last
verified, which is more reliable than filesystem mtime (a reformat bumps mtime but changes no fact).
``review`` is a cadence keyword; the due date is ``refreshed + cadence``. ``evergreen`` never goes
stale. A managed file with no frontmatter is ``unknown`` (and fails ``check``).

Reusable across every profile by construction: the module only ever takes a ``profiles_root`` and a
``profile`` name (never a global), and ``iter_managed_topics`` walks whatever ``knowledge/`` dir it
is handed. Anchors on ``agent.profiles.list_profiles`` for the "every profile" sweep.

Pure/stdlib-only (no PyYAML dep — the frontmatter is a flat ``key: value`` subset, matching how
``PROFILE.md`` is parsed elsewhere). ``seed`` is the one mutating helper and lives in the CLI layer.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

from .paths import PathConfig, resolve_profiles_root

# --- what counts as a managed knowledge topic --------------------------------
#
# Managed = the human-authored prose corpus that goes stale and drives skills. We exclude the
# refresh SOP itself, skill definitions living in knowledge/, raw source briefs, brand assets, and
# directory index/readme docs.
EXCLUDED_NAMES = frozenset({"REFRESH.md", "deck-composer.md"})
EXCLUDED_DIRS = frozenset({"source", "brand"})
EXCLUDED_STEMS = frozenset({"README", "INDEX", "SOURCES"})

#: Review cadences → interval in days. ``evergreen`` is None (never due).
REVIEW_CADENCE_DAYS: dict[str, int | None] = {
    "evergreen": None,
    "14d": 14,
    "30d": 30,
    "90d": 90,
    "180d": 180,
    "365d": 365,
}

REQUIRED_FIELDS = ("source", "refreshed", "review")

#: Days before the due date at which a topic starts showing as ``due-soon``.
DUE_SOON_WINDOW_DAYS = 14


def is_managed_topic(relpath: str | PurePosixPath) -> bool:
    """True if ``relpath`` (relative to a ``knowledge/`` dir) is a lifecycle-managed topic file."""
    rel = PurePosixPath(str(relpath))
    if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
        return False
    name = rel.name
    if not name.endswith(".md") or name in EXCLUDED_NAMES:
        return False
    if rel.stem.upper() in EXCLUDED_STEMS:
        return False
    return True


def iter_managed_topics(knowledge_dir: Path) -> list[Path]:
    """All managed topic files under ``knowledge_dir``, sorted, subfolders included."""
    if not knowledge_dir.is_dir():
        return []
    out = []
    for path in sorted(knowledge_dir.rglob("*.md")):
        if is_managed_topic(path.relative_to(knowledge_dir).as_posix()):
            out.append(path)
    return out


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split leading ``--- ... ---`` frontmatter into a flat dict + the remaining body.

    Returns ``({}, text)`` when there is no (or an unterminated) frontmatter block. Only flat
    ``key: value`` scalars are parsed — blank lines and ``#`` comments inside the block are ignored.
    """
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    lines = text.splitlines(keepends=True)
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for raw in lines[1:end]:
        s = raw.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        key, _, value = s.partition(":")
        meta[key.strip()] = value.strip()
    return meta, "".join(lines[end + 1 :])


#: Canonical frontmatter field order when (re)writing a block.
_FIELD_ORDER = ("source", "owner", "refreshed", "review")


def upsert_frontmatter(text: str, updates: dict[str, str | None]) -> str:
    """Return ``text`` with its frontmatter merged with ``updates`` (None values are ignored).

    Creates a frontmatter block if the file has none. Canonical fields come first in a stable
    order, then any extra keys alpha-sorted — so a re-stamp (e.g. bumping ``refreshed`` on promote)
    is deterministic and diff-friendly. The body below the frontmatter is preserved byte-for-byte.
    """
    existing, body = parse_frontmatter(text)
    merged = {**existing, **{k: v for k, v in updates.items() if v is not None}}
    ordered = [k for k in _FIELD_ORDER if k in merged]
    ordered += sorted(k for k in merged if k not in _FIELD_ORDER)
    block = "---\n" + "".join(f"{k}: {merged[k]}\n" for k in ordered) + "---\n"
    return block + body


@dataclass(frozen=True)
class KnowledgeMeta:
    """Parsed + validated lifecycle metadata for one knowledge topic file."""

    relpath: str  # path relative to the knowledge/ dir
    has_frontmatter: bool
    source: str | None = None
    owner: str | None = None
    refreshed: date | None = None
    review: str | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)


def read_meta(path: Path, knowledge_dir: Path) -> KnowledgeMeta:
    """Parse + validate the frontmatter of a managed topic file. Never raises — problems are
    collected in ``errors`` so ``check`` can report every file, not just the first bad one."""
    rel = path.relative_to(knowledge_dir).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    raw, _ = parse_frontmatter(text)
    errors: list[str] = []

    if not raw:
        return KnowledgeMeta(relpath=rel, has_frontmatter=False, errors=("missing frontmatter",))

    for f in REQUIRED_FIELDS:
        if not raw.get(f):
            errors.append(f"missing required field '{f}'")

    refreshed: date | None = None
    if raw.get("refreshed"):
        try:
            refreshed = date.fromisoformat(raw["refreshed"])
        except ValueError:
            errors.append(f"invalid 'refreshed' date: {raw['refreshed']!r} (want YYYY-MM-DD)")

    review = raw.get("review") or None
    if review and review not in REVIEW_CADENCE_DAYS:
        errors.append(
            f"invalid 'review' cadence: {review!r} (want one of {sorted(REVIEW_CADENCE_DAYS)})"
        )

    return KnowledgeMeta(
        relpath=rel,
        has_frontmatter=True,
        source=raw.get("source") or None,
        owner=raw.get("owner") or None,
        refreshed=refreshed,
        review=review,
        errors=tuple(errors),
    )


def refreshed_date(path: Path) -> date | None:
    """The ``refreshed`` frontmatter date of a knowledge file, or None if absent/invalid.

    The narrow read used by ``agent.readiness`` to prefer the authoritative human vintage over
    filesystem mtime — takes a bare path, no knowledge-dir context needed."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    raw, _ = parse_frontmatter(text)
    stamp = raw.get("refreshed")
    if not stamp:
        return None
    try:
        return date.fromisoformat(stamp)
    except ValueError:
        return None


def due_date(meta: KnowledgeMeta) -> date | None:
    """The date this topic becomes due for review, or None for evergreen/unknown."""
    if meta.refreshed is None or meta.review is None:
        return None
    days = REVIEW_CADENCE_DAYS.get(meta.review)
    if days is None:  # evergreen
        return None
    return meta.refreshed + _days(days)


def status_of(meta: KnowledgeMeta, today: date) -> str:
    """One of: ``evergreen``, ``fresh``, ``due-soon``, ``overdue``, ``unknown``."""
    if not meta.has_frontmatter or meta.errors:
        return "unknown"
    if meta.review == "evergreen":
        return "evergreen"
    due = due_date(meta)
    if due is None:
        return "unknown"
    if today >= due:
        return "overdue"
    # Cadence-aware due-soon window: never wider than half the cadence, so a short cadence
    # (e.g. 14d) isn't reported "due-soon" from the moment it's refreshed. No effect on ≥30d.
    cadence_days = REVIEW_CADENCE_DAYS.get(meta.review or "") or DUE_SOON_WINDOW_DAYS
    window = min(DUE_SOON_WINDOW_DAYS, cadence_days // 2)
    if today >= due - _days(window):
        return "due-soon"
    return "fresh"


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def profile_meta(profiles_root: Path, profile: str, today: date) -> list[tuple[KnowledgeMeta, str]]:
    """(meta, status) for every managed topic in one profile's knowledge dir."""
    knowledge_dir = profiles_root / profile / "knowledge"
    rows = []
    for path in iter_managed_topics(knowledge_dir):
        meta = read_meta(path, knowledge_dir)
        rows.append((meta, status_of(meta, today)))
    return rows


def check(profiles_root: Path, profiles: list[str]) -> list[str]:
    """Return one human-readable problem string per metadata violation across ``profiles``.
    Empty list ⇒ every managed topic has valid frontmatter (the gate passes)."""
    problems: list[str] = []
    for profile in profiles:
        knowledge_dir = profiles_root / profile / "knowledge"
        for path in iter_managed_topics(knowledge_dir):
            meta = read_meta(path, knowledge_dir)
            for err in meta.errors:
                problems.append(f"{profile}/knowledge/{meta.relpath}: {err}")
    return problems


# --- seeding (the one mutating helper; CLI-only) -----------------------------

_EVERGREEN_STEMS = frozenset(
    {"voice", "brand-notes", "audience-psychology", "hook-matrix", "social-tuning"}
)
_SLOW_DIRS = frozenset({"guidance", "adversary-testing"})


def default_review(relpath: str) -> str:
    """A sensible starting cadence by topic — the operator tunes it in-file afterwards."""
    rel = PurePosixPath(relpath)
    top = rel.parts[0] if len(rel.parts) > 1 else ""
    if top in _SLOW_DIRS:
        return "180d"
    if rel.stem in _EVERGREEN_STEMS:
        return "evergreen"
    if rel.stem == "travel-policy":
        return "365d"
    return "90d"


def _git_last_date(repo_root: Path, path: Path) -> date | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%as", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        stamp = out.stdout.strip()
        return date.fromisoformat(stamp) if stamp else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def seed_file(path: Path, knowledge_dir: Path, repo_root: Path) -> bool:
    """Insert a frontmatter block into a managed topic file that lacks one. Idempotent — returns
    False if it already has frontmatter. ``refreshed`` is seeded from the file's last git commit
    date (falling back to mtime), ``source`` to ``manual`` (a placeholder to replace), ``review``
    to a topic-appropriate default."""
    rel = path.relative_to(knowledge_dir).as_posix()
    text = path.read_text(encoding="utf-8")
    existing, _ = parse_frontmatter(text)
    if existing:
        return False
    seeded = _git_last_date(repo_root, path) or date.fromtimestamp(path.stat().st_mtime)
    block = (
        "---\n"
        "source: manual\n"
        f"refreshed: {seeded.isoformat()}\n"
        f"review: {default_review(rel)}\n"
        "---\n"
    )
    path.write_text(block + text, encoding="utf-8")
    return True


# --- CLI ----------------------------------------------------------------------


def _all_profiles(profiles_root: Path) -> list[str]:
    """Names of every profile (a dir containing ``PROFILE.md``), sorted. Mirrors
    ``agent.profiles.list_profiles`` — reimplemented here because ``gtm_core`` must not import
    ``agent`` (the import-layering contract, tests/contracts/test_layering.py)."""
    if not profiles_root.is_dir():
        return []
    return sorted(
        c.name for c in profiles_root.iterdir() if c.is_dir() and (c / "PROFILE.md").is_file()
    )


def _resolve_profiles(profiles_root: Path, args) -> list[str]:
    if args.all:
        return _all_profiles(profiles_root)
    if not args.profile:
        raise SystemExit("[knowledge-meta] pass --profile <slug> or --all")
    return [args.profile]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.knowledge_meta",
        description="Knowledge-file freshness/provenance metadata: report, check, seed.",
    )
    parser.add_argument("command", choices=("report", "check", "seed"))
    parser.add_argument("--profile", default=None, help="one profile slug")
    parser.add_argument("--all", action="store_true", help="every profile (via list_profiles)")
    parser.add_argument("--json", action="store_true", help="machine-readable output (report)")
    parser.add_argument("--profiles-root", default=None, help="override profiles root")
    parser.add_argument(
        "--repo-root", default=None, help="repo root for git date seeding (seed only)"
    )
    args = parser.parse_args(argv)

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else PathConfig.from_env().profiles_root
    )
    profiles = _resolve_profiles(profiles_root, args)
    today = date.today()

    if args.command == "check":
        problems = check(profiles_root, profiles)
        if problems:
            print(
                f"[knowledge-meta] {len(problems)} metadata problem(s) "
                f"across {len(profiles)} profile(s):",
                file=sys.stderr,
            )
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            print(
                "\nFix by adding/repairing frontmatter, or run: "
                "uv run python -m gtm_core.knowledge_meta seed --all",
                file=sys.stderr,
            )
            return 1
        print(f"[knowledge-meta] OK — all managed topics valid across {len(profiles)} profile(s).")
        return 0

    if args.command == "seed":
        repo_root = (
            Path(args.repo_root).expanduser().resolve()
            if args.repo_root
            else resolve_profiles_root().parent
        )
        seeded = 0
        for profile in profiles:
            knowledge_dir = profiles_root / profile / "knowledge"
            for path in iter_managed_topics(knowledge_dir):
                if seed_file(path, knowledge_dir, repo_root):
                    seeded += 1
                    print(f"  seeded {profile}/knowledge/{path.relative_to(knowledge_dir)}")
        print(f"[knowledge-meta] seeded {seeded} file(s) across {len(profiles)} profile(s).")
        return 0

    # report
    payload = []
    for profile in profiles:
        rows = profile_meta(profiles_root, profile, today)
        payload.append((profile, rows))

    if args.json:
        out = [
            {
                "profile": profile,
                "topics": [
                    {
                        "topic": m.relpath,
                        "status": status,
                        "refreshed": m.refreshed.isoformat() if m.refreshed else None,
                        "review": m.review,
                        "source": m.source,
                        "owner": m.owner,
                        "due": due_date(m).isoformat() if due_date(m) else None,
                    }
                    for m, status in rows
                ],
            }
            for profile, rows in payload
        ]
        print(json.dumps(out, indent=2))
        return 0

    _order = {"overdue": 0, "due-soon": 1, "unknown": 2, "fresh": 3, "evergreen": 4}
    for profile, rows in payload:
        rows = sorted(rows, key=lambda r: (_order.get(r[1], 9), r[0].relpath))
        counts: dict[str, int] = {}
        print(f"\nprofile: {profile}")
        print(f"  {'STATUS':<9} {'REFRESHED':<11} {'REVIEW':<9} TOPIC")
        for m, status in rows:
            counts[status] = counts.get(status, 0) + 1
            refreshed = m.refreshed.isoformat() if m.refreshed else "—"
            print(f"  {status:<9} {refreshed:<11} {(m.review or '—'):<9} {m.relpath}")
        summary = " · ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no topics"
        print(f"  {len(rows)} managed topics — {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

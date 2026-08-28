"""Stage → review → promote for knowledge refreshes (knowledge-lifecycle PRD, Phase 3a).

The safety-critical core of automated refresh. ``profiles/<p>/`` is **read-only at runtime**
(CLAUDE.md tenant boundary — only ``content/<p>/`` is writable), so a refresh can never rewrite the
live corpus. Instead:

  1. the refresh skill writes a candidate to ``content/<p>/knowledge-staging/<topic>`` (``stage``);
  2. an operator reviews the unified ``diff`` against the live file;
  3. an operator runs ``promote`` — the ONLY thing that writes ``profiles/<p>/knowledge/`` — which
     copies the candidate over the live file, re-stamps ``refreshed:`` to today, and clears staging.

``promote`` is an explicit human command (mirroring how onboarding promotes ``profiles/.staging/``);
the headless brain has no path to it. Everything here is deterministic, files-only, and reusable —
it takes ``profiles_root`` + ``content_root`` + ``profile``, never a global.
"""

from __future__ import annotations

import argparse
import difflib
from datetime import date
from pathlib import Path

from . import knowledge_meta as km
from .paths import PathConfig, resolve_content_root, resolve_profiles_root

_STAGING_DIRNAME = "knowledge-staging"


def _safe_topic_relpath(topic: str) -> Path:
    """A staged topic path, guarded against traversal (mirrors paths._safe_segment per segment).
    ``topic`` may be a subdir path like ``guidance/01-nist`` but never absolute, ``..`` or NUL."""
    rel = topic if topic.endswith(".md") else f"{topic}.md"
    parts = Path(rel).parts
    for seg in parts:
        if seg in ("", ".", "..") or "\x00" in seg or seg.startswith("/"):
            raise ValueError(f"unsafe topic: {topic!r}")
    if Path(rel).is_absolute():
        raise ValueError(f"unsafe topic: {topic!r}")
    return Path(*parts)


def staging_dir(content_root: Path, profile: str) -> Path:
    return content_root / profile / _STAGING_DIRNAME


def staged_path(content_root: Path, profile: str, topic: str) -> Path:
    return staging_dir(content_root, profile) / _safe_topic_relpath(topic)


def live_path(profiles_root: Path, profile: str, topic: str) -> Path:
    return profiles_root / profile / "knowledge" / _safe_topic_relpath(topic)


def stage(content_root: Path, profile: str, topic: str, candidate_text: str) -> Path:
    """Write a refreshed candidate to the profile's staging area. Returns the staged path."""
    target = staged_path(content_root, profile, topic)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(candidate_text, encoding="utf-8")
    return target


def list_staged(content_root: Path, profile: str) -> list[str]:
    """Topic relpaths (extension stripped) currently staged for ``profile``, sorted."""
    root = staging_dir(content_root, profile)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        out.append(rel[:-3])
    return out


def diff(profiles_root: Path, content_root: Path, profile: str, topic: str) -> str:
    """Unified diff of the live knowledge file vs the staged candidate ('' if identical)."""
    live = live_path(profiles_root, profile, topic)
    staged = staged_path(content_root, profile, topic)
    if not staged.is_file():
        raise FileNotFoundError(f"no staged candidate for {profile}/{topic}")
    live_text = live.read_text(encoding="utf-8") if live.is_file() else ""
    staged_text = staged.read_text(encoding="utf-8")
    return "".join(
        difflib.unified_diff(
            live_text.splitlines(keepends=True),
            staged_text.splitlines(keepends=True),
            fromfile=f"live/{topic}",
            tofile=f"staged/{topic}",
        )
    )


def promote(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    topic: str,
    *,
    today: date,
    source: str | None = None,
    remove_staged: bool = True,
) -> Path:
    """Promote a staged candidate into the live corpus — the operator gate.

    Copies the staged candidate over ``profiles/<p>/knowledge/<topic>``, then re-stamps its
    frontmatter: ``refreshed`` → ``today`` always, ``source`` if provided, and a ``review`` cadence
    defaulted by topic when the candidate declares none. Clears the staged file on success. Raises
    if there is no staged candidate."""
    staged = staged_path(content_root, profile, topic)
    if not staged.is_file():
        raise FileNotFoundError(f"no staged candidate for {profile}/{topic}")
    text = staged.read_text(encoding="utf-8")

    existing, _ = km.parse_frontmatter(text)
    updates: dict[str, str | None] = {"refreshed": today.isoformat()}
    if source is not None:
        updates["source"] = source
    elif not existing.get("source"):
        updates["source"] = "manual"
    if not existing.get("review"):
        updates["review"] = km.default_review(_safe_topic_relpath(topic).as_posix())
    stamped = km.upsert_frontmatter(text, updates)

    target = live_path(profiles_root, profile, topic)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stamped, encoding="utf-8")
    if remove_staged:
        staged.unlink()
    return target


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.knowledge_staging",
        description="Review + promote staged knowledge refreshes (the operator gate).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("list", "path", "diff", "promote"):
        sp = sub.add_parser(name)
        sp.add_argument("--profile", required=True)
        if name in ("path", "diff", "promote"):
            sp.add_argument("--topic", required=True)
        if name == "promote":
            sp.add_argument("--source", default=None, help="override the source: provenance field")
        sp.add_argument("--profiles-root", default=None)
        sp.add_argument("--content-root", default=None)
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
    _ = PathConfig  # (kept import parity with sibling CLIs; env override already applied above)

    if args.cmd == "path":
        # The staging path a refresh skill should WRITE its candidate to (traversal-guarded).
        print(staged_path(content_root, args.profile, args.topic))
        return 0

    if args.cmd == "list":
        staged = list_staged(content_root, args.profile)
        print(f"{len(staged)} staged candidate(s) for {args.profile}:")
        for t in staged:
            print(f"  - {t}")
        return 0

    if args.cmd == "diff":
        out = diff(profiles_root, content_root, args.profile, args.topic)
        print(out if out else "(no change vs live)")
        return 0

    # promote
    target = promote(
        profiles_root,
        content_root,
        args.profile,
        args.topic,
        today=date.today(),
        source=args.source,
    )
    print(f"promoted {args.profile}/{args.topic} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

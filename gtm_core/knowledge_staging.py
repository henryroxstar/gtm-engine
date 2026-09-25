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
import shutil
import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

from . import knowledge_meta as km
from .paths import PathConfig, _safe_segment, resolve_content_root, resolve_profiles_root

_STAGING_DIRNAME = "knowledge-staging"

#: Extensions a staged topic may carry. **Closed on purpose**: ``.md`` is the prose corpus and
#: ``.toml`` the machine-readable targeting files (the ICP rubric, the hook bank, the persona
#: vocabulary). A topic carrying any OTHER extension is REFUSED, never coerced — appending ``.md``
#: to ``icp-scoring.toml`` is exactly the defect this allowlist closes, and it left those three
#: files with no staged-review path at all. Widening this set means answering, for the new
#: extension, what ``promote`` must verify before it may overwrite a live file.
_ALLOWED_SUFFIXES = frozenset({".md", ".toml"})

#: Prefix of the provenance comment a ``.toml`` promote stamps (see ``_stamp_toml_provenance``).
_TOML_PROVENANCE_PREFIX = "# refreshed:"


def _safe_topic_relpath(topic: str) -> Path:
    """A staged topic path, guarded against traversal (mirrors paths._safe_segment per segment).
    ``topic`` may be a subdir path like ``guidance/01-nist`` but never absolute, ``..`` or NUL.

    A topic with no extension defaults to ``.md``; one carrying an allowed extension keeps it; any
    other extension raises rather than being silently renamed (``_ALLOWED_SUFFIXES``)."""
    suffix = Path(topic).suffix
    if not suffix:
        rel = f"{topic}.md"
    elif suffix in _ALLOWED_SUFFIXES:
        rel = topic
    else:
        raise ValueError(
            f"unsupported topic extension {suffix!r} in {topic!r} "
            f"(allowed: {', '.join(sorted(_ALLOWED_SUFFIXES))})"
        )
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
    """The live file a topic promotes onto.

    Topics are namespaced by managed root (``km.MANAGED_ROOTS``): a bare relpath is a
    ``knowledge/`` topic, while one carrying a root's prefix (today only ``products/``) resolves
    under ``profiles/<p>/`` directly. The traversal guard runs on the WHOLE topic either way, so a
    prefix cannot be used to escape the profile."""
    rel = _safe_topic_relpath(topic)
    for sub, prefix in km.MANAGED_ROOTS:
        if prefix and rel.parts[0] == prefix.rstrip("/"):
            return profiles_root / profile / sub / Path(*rel.parts[1:])
    return profiles_root / profile / "knowledge" / rel


def stage(content_root: Path, profile: str, topic: str, candidate_text: str) -> Path:
    """Write a refreshed candidate to the profile's staging area. Returns the staged path."""
    target = staged_path(content_root, profile, topic)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(candidate_text, encoding="utf-8")
    return target


def list_staged(content_root: Path, profile: str) -> list[str]:
    """Topic relpaths currently staged for ``profile``, sorted.

    A ``.md`` topic comes back BARE (``company``) — the vocabulary every skill, ledger row and
    ``knowledge_status`` row already uses. Any other allowed extension comes back WITH it, because
    that is what ``_safe_topic_relpath`` needs to resolve the same file again."""
    root = staging_dir(content_root, profile)
    if not root.is_dir():
        return []
    out = []
    for suffix in _ALLOWED_SUFFIXES:
        for path in root.rglob(f"*{suffix}"):
            rel = path.relative_to(root).as_posix()
            out.append(rel[: -len(suffix)] if suffix == ".md" else rel)
    return sorted(out)


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


def _require_parsable_toml(text: str, topic: str) -> None:
    """The ``.toml`` promote gate: refuse a candidate that cannot safely replace a live file.

    ``promote`` is the only writer of ``profiles/``, so a malformed rubric promoted over a working
    one does not degrade gracefully — the next ``load_rubric`` raises and the prospecting pipeline
    stops. An EMPTY parse is refused for the same reason: a zero-byte or comment-only file is
    *valid* TOML, so a bare parse would wave through the one candidate that silently blanks the
    targeting file it replaces."""
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"staged candidate for {topic!r} is not valid TOML: {exc}") from exc
    if not parsed:
        raise ValueError(
            f"staged candidate for {topic!r} parses as empty TOML "
            "(no keys) — refusing to blank the live file"
        )


def _stamp_toml_provenance(text: str, *, today: date, source: str | None) -> str:
    """Prepend the ``.toml`` analogue of the ``refreshed:`` frontmatter re-stamp.

    ``km.upsert_frontmatter`` writes YAML frontmatter, which is not valid TOML — so a ``.toml``
    promote records the same two facts as a comment header instead. It is an UPSERT, like its
    frontmatter counterpart: a header from an earlier promote is replaced, never accumulated."""
    body = text.lstrip("\n")
    while body.startswith(_TOML_PROVENANCE_PREFIX):
        _, _, body = body.partition("\n")
    header = f"{_TOML_PROVENANCE_PREFIX} {today.isoformat()}"
    if source:
        header += f"  source: {source}"
    return f"{header}\n{body}"


def snapshot_dir(content_root: Path, profile: str) -> Path:
    return content_root / _safe_segment(profile, "profile") / ".snapshots" / "knowledge"


def _utc_stamp(now: datetime | None = None) -> str:
    # microsecond resolution so back-to-back snapshots never collide on filename
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S-%fZ")


def snapshot_topic(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    topic: str,
    *,
    now: datetime | None = None,
) -> Path | None:
    """Snapshot the current live file before overwriting. Returns destination or None if absent."""
    target = live_path(profiles_root, profile, topic)
    if not target.exists():
        return None
    snap_dir = snapshot_dir(content_root, profile)
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest = snap_dir / Path(topic).parent / f"{Path(topic).name}.{_utc_stamp(now)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dest)
    return dest


def restore(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    topic: str,
    *,
    snapshot_file: str | Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Restore a live topic file from a snapshot (newest by default).

    Snapshots the current live file first, so restore is itself reversible.
    """
    snap_dir = snapshot_dir(content_root, profile)
    if snapshot_file:
        src = Path(snapshot_file)
        if not src.is_absolute():
            src = snap_dir / snapshot_file
        if not src.is_file():
            raise FileNotFoundError(f"snapshot file {src} not found")
    else:
        topic_name = Path(topic).name
        topic_parent = Path(topic).parent
        search_dir = snap_dir / topic_parent
        snaps = sorted(p for p in search_dir.glob(f"{topic_name}.*") if p.is_file())
        if not snaps and not topic_name.endswith(".md"):
            snaps = sorted(p for p in search_dir.glob(f"{topic_name}.md.*") if p.is_file())
        if not snaps:
            raise FileNotFoundError(f"no snapshots for {topic} in {snap_dir}")
        src = snaps[-1]

    target = live_path(profiles_root, profile, topic)
    # Snapshot current file first so restore is reversible
    snapshot_topic(profiles_root, content_root, profile, topic, now=now)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    return src


def promote(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    topic: str,
    *,
    today: date,
    source: str | None = None,
    remove_staged: bool = True,
    now: datetime | None = None,
) -> Path:
    """Promote a staged candidate into the live corpus — the operator gate.

    Copies the staged candidate over ``profiles/<p>/knowledge/<topic>``, then re-stamps its
    provenance: ``refreshed`` → ``today`` always, ``source`` if provided, and (``.md`` only) a
    ``review`` cadence defaulted by topic when the candidate declares none. A ``.toml`` candidate
    must round-trip through ``tomllib`` BEFORE it may overwrite the live file, and carries its
    provenance as a comment header instead of YAML frontmatter. Clears the staged file on success.
    Raises if there is no staged candidate, or if a ``.toml`` candidate fails the parse gate — in
    which case the live file is untouched and the candidate is left staged for repair."""
    rel = _safe_topic_relpath(topic)
    staged = staged_path(content_root, profile, topic)
    if not staged.is_file():
        raise FileNotFoundError(f"no staged candidate for {profile}/{topic}")
    text = staged.read_text(encoding="utf-8")

    if rel.suffix == ".toml":
        _require_parsable_toml(text, topic)
        stamped = _stamp_toml_provenance(text, today=today, source=source)
    else:
        existing, _ = km.parse_frontmatter(text)
        updates: dict[str, str | None] = {"refreshed": today.isoformat()}
        if source is not None:
            updates["source"] = source
        elif not existing.get("source"):
            updates["source"] = "manual"
        if not existing.get("review"):
            updates["review"] = km.default_review(rel.as_posix())
        stamped = km.upsert_frontmatter(text, updates)

    target = live_path(profiles_root, profile, topic)
    # Snapshot the live file before overwriting (R-14)
    snapshot_topic(profiles_root, content_root, profile, topic, now=now)
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
    for name in ("list", "path", "diff", "promote", "restore", "stage"):
        sp = sub.add_parser(name)
        sp.add_argument("--profile", required=True)
        if name in ("path", "diff", "promote", "restore", "stage"):
            sp.add_argument("--topic", required=True)
        if name == "promote":
            sp.add_argument("--source", default=None, help="override the source: provenance field")
        if name == "restore":
            sp.add_argument(
                "--snapshot", default=None, help="optional snapshot filename to restore"
            )
        if name == "stage":
            sp.add_argument(
                "--from", dest="from_file", required=True, help="candidate file to stage"
            )
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

    if args.cmd == "stage":
        from_p = Path(args.from_file).expanduser().resolve()
        if not from_p.is_file():
            print(f"Error: file not found: {from_p}", file=sys.stderr)
            return 1
        staged_p = stage(content_root, args.profile, args.topic, from_p.read_text(encoding="utf-8"))
        print(staged_p)
        return 0

    if args.cmd == "restore":
        src = restore(
            profiles_root,
            content_root,
            args.profile,
            args.topic,
            snapshot_file=args.snapshot,
        )
        print(f"restored {args.profile}/{args.topic} from {src.name}")
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

"""Generated knowledge index (E-3, PRD §5) — the catalog of a profile's ``knowledge/``
corpus, with topic tags, freshness, and a link/backlink graph, so a pack's readiness check
(``agent.readiness``) and an operator can see what exists beyond the canonical topic files and how
the corpus cross-references itself.

Pure and read-only: this module only *computes* the index from what's on disk. It is never
hand-edited and never itself the source of truth — ``knowledge/*.md`` is. Rebuilt by simply calling
:func:`build_knowledge_index` (one dir) or :func:`build_profile_index` (knowledge + products) again;
there is no cache to invalidate.

The graph layer (``tags``/``related``/``links_out``/``links_in``/``dangling``) is additive and has
**no runtime consumer yet** — it is queryable via ``python -m gtm_core.knowledge_index --json`` so a
skill can adopt it later. Stdlib-only; reuses ``gtm_core.knowledge_meta`` for frontmatter.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import knowledge_meta as km
from .paths import PathConfig, _safe_segment

#: Canonical topic files sampled from real profiles (PRD §1.5) — the corpus's "table of
#: contents". Anything else is the messy tail, still indexed, just not "canonical".
CANONICAL_TOPICS = frozenset({"company", "product", "icp-personas", "voice", "case-studies"})

# Non-topic files that live alongside the corpus but aren't a knowledge topic themselves.
_EXCLUDED_FILENAMES = frozenset({"REFRESH.md"})

# A ``[[wikilink]]`` target that points at an auto-memory node (``feedback_*``/``project_*``/
# ``reference_*``/``user_*``) rather than a sibling knowledge file. These are deliberate external
# references, not broken corpus links — so they are neither ``links_out`` nor ``dangling``.
_MEMORY_REF = re.compile(r"^(?:feedback|project|reference|user)_[a-z0-9_]+$")

# ``[[slug]]`` or ``[[dir/slug]]`` wikilink. Body-scoped; extracted only after code is masked out.
_WIKILINK = re.compile(r"\[\[([^\]\n]+?)\]\]")

# Fenced code blocks and inline code — masked (newlines preserved) before wikilink extraction so
# literal tokens like ``[[staff]]``/``[[bin]]`` inside code never count as links.
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


@dataclass(frozen=True)
class KnowledgeIndexEntry:
    topic: str  # filename stem — the retrieval key resolve_knowledge_file() also uses
    relpath: str  # path relative to the scan root (subfolder files included)
    canonical: bool
    age_days: float
    tags: tuple[str, ...] = ()  # frontmatter ``tags:`` labels (topic/entity)
    related: tuple[str, ...] = ()  # frontmatter ``related:`` sibling refs (raw, as authored)
    links_out: tuple[str, ...] = ()  # resolved corpus refs (from ``related:`` + body ``[[..]]``)
    links_in: tuple[str, ...] = ()  # backlinks — corpus topics whose links_out include this one
    dangling: tuple[str, ...] = ()  # refs that resolve to no corpus file (and aren't memory nodes)


def _mask_code(body: str) -> str:
    """Blank fenced + inline code, preserving newlines so line numbers/structure are unchanged."""

    def _blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return _INLINE_CODE.sub(_blank, _FENCE.sub(_blank, body))


def _extract_wikilinks(body: str) -> list[str]:
    """All ``[[..]]`` targets in ``body``, code masked out, order-preserving + de-duplicated."""
    seen: list[str] = []
    for m in _WIKILINK.finditer(_mask_code(body)):
        target = m.group(1).strip()
        if target and target not in seen:
            seen.append(target)
    return seen


def _corpus_keys(relpaths: list[str]) -> dict[str, str]:
    """Map every resolvable name of a corpus file → its canonical relpath key.

    A ref may be authored as a bare stem (``company``), a relpath-without-extension
    (``guidance/nist-gateway-alignment``), or with the ``.md`` suffix — all resolve to the same
    file. The canonical key returned in ``links_out`` is the relpath-without-extension.
    """
    keys: dict[str, str] = {}
    for rel in relpaths:
        canon = rel[:-3] if rel.endswith(".md") else rel
        stem = canon.rsplit("/", 1)[-1]
        keys.setdefault(canon, canon)
        keys.setdefault(stem, canon)  # stem is a fallback; explicit relpath wins if both present
    return keys


def _resolve_ref(raw: str, keys: dict[str, str]) -> tuple[str | None, bool]:
    """Resolve one ref → (canonical_key | None, is_dangling).

    Returns ``(key, False)`` when it resolves to a corpus file, ``(None, False)`` when it's a
    recognized external memory node (deliberate, not dangling), and ``(None, True)`` otherwise.
    """
    target = raw.strip()
    if target.endswith(".md"):
        target = target[:-3]
    if target in keys:
        return keys[target], False
    stem = target.rsplit("/", 1)[-1]
    if stem in keys:
        return keys[stem], False
    if _MEMORY_REF.match(target):
        return None, False  # external memory ref — not a broken corpus link
    return None, True


def _build_entries(files: list[tuple[str, Path]], *, now: float) -> tuple[KnowledgeIndexEntry, ...]:
    """Core builder shared by :func:`build_knowledge_index` and :func:`build_profile_index`.

    ``files`` is a list of ``(relpath, path)`` where ``relpath`` is relative to the scan root.
    Two passes: (1) read frontmatter + body links per file; (2) invert ``links_out`` into
    ``links_in`` once the whole corpus is known, then freeze the entries.
    """
    keys = _corpus_keys([rel for rel, _ in files])
    raw: dict[str, dict] = {}  # canonical relpath-without-ext → assembled fields

    for rel, path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = km.parse_frontmatter(text)
        related = km.split_list(meta.get("related"))
        refs = list(related) + _extract_wikilinks(body)
        out: list[str] = []
        dangling: list[str] = []
        for ref in refs:
            canon, is_dangling = _resolve_ref(ref, keys)
            if canon is not None and canon not in out:
                out.append(canon)
            elif is_dangling and ref not in dangling:
                dangling.append(ref)
        canon_self = rel[:-3] if rel.endswith(".md") else rel
        raw[canon_self] = {
            "topic": Path(rel).stem,
            "relpath": rel,
            "canonical": Path(rel).stem in CANONICAL_TOPICS,
            "age_days": (now - path.stat().st_mtime) / 86400,
            "tags": km.split_list(meta.get("tags")),
            "related": related,
            "links_out": tuple(out),
            "dangling": tuple(dangling),
        }

    backlinks: dict[str, list[str]] = {}
    for src, data in raw.items():
        for dst in data["links_out"]:
            backlinks.setdefault(dst, []).append(src)

    entries = [
        KnowledgeIndexEntry(
            topic=data["topic"],
            relpath=data["relpath"],
            canonical=data["canonical"],
            age_days=data["age_days"],
            tags=data["tags"],
            related=data["related"],
            links_out=data["links_out"],
            links_in=tuple(sorted(backlinks.get(canon_self, ()))),
            dangling=data["dangling"],
        )
        for canon_self, data in raw.items()
    ]
    return tuple(sorted(entries, key=lambda e: e.relpath))


def build_knowledge_index(
    knowledge_dir: Path, *, now: float | None = None
) -> tuple[KnowledgeIndexEntry, ...]:
    """Scan ``knowledge_dir`` and return one entry per ``*.md`` file, sorted by relpath.

    Every file is indexed, canonical or not — the "messy tail" (e.g. ``hook-matrix.md``,
    ``brand-notes.md``) is exactly as reachable as ``company.md``, just tagged ``canonical=False``.
    Links resolve **within this directory** (products are a separate tree — use
    :func:`build_profile_index` for a knowledge+products graph). Returns ``()`` if the directory
    doesn't exist (no knowledge yet is a valid, non-error state). ``now`` is injectable (epoch
    seconds) for deterministic freshness tests.
    """
    now = time.time() if now is None else now
    if not knowledge_dir.is_dir():
        return ()
    files = [
        (path.relative_to(knowledge_dir).as_posix(), path)
        for path in sorted(knowledge_dir.rglob("*.md"))
        if path.name not in _EXCLUDED_FILENAMES
    ]
    return _build_entries(files, now=now)


def build_profile_index(
    profiles_root: Path, profile: str, *, product: str | None = None, now: float | None = None
) -> tuple[KnowledgeIndexEntry, ...]:
    """The whole-profile graph: ``knowledge/`` **plus** product packs, cross-resolved.

    Relpaths are rooted at the profile dir (``knowledge/company.md``,
    ``products/vta/vta-how-to.md``) so a ``[[..]]`` in a product file can resolve to a knowledge
    topic and vice-versa. With ``product`` given, only that one product pack is included; otherwise
    every ``products/<slug>/`` is. Segments are guarded (``_safe_segment``) — traversal is the
    highest-risk tenant error. Only ever scans under ``profiles/<profile>/``; never ``content/``.
    """
    now = time.time() if now is None else now
    _safe_segment(profile, "profile")
    profile_dir = profiles_root / profile
    files: list[tuple[str, Path]] = []

    knowledge_dir = profile_dir / "knowledge"
    if knowledge_dir.is_dir():
        for path in sorted(knowledge_dir.rglob("*.md")):
            if path.name not in _EXCLUDED_FILENAMES:
                files.append((path.relative_to(profile_dir).as_posix(), path))

    products_root = profile_dir / "products"
    product_dirs: list[Path] = []
    if product is not None:
        _safe_segment(product, "product")
        cand = products_root / product
        if cand.is_dir():
            product_dirs.append(cand)
    elif products_root.is_dir():
        product_dirs = [p for p in sorted(products_root.iterdir()) if p.is_dir()]
    for pdir in product_dirs:
        for path in sorted(pdir.rglob("*.md")):
            if path.name not in _EXCLUDED_FILENAMES:
                files.append((path.relative_to(profile_dir).as_posix(), path))

    return _build_entries(files, now=now)


def find_in_index(index: tuple[KnowledgeIndexEntry, ...], topic: str) -> KnowledgeIndexEntry | None:
    """Look up ``topic`` in a built index — the retrieval step for a topic gtm-engine
    doesn't already know the canonical filename for."""
    for entry in index:
        if entry.topic == topic:
            return entry
    return None


# --- CLI (queryable surface; no runtime consumer wires this in yet) -----------


def _entry_dict(e: KnowledgeIndexEntry) -> dict:
    return {
        "topic": e.topic,
        "relpath": e.relpath,
        "canonical": e.canonical,
        "age_days": round(e.age_days, 1),
        "tags": list(e.tags),
        "related": list(e.related),
        "links_out": list(e.links_out),
        "links_in": list(e.links_in),
        "dangling": list(e.dangling),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.knowledge_index",
        description="Knowledge corpus index: tags, freshness, and the link/backlink graph.",
    )
    parser.add_argument("--profile", required=True, help="profile slug")
    parser.add_argument("--product", default=None, help="limit products to this one pack")
    parser.add_argument(
        "--knowledge-only",
        action="store_true",
        help="scan only knowledge/ (skip product packs)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--profiles-root", default=None, help="override profiles root")
    args = parser.parse_args(argv)

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else PathConfig.from_env().profiles_root
    )

    if args.knowledge_only:
        index = build_knowledge_index(profiles_root / args.profile / "knowledge")
    else:
        index = build_profile_index(profiles_root, args.profile, product=args.product)

    if args.json:
        print(json.dumps([_entry_dict(e) for e in index], indent=2))
        return 0

    print(f"\nprofile: {args.profile} — {len(index)} files")
    print(f"  {'C':<1} {'AGE':>5} {'#TAG':>4} {'#OUT':>4} {'#IN':>4} {'#DANG':>5}  RELPATH")
    n_dangling = 0
    for e in index:
        n_dangling += len(e.dangling)
        print(
            f"  {'*' if e.canonical else ' ':<1} {e.age_days:5.0f} {len(e.tags):4d} "
            f"{len(e.links_out):4d} {len(e.links_in):4d} {len(e.dangling):5d}  {e.relpath}"
        )
    if n_dangling:
        print(f"\n  {n_dangling} dangling ref(s) (resolve to no corpus file, not memory nodes):")
        for e in index:
            for d in e.dangling:
                print(f"    {e.relpath} → [[{d}]]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Duplicate-fact **advisory report** for the knowledge corpus (context-graph-lite, workstream B).

Surfaces *candidate* copy-paste drift across ``knowledge/`` + ``products/`` for a human to triage —
it never blocks, never edits, and is never CI-gated (no sync check, unlike
``docs/knowledge-usage.md``). Deliberately conservative: the corpus is full of the SAME term
appearing in many files for legitimately DIFFERENT purposes (verified case: a flagship product name is the
product definition in ``company.md``, the subject of a dozen ``guidance/*-alignment.md`` standards
crosswalks, a persona/positioning anchor in ``icp-personas.md``, AND the target of a deliberate
``≠ solo.io agentgateway`` collision guardrail — four different jobs for one string). A
keyword-presence check would flag all of them; this module instead compares whole prose **blocks**
and only flags pairs that are near-identical (same sentence copy-pasted), which the re-worded,
purpose-specific mentions above never are.

Stdlib-only. Mirrors the generate-a-doc shape of ``gtm_core.knowledge_usage`` and reuses
``gtm_core.knowledge_index`` for the corpus file list + the profile-vs-content boundary guard.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from . import knowledge_meta as km
from .knowledge_index import build_profile_index
from .paths import PathConfig

#: Blocks shorter than this are ignored — short lines (headers, single facts, list bullets like
#: "- ISO 27001") legitimately repeat verbatim across files without being copy-paste drift.
MIN_BLOCK_WORDS = 25

#: Near-duplicate threshold on SequenceMatcher's ratio (0..1). High by design: a re-worded,
#: purpose-specific mention of the same term should fall well below this; only a genuinely
#: copy-pasted (or near-verbatim-edited) block should cross it.
SIMILARITY_THRESHOLD = 0.85

#: Word-shingle size for CANDIDATE GENERATION. A brute-force all-pairs SequenceMatcher scan is
#: O(blocks²) — on a real tenant corpus that's ~3,600 blocks / ~6.6M pairs, which hangs for
#: minutes. Instead: bucket blocks by shared k-word shingles (an inverted index), so only block
#: pairs that already share a distinctive multi-word phrase are ever compared — the same technique
#: MinHash/near-dup detectors use, without the hashing approximation (this corpus is small enough
#: for exact set ops). SequenceMatcher then runs only on that short candidate list, for the final
#: human-readable ratio.
SHINGLE_SIZE = 6

#: Pre-filter on shingle-set Jaccard before paying for SequenceMatcher — cheap set ops, so this can
#: be looser than SIMILARITY_THRESHOLD; SequenceMatcher (a stricter, character-level metric) makes
#: the real accept/reject call.
_SHINGLE_JACCARD_MIN = 0.5

#: A block first/last line matching one of these (case-insensitive) is skipped — sanctioned,
#: intentionally-repeated boilerplate (disclaimers, standard headers) that isn't drift.
_BOILERPLATE_PREFIXES = re.compile(
    r"^(?:note|disclaimer|source|see also|related)\s*:", re.IGNORECASE
)

_FENCE = re.compile(r"```.*?```", re.DOTALL)
# Mirrors tests/linter/content_linter.py's _QUOTED_SPAN_RE — a verbatim-quoted span (the author
# citing a source exactly) is not drift between two files even if it appears in both.
_QUOTED_SPAN_RE = re.compile(r"\"[^\"]*\"|“[^”]*”")


@dataclass(frozen=True)
class Block:
    relpath: str
    lineno: int  # 1-indexed start line within the file
    text: str
    words: int


@dataclass(frozen=True)
class DupPair:
    a: Block
    b: Block
    ratio: float


def _blank(m: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


def _extract_blocks(relpath: str, text: str) -> list[Block]:
    """Split a knowledge file body into paragraph/list blocks for cross-file comparison.

    Strips frontmatter, fenced code, and quoted verbatim spans first — none of those should ever
    be compared as "prose drift". A block is a run of non-blank lines; consecutive `-`/`*` list
    items are grouped as one block so a repeated bullet list reads as a unit, not one flag per line.
    """
    _, body = km.parse_frontmatter(text)
    body = _FENCE.sub(_blank, body)
    body = _QUOTED_SPAN_RE.sub(lambda m: " " * len(m.group(0)), body)

    blocks: list[Block] = []
    current: list[str] = []
    start_line = 1
    lines = body.splitlines()
    for i, line in enumerate(lines, start=1):
        if line.strip():
            if not current:
                start_line = i
            current.append(line.strip())
        elif current:
            blocks.append(_finish_block(relpath, start_line, current))
            current = []
    if current:
        blocks.append(_finish_block(relpath, start_line, current))
    return [b for b in blocks if b.words >= MIN_BLOCK_WORDS]


def _finish_block(relpath: str, start_line: int, lines: list[str]) -> Block:
    joined = " ".join(lines)
    return Block(relpath=relpath, lineno=start_line, text=joined, words=len(joined.split()))


def _is_boilerplate(block: Block) -> bool:
    return bool(_BOILERPLATE_PREFIXES.match(block.text))


def _load_allowlist(path: Path | None) -> list[str]:
    """Fixed-string suppressions (`.debrandignore`-style): a flagged pair is dropped if either
    side's text contains one of these substrings. Optional — an absent/missing file means no
    suppressions, not an error."""
    if path is None or not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _shingles(text: str, k: int = SHINGLE_SIZE) -> frozenset[tuple[str, ...]]:
    """Lowercased word k-grams — the fingerprint used to bucket candidate blocks before the
    expensive character-level comparison. A block shorter than ``k`` words yields no shingles (and
    so is never bucketed with anything) — that's fine, it's already below MIN_BLOCK_WORDS in
    practice."""
    words = text.lower().split()
    if len(words) < k:
        return frozenset()
    return frozenset(tuple(words[i : i + k]) for i in range(len(words) - k + 1))


def _candidate_pairs(blocks: list[Block], shingle_sets: list[frozenset]) -> set[tuple[int, int]]:
    """Every (i, j) with i < j whose blocks share ≥1 shingle and come from different files — via
    an inverted index (shingle → block indices), so cost is proportional to how much text is
    ACTUALLY shared across the corpus, not to blocks² like a brute-force scan would be."""
    buckets: dict[tuple[str, ...], list[int]] = {}
    for i, shingles in enumerate(shingle_sets):
        for sh in shingles:
            buckets.setdefault(sh, []).append(i)

    candidates: set[tuple[int, int]] = set()
    for indices in buckets.values():
        if len(indices) < 2:
            continue
        for pos, i in enumerate(indices):
            for j in indices[pos + 1 :]:
                if blocks[i].relpath == blocks[j].relpath:
                    continue  # only cross-file duplication is drift
                candidates.add((i, j) if i < j else (j, i))
    return candidates


def find_candidates(
    profiles_root: Path,
    profile: str,
    *,
    product: str | None = None,
    allowlist_path: Path | None = None,
) -> list[DupPair]:
    """Scan one profile's committed knowledge (never ``content/`` — the index only ever walks
    ``profiles/<profile>/``) and return candidate near-duplicate block pairs across DIFFERENT
    files, ranked by similarity descending. Advisory only — callers must never treat this as a
    pass/fail gate."""
    index = build_profile_index(profiles_root, profile, product=product)
    profile_dir = profiles_root / profile
    allow = _load_allowlist(allowlist_path)

    blocks: list[Block] = []
    for entry in index:
        path = profile_dir / entry.relpath
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks.extend(b for b in _extract_blocks(entry.relpath, text) if not _is_boilerplate(b))

    shingle_sets = [_shingles(b.text) for b in blocks]

    pairs: list[DupPair] = []
    for i, j in _candidate_pairs(blocks, shingle_sets):
        a, b = blocks[i], blocks[j]
        sa, sb = shingle_sets[i], shingle_sets[j]
        if sa and sb:
            jaccard = len(sa & sb) / len(sa | sb)
            if jaccard < _SHINGLE_JACCARD_MIN:
                continue
        ratio = SequenceMatcher(None, a.text, b.text).ratio()
        if ratio < SIMILARITY_THRESHOLD:
            continue
        if any(term in a.text or term in b.text for term in allow):
            continue
        pairs.append(DupPair(a=a, b=b, ratio=ratio))

    return sorted(pairs, key=lambda p: p.ratio, reverse=True)


def render(pairs: list[DupPair], *, profile: str) -> str:
    lines = [
        "<!-- ADVISORY — NOT a gate, NOT CI-synced. Candidates for human triage only; a match here",
        "     may still be a legitimate re-mention (same term, different purpose) — verify before",
        "     consolidating. Regenerate: `python -m gtm_core.knowledge_dupcheck --profile "
        f"{profile}`. -->",
        "",
        f"# Knowledge duplicate-fact candidates — {profile}",
        "",
        f"**{len(pairs)} candidate pair(s)** at similarity ≥ {SIMILARITY_THRESHOLD:.0%} on blocks "
        f"≥ {MIN_BLOCK_WORDS} words. Cross-file only. For each pair: consolidate into one home and "
        "link the other to it (`related:` frontmatter or a `[[wikilink]]`), OR leave as-is if it's a "
        "legitimate purpose-specific re-mention (e.g. a standards-crosswalk subject, a persona "
        "anchor, or an intentional `X ≠ competitor-X` collision guardrail).",
        "",
    ]
    if not pairs:
        lines.append("_None found._")
        return "\n".join(lines) + "\n"

    for p in pairs:
        lines += [
            f"## {p.ratio:.0%} similar — `{p.a.relpath}:{p.a.lineno}` ↔ "
            f"`{p.b.relpath}:{p.b.lineno}`",
            "",
            f"**{p.a.relpath}:{p.a.lineno}**",
            "> " + p.a.text,
            "",
            f"**{p.b.relpath}:{p.b.lineno}**",
            "> " + p.b.text,
            "",
        ]
    return "\n".join(lines)


def default_report_path(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[1]
    return root / "docs" / "knowledge-dup-report.md"


def default_allowlist_path(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[1]
    return root / "tests" / "lint" / "knowledge-dup-allow.txt"


# --- CLI (advisory only — always exits 0) --------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.knowledge_dupcheck",
        description="Advisory near-duplicate report across one profile's knowledge corpus. "
        "Never blocks — always exits 0.",
    )
    parser.add_argument("--profile", required=True, help="profile slug")
    parser.add_argument("--product", default=None, help="limit products to this one pack")
    parser.add_argument(
        "--out", default=None, help="report path (default docs/knowledge-dup-report.md)"
    )
    parser.add_argument("--json", action="store_true", help="print candidates as JSON instead")
    parser.add_argument("--profiles-root", default=None, help="override profiles root")
    parser.add_argument("--allowlist", default=None, help="override the suppression file path")
    args = parser.parse_args(argv)

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else PathConfig.from_env().profiles_root
    )
    allowlist_path = Path(args.allowlist) if args.allowlist else default_allowlist_path()

    pairs = find_candidates(
        profiles_root, args.profile, product=args.product, allowlist_path=allowlist_path
    )

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "ratio": round(p.ratio, 3),
                        "a": {"relpath": p.a.relpath, "lineno": p.a.lineno, "text": p.a.text},
                        "b": {"relpath": p.b.relpath, "lineno": p.b.lineno, "text": p.b.text},
                    }
                    for p in pairs
                ],
                indent=2,
            )
        )
        return 0

    out_path = Path(args.out) if args.out else default_report_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(pairs, profile=args.profile), encoding="utf-8")
    print(f"[knowledge-dupcheck] {len(pairs)} candidate pair(s) — wrote {out_path} (advisory only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

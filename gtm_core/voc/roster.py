"""Turn the SEC-filings roster markdown into evidence records — the verification backlog.

The harvest produces ``enterprise-signals-<date>.md``: a few passages that were genuinely read,
followed by a roster of ~100 classified filers whose 10-K merely *contains* the searched phrase.
As prose that distinction survives on a reader's attention span. As records it survives
structurally: every roster row imports as ``verified=False`` and is therefore uncitable and
uncountable by :func:`gtm_core.voc.evidence.breadth` until someone reads the passage.

That is the whole point of this importer. It does not add evidence — it converts a markdown table
into a **worklist with a shape the breadth rule already refuses to count**.

Two things it deliberately does NOT do:

- **It never marks anything verified.** Reading a passage is an act; parsing a table is not.
- **It never assigns a demand claim.** Every row lands on ``ROSTER_CLAIM``, which asserts only what
  the search actually established: the phrase appears in this filer's 10-K. Deciding *which demand*
  a passage supports requires reading it, so that claim_id is set when the passage is verified.

CLI::

    python -m gtm_core.voc.roster --profile P                 # dry run: what would be imported
    python -m gtm_core.voc.roster --profile P --write         # append to the evidence store
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import quote

from ..paths import PathConfig, _safe_segment
from . import evidence as ev
from .collect import CUSTOMER_VOICE, VENDOR_VOICE

# The only claim a full-text-search hit establishes on its own. Not a demand claim — deliberately.
ROSTER_CLAIM = "agentic-ai-in-10k"

# What sits in `verbatim` for a row whose passage has not been retrieved. Explicit rather than
# empty, so a row can never be mistaken for a quote; `_build` refuses to pair it with verified=True.
UNREAD_PLACEHOLDER = "[unread] 10-K matched the searched agentic-AI phrase; passage not retrieved."

SOURCE_ID = "enterprise_filings"

_H2 = re.compile(r"^##\s+(.*)$")
_H3 = re.compile(r"^###\s+(.*)$")
_VERIFIED_ENTITY = re.compile(r"^\*\*(.+?)\*\*\s+—")
_HAS_WORD = re.compile(r"\w")

# Legal-form suffixes only. The roster's vendor paragraph writes short names ("Lumen Cloud")
# while the verified section writes filing names ("Lumen Cloud, Inc."); without this,
# the same company imports as backlog *and* exists as a verified record, i.e. two entities.
# Kept to legal forms deliberately — stripping words like "Technologies" would collide real
# distinct filers.
_LEGAL_SUFFIXES = frozenset(
    {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "llc", "lp", "plc"}
)


def _norm_entity(name: str) -> str:
    """Comparison key for a company name: casefolded, punctuation-free, legal form dropped."""
    tokens = re.sub(r"[^\w\s]", "", name.lower()).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return "".join(tokens)


def _cells(line: str) -> list[str] | None:
    """Split a markdown table row into stripped cells, or None if this is not a data row.

    Header and separator rows are rejected by inspecting the parsed cells rather than by a
    lookahead in the row pattern — a lookahead placed after ``\\s*`` can be defeated by
    backtracking (the engine simply matches zero spaces), which silently admits the header.
    """
    if not line.startswith("|"):
        return None
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 2 or not cells[0]:
        return None
    if cells[0].lower() == "company" or set(cells[0]) <= {"-", ":"}:
        return None
    return cells


def _edgar_url(entity: str) -> str:
    """The EDGAR full-text-search URL that reproduces this row.

    Deliberately the *search*, not a document: the roster never resolved per-filer document URLs,
    and inventing one would make an unread row look sourced.
    """
    return (
        "https://www.sec.gov/edgar/search/#/q=%22agentic%20AI%22&forms=10-K"
        f"&entityName={quote(entity)}"
    )


def _normalise_date(filed: str) -> str:
    """``date`` is a required field, and a record with an empty one is dropped on reload by
    ``evidence._coerce``. Vendor-side names carry no filing date in the roster paragraph, so an
    absent date becomes an explicit ``"unknown"`` rather than a fabricated or empty one."""
    filed = filed.strip()
    return filed if filed else "unknown"


def _build(entity: str, filed: str, speaker: str, tags: list[str], note: str) -> ev.EvidenceRecord:
    return ev.EvidenceRecord(
        claim_id=ROSTER_CLAIM,
        verbatim=UNREAD_PLACEHOLDER,
        url=_edgar_url(entity),
        date=_normalise_date(filed),
        entity=entity,
        speaker=speaker,
        source_id=SOURCE_ID,
        verified=False,  # never anything else — see the module docstring
        note=note,
        tags=tags,
    )


def parse(text: str) -> dict:
    """Parse a roster markdown document.

    Returns ``{"records": [...], "already_verified": [...], "sections": {...}}``. Entities that
    appear in the *Verified passages* section are reported but not imported as backlog rows — they
    have (or will have) their own record carrying a real quote and a real demand claim.
    """
    verified_entities: list[str] = []
    records: list[ev.EvidenceRecord] = []
    sections: dict[str, int] = {}

    section = ""  # current H2
    industry = ""  # current H3

    for raw in text.splitlines():
        line = raw.rstrip()
        if m := _H2.match(line):
            section = m.group(1).lower()
            industry = ""
            continue
        if m := _H3.match(line):
            # "### Financial services & payments — 20" → drop the trailing count.
            industry = m.group(1).split("—")[0].strip()
            continue

        if "verified passages" in section:
            if m := _VERIFIED_ENTITY.match(line):
                verified_entities.append(m.group(1).strip())
            continue

        if "buyer-side" in section:
            cells = _cells(line)
            if cells is None:
                continue
            entity, filed = cells[0], cells[1]
            if entity.startswith("*"):
                continue
            records.append(
                _build(
                    entity,
                    filed,
                    CUSTOMER_VOICE,
                    ["buyer-side", industry.lower()] if industry else ["buyer-side"],
                    "Buyer-side classification inferred from the filer's primary business, not "
                    "from reading the passage. Read one passage before citing.",
                )
            )
            sections[industry or "unclassified"] = sections.get(industry or "unclassified", 0) + 1
            continue

        if "vendor / platform-side" in section:
            if not line or line.startswith(("|", "#", ">")) or "subtotal" in line.lower():
                continue
            for name in (n.strip(" *·") for n in line.split("·")):
                # `---` rules and stray punctuation share this block with the name paragraph.
                if not name or not _HAS_WORD.search(name):
                    continue
                records.append(
                    _build(
                        name,
                        "",
                        VENDOR_VOICE,
                        ["vendor-side"],
                        "Vendor/platform-side filer: revealed roadmap, never customer demand. "
                        "Structurally excluded from breadth by speaker as well as by verification.",
                    )
                )
                sections["vendor-side"] = sections.get("vendor-side", 0) + 1

    # A filer with a read passage gets a real record elsewhere; don't also file it as backlog.
    verified_set = {_norm_entity(e) for e in verified_entities}
    kept = [r for r in records if _norm_entity(r.entity) not in verified_set]

    return {
        "records": kept,
        "already_verified": verified_entities,
        "sections": dict(sorted(sections.items())),
        "dropped_as_verified": len(records) - len(kept),
    }


def new_records(parsed_records: list[ev.EvidenceRecord], existing: list[ev.EvidenceRecord]) -> list:
    """Rows worth importing: not already present, and not for a filer that has already been read.

    Two exclusions, and the second is easy to miss. Keying only on
    ``(source, entity, claim)`` lets a filer whose passage was read under a *demand* claim still
    import under :data:`ROSTER_CLAIM`, because the claim ids differ — so a company that has been
    read reappears in a list whose entire meaning is "not yet read". The backlog must exclude any
    filer with a verified record from the same source, whatever claim that record sits on.
    """
    read = {(r.source_id, _norm_entity(r.entity)) for r in existing if r.verified}
    seen = {(r.source_id, _norm_entity(r.entity), r.claim_id) for r in existing}
    out = []
    for r in parsed_records:
        entity_key = (r.source_id, _norm_entity(r.entity))
        if entity_key in read:
            continue
        key = (*entity_key, r.claim_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def latest_roster(content_root: Path, profile: str) -> Path | None:
    prof = _safe_segment(profile, "profile")
    files = sorted((content_root / prof / "market-signals").glob("enterprise-signals-*.md"))
    return files[-1] if files else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.voc.roster",
        description="Import the SEC-filings roster into the evidence store as an unverified "
        "backlog. Never marks anything verified.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--roster", type=Path, default=None, help="Override the roster markdown.")
    parser.add_argument("--write", action="store_true", help="Append; default is a dry run.")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = PathConfig.from_env(repo_root=args.repo_root)
    try:
        roster = args.roster or latest_roster(cfg.content_root, args.profile)
        store = ev.store_path(cfg.content_root, args.profile)
    except ValueError as exc:
        raise SystemExit(f"[voc-roster] {exc}") from exc
    if roster is None or not roster.is_file():
        raise SystemExit(f"[voc-roster] no roster markdown found for profile {args.profile!r}")

    parsed = parse(roster.read_text(encoding="utf-8"))
    fresh = new_records(parsed["records"], ev.load(store))

    written = ev.append(store, fresh) if args.write else 0
    print(
        json.dumps(
            {
                "kind": "voc-roster-import",
                "roster": str(roster),
                "store": str(store),
                "parsed": len(parsed["records"]),
                "already_verified_entities": parsed["already_verified"],
                "dropped_as_verified": parsed["dropped_as_verified"],
                "new": len(fresh),
                "written": written,
                "dry_run": not args.write,
                "by_section": parsed["sections"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

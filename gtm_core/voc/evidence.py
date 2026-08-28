"""Per-item evidence records + the breadth rule that decides what may back a demand claim.

The collector (:mod:`gtm_core.voc.collect`) answers *which sources exist*. This module
answers the harder question the brief actually rests on: **for a given demand claim, how
many independent sources genuinely support it?**

Two filters apply, and both are enforced here in code rather than left to prose:

1. **Speaker** — only ``customer-voice`` may count. A spec proposal (``standards-voice``)
   is a leading indicator; a competitor changelog (``vendor-voice``) is revealed roadmap.
   Neither is evidence that a customer wants something.
2. **Verification** — a record whose passage has not actually been read is
   ``verified: false`` and can never count. This exists because the SEC-filings roster
   ships 169 filers of which only a handful have a read passage: a full-text-search hit
   means *the phrase appears*, not *this company expressed this need*.

Breadth counts **distinct sources**, not records: ten quotes from one 10-K are one source.

CLI::

    python -m gtm_core.voc.evidence --profile P                  # summarize the store
    python -m gtm_core.voc.evidence --profile P --claim "..."    # breadth for one claim
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from ..paths import PathConfig, _safe_segment
from .collect import counts_toward_breadth

# Confidence rubric — a function of how many INDEPENDENT customer-voice sources corroborate.
# Deliberately conservative: a single source is never more than "weak", however emphatic it is.
CONFIDENCE_BANDS: tuple[tuple[int, str], ...] = (
    (3, "strong"),
    (2, "moderate"),
    (1, "weak"),
    (0, "unsupported"),
)


@dataclass(frozen=True)
class EvidenceRecord:
    """One quotable piece of evidence.

    ``verified`` means *a human or an agent actually read this passage*. A search hit
    alone is ``verified=False`` — recorded so it can be worked through later, but never
    counted toward breadth and never citable in the brief.
    """

    claim_id: str
    verbatim: str
    url: str
    date: str
    entity: str
    speaker: str
    source_id: str
    verified: bool = False
    note: str = ""
    tags: list[str] = field(default_factory=list)

    def is_citable(self) -> bool:
        """Whether this record may be quoted in the brief and counted toward breadth."""
        return self.verified and counts_toward_breadth(self.speaker)


def _coerce(raw: dict) -> EvidenceRecord | None:
    """Build a record from a raw dict, tolerating unknown keys. Returns None if the
    required fields are missing — a malformed line must never silently become evidence."""
    required = ("claim_id", "verbatim", "url", "date", "entity", "speaker", "source_id")
    if not all(isinstance(raw.get(k), str) and raw.get(k) for k in required):
        return None
    tags = raw.get("tags")
    return EvidenceRecord(
        claim_id=raw["claim_id"],
        verbatim=raw["verbatim"],
        url=raw["url"],
        date=raw["date"],
        entity=raw["entity"],
        speaker=raw["speaker"],
        source_id=raw["source_id"],
        verified=bool(raw.get("verified", False)),
        note=str(raw.get("note", "")),
        tags=[t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
    )


def store_path(content_root: Path, profile: str) -> Path:
    """Append-only evidence store for *profile*."""
    prof = _safe_segment(profile, "profile")
    return content_root / prof / "plans" / "market-intelligence" / "evidence.jsonl"


def load(path: Path) -> list[EvidenceRecord]:
    """Read the store. Missing file → empty list; malformed lines are skipped, not fatal."""
    if not path.is_file():
        return []
    out: list[EvidenceRecord] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if isinstance(raw, dict) and (rec := _coerce(raw)) is not None:
            out.append(rec)
    return out


def append(path: Path, records: Iterable[EvidenceRecord]) -> int:
    """Append *records* to the store. Returns how many were written."""
    records = list(records)
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
    return len(records)


def breadth(records: Iterable[EvidenceRecord], claim_id: str) -> int:
    """Number of **distinct citable sources** backing *claim_id*.

    Distinct by ``(source_id, entity)`` — ten quotes from one company's 10-K are one
    source, not ten. This is what stops a single loud filer from reading as consensus.
    """
    return len(
        {(r.source_id, r.entity) for r in records if r.claim_id == claim_id and r.is_citable()}
    )


def confidence(n_sources: int) -> str:
    """Map a breadth count to a confidence band."""
    for threshold, label in CONFIDENCE_BANDS:
        if n_sources >= threshold:
            return label
    return "unsupported"


def assess(records: Iterable[EvidenceRecord], claim_id: str) -> dict:
    """Full verdict for one claim: breadth, confidence, and what was excluded and why.

    The exclusion counts are not decoration — they are how the brief stays honest about
    *why* a claim is weak. "Three sources, but all unverified" is a different situation
    from "no sources at all", and the brief must be able to say which.
    """
    records = list(records)
    mine = [r for r in records if r.claim_id == claim_id]
    n = breadth(mine, claim_id)
    return {
        "claim_id": claim_id,
        "breadth": n,
        "confidence": confidence(n),
        "records_total": len(mine),
        "excluded_unverified": sum(
            1 for r in mine if not r.verified and counts_toward_breadth(r.speaker)
        ),
        "excluded_wrong_speaker": sum(1 for r in mine if not counts_toward_breadth(r.speaker)),
        "citable_entities": sorted({r.entity for r in mine if r.is_citable()}),
    }


def summarize(records: Iterable[EvidenceRecord]) -> dict:
    """Store-wide rollup — the verification-budget dashboard (PRD §8.2)."""
    records = list(records)
    claims = sorted({r.claim_id for r in records})
    by_speaker: dict[str, int] = {}
    for r in records:
        by_speaker[r.speaker] = by_speaker.get(r.speaker, 0) + 1
    verified = [r for r in records if r.verified]
    return {
        "kind": "voc-evidence-summary",
        "as_of": date.today().isoformat(),
        "records": len(records),
        "verified": len(verified),
        "unverified": len(records) - len(verified),
        "by_speaker": dict(sorted(by_speaker.items())),
        "claims": {c: assess(records, c) for c in claims},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.voc.evidence",
        description="Summarize the evidence store, or assess breadth for one claim.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--claim", default=None, help="Assess a single claim_id.")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = PathConfig.from_env(repo_root=args.repo_root)
    try:
        path = store_path(cfg.content_root, args.profile)
    except ValueError as exc:
        raise SystemExit(f"[voc-evidence] {exc}") from exc

    records = load(path)
    result = assess(records, args.claim) if args.claim else summarize(records)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

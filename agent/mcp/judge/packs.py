"""A 1:1 outreach pack, read as the ``(rows, touches)`` the judge already scores.

Its own module rather than part of :mod:`agent.mcp.judge.scoring`: that file is the scoring
core (one implementation, two transports), and "what does a pack say" is a different question
answered by a different parser. Keeping them apart also keeps the seam small — everything here
is upstream of scoring, and nothing here knows how a row is scored.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Formats that are a *rendered* email for one named person rather than a merge template.
PACK_FORMATS = ("prospect-pack", "draft-outreach")

#: Front-block fields a pack carries that a CSV would otherwise supply.
_PACK_SEGMENT_RE = re.compile(r"\*\*Segment:\*\*\s*([^|\n]+)")
#: The why-now runs to the next `**Field:**`, a blank line, or EOF — NOT to end-of-line. A
#: wrapped clause carries its `[source | date]` bracket on the following line, and a
#: line-anchored capture drops exactly the citation the freshness check needs.
_PACK_WHYNOW_RE = re.compile(r"\*\*Why[- ]now[^:]*:\*\*\s*(.+?)(?=\n\*\*|\n\s*\n|\Z)", re.DOTALL)
_PACK_CITE_RE = re.compile(r"\[\s*(?P<source>[^|\]]+?)\s*\|\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\]")


def _seat_of(header: str) -> str:
    """ "Leonard Loo, Co-Founder (the technical founder: ...)" -> "Co-Founder".

    The persona line carries the name, the seat, and often a parenthetical explaining why the
    seat collapses at this company size. Only the seat is context the judge should weigh; the
    rest is drafting notes. A header with no comma has no name to strip (an unresolved seat
    reads "unresolved — team inbox ..."), and its first clause is the honest answer.
    """
    seat = header.split(",", 1)[1] if "," in header else header
    return seat.split("(", 1)[0].split(".", 1)[0].strip()


def pack_rows(spec: Path, text: str) -> tuple[list[dict], list, list[str]]:
    """A 1:1 outreach pack → the same ``(rows, touches, problems)`` a spec + CSV yields.

    A pack is **already rendered**: one named person, no merge fields, so there is no CSV to
    join and :func:`render` passes the body through unchanged. Everything downstream reads the
    *record*, not the spec, so nothing below this function knows the difference — the record's
    ``body_hash`` is over the literal bytes the person receives, which is a stronger identity
    than the template case can offer.

    Why it exists: until 2026-09-04 a pack handed to ``score_emails`` found no
    ``**Step N — Day D**`` headers, produced zero touches, and the batch reported **success
    having scored nothing**. The judge is the only surface that emits an adjudication record,
    and records are what feed ``defect-report``, ``disposal`` and the calibration holdout — so
    the 1:1 lane, one named person and the highest stakes per email, was the only lane with no
    path into the loop that is supposed to improve the next batch.

    Two things it deliberately does not do. It does not synthesise a recipient: a pack with no
    parsable email block is a problem, reported, never a silently empty batch. And its verdicts
    are not routable by ``lanes route`` — that router joins on pooled CSV rows, and a pack has
    none; these records feed the defect report and the ranked read.
    """
    from .render import Touch, detect_format, parse_draft_outreach_pack, parse_prospect_pack

    fmt = detect_format(text)
    parse = parse_prospect_pack if fmt == "prospect-pack" else parse_draft_outreach_pack
    _version, blocks, _meta = parse(text)
    if not blocks:
        return (
            [],
            [],
            [
                f"{spec.name} is neither a sequence spec (no touches) nor a readable "
                f"1:1 pack (tried {fmt})"
            ],
        )
    b = blocks[0]
    if "@" not in (b.to or ""):
        # `to` fell back to the company slug: the pack names no addressee. Scoring it would
        # write a record whose join key is a folder name.
        return [], [], [f"no **Contact:** address in pack {spec.name}"]
    why_now = (lambda m: " ".join(m.group(1).split()) if m else "")(_PACK_WHYNOW_RE.search(text))
    # The DOSSIER, not just the pack's one-line why-now. On 2026-09-05 two of six packs were
    # rejected for claims the dossier plainly supports — one for naming a customer "that
    # does not appear in the signal evidence" (the dossier quotes it verbatim from the company's
    # own site) and another for "no connection between the two companies" (the dossier records
    # that the product named in the pack is that company's own). Judging a summary and calling it
    # unsupported-claim verdicts at exactly the rate the summary is lossy.
    dossier = ""
    for cand in sorted(spec.parent.glob("dossier-*.md")):
        dossier = cand.read_text(encoding="utf-8", errors="replace")
        break
    # The why-now line ends `[<source> | <ISO date>]`. Only what the pack records VERBATIM is
    # copied onto the row. In particular `signal_source_url` is deliberately NOT filled: these
    # packs cite a bare host ("acme.example"), and `check_record` asks for an https URL naming
    # one source. Upgrading a host to a URL would manufacture a citation the pack never made —
    # and a source finding that fires honestly is worth more than a clean column.
    cite = _PACK_CITE_RE.search(why_now)
    row = {
        "email": b.to,
        "first": b.first,
        "company": b.company,
        "title": _seat_of(b.header),
        "segment": (lambda m: m.group(1).strip() if m else "")(_PACK_SEGMENT_RE.search(text)),
        "signal_clause": why_now,
        "signal_evidence": why_now,
        "dossier": dossier,
        "signal_observed": cite.group("date") if cite else "",
        "signal_subject": b.company,
    }
    return [row], [Touch(number=1, day=0, subject=b.subject, body=b.body)], []

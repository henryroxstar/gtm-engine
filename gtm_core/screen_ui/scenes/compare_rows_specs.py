from __future__ import annotations

from dataclasses import dataclass

# ── compare-track — the shape every "what you have vs what you need" beat shares ───────────────
#
# Four beats in act two and three make the same move: here is the common pattern, here is the one
# that holds. Built once with a lit-cell vocabulary rather than four bespoke cards (a filing
# cabinet, a waveform, a lock, a pair of boxes) — the point of the run is that it IS the same
# move, and four unrelated illustrations hide that while also reading as a slide deck.


# ── compare-rows — two ARTIFACTS, stacked, one answering the other ────────────────────────────
#
# WHY THIS REPLACED THE CELL TRACKS. The 2026-08-29 cut told four different arguments with the
# same picture: a row of ten little squares lighting up left to right, labelled variously THE
# WHOLE CABINET, ONE DRAWER, THE RECORDING, THE NOTE, INSTRUCTIONS and A HARD LIMIT. A square is
# not a drawer, a second of tape, or a rule, and using one shape for all six taught the viewer
# that the shape meant nothing. The operator read the result as filler ("can we consider nicer
# colors? and clearer?", 2026-08-30) and they were right: the cards were abstract where the
# argument is concrete.
#
# Every one of these beats is about a RECORD — a table of customers, a transcript, a log, a rule.
# So each panel is drawn as the artifact itself, with real rows in it. The one Act 1 graphic that
# worked was the record card, for exactly this reason, and this is that component generalised to
# the back half rather than a second design language competing with it.
#
# Rows cap at four per panel by arithmetic, not taste: payload type is floored at
# _PAYLOAD_MIN_H_FRAC and two panels of five rows plus labels and captions do not fit 1080 above
# that floor. A fifth row would have to be set below the floor, which is the illegibility defect
# this module already exists to prevent.


@dataclass(frozen=True)
class _Row:
    text: str
    #: normal | dim | strike | hot | gap
    state: str = "normal"
    #: right-aligned status chip, e.g. "ALLOWED" / "DENIED"
    tag: str = ""
    #: "" | "ok" | "deny" — picks the chip colour from the palette's good/warn fallbacks.
    tag_kind: str = ""


@dataclass(frozen=True)
class _Panel:
    label: str
    chip: str
    rows: tuple[_Row, ...]
    # NO per-panel caption. It was removed 2026-09-04: on all four shipped cards it restated the
    # panel LABEL and the burned caption between them, and it was drawn hard against the panel's
    # bottom border — the "infographics look like shit" note. Its slot is what pays for padding
    # from the token ladder (see `compare_rows`). The argument belongs to the label, the rows and
    # the VO; a fourth text layer competing for the same panel was never carrying its own weight.


@dataclass(frozen=True)
class _RowsSpec:
    prefix: str
    top: _Panel
    bottom: _Panel
    footnotes: tuple[str, ...] = ()
    attribution: str = ""


#: Three, not four. Four rows per panel — eight payload rows on a card — cannot coexist with a
#: burned caption at 16:9: eight rows at the payload floor plus two labels and two captions come
#: to 0.81h of a 0.62h band. The shipped cut drew them anyway and let the caption fall across the
#: bottom panel. Every card below therefore loses its least load-bearing row rather than its type
#: size; each cut is argued at the spec.
_MAX_PANEL_ROWS = 3

#: Every name, reference and number below is INVENTED. These are code, not tenant content, so the
#: third-party-PII rule applies in full: keep the shape of a real record, never the identity of
#: one (see CLAUDE.md, and tests/lint/pii_check.py which enforces it at four layers).

_ROWS_SCOPE = _RowsSpec(
    prefix="rows-scope",
    top=_Panel(
        label="WHAT THE AI AGENT COULD REACH",
        chip="EVERY ROW",
        rows=(
            # R. FONSECA dropped: a third named patient adds a name, not an argument. The count
            # row is what makes "the whole table" land, so it stays.
            _Row("A. OKONKWO   ·   repeat prescription", "hot"),
            _Row("M. HALVORSEN   ·   allergy notes", "hot"),
            _Row("+ 2,301 more patient records", "hot"),
        ),
    ),
    bottom=_Panel(
        label="WHAT THE CALL ACTUALLY NEEDED",
        chip="ONE ROW",
        rows=(
            _Row("A. OKONKWO   ·   repeat prescription", "hot"),
            _Row("M. HALVORSEN   ·   allergy notes", "dim"),
            _Row("+ 2,301 more patient records", "dim"),
        ),
    ),
)

_ROWS_EVIDENCE = _RowsSpec(
    prefix="rows-evidence",
    top=_Panel(
        label="THE RECORDING",
        chip="WHAT WAS SAID",
        rows=(
            # The 00:52 confirmation is dropped from both panels. It was a trailing pleasantry,
            # and cutting it ends BOTH panels on the 00:47 beat — the silence above, the DENIED
            # below — so the gap and the refusal are now the last thing read on each side.
            _Row("00:41   “can you change the address?”"),
            _Row("00:44   “of course. what’s the new one?”"),
            _Row("00:47   nothing here", "gap"),
        ),
    ),
    bottom=_Panel(
        label="THE DECISION RECORD",
        chip="WHAT WAS DECIDED",
        rows=(
            _Row("00:41   read customer record", tag="ALLOWED", tag_kind="ok"),
            _Row("00:44   write mailing address", tag="ALLOWED", tag_kind="ok"),
            _Row("00:47   read account balance", "hot", tag="DENIED", tag_kind="deny"),
        ),
    ),
)

_ROWS_IDENTITY = _RowsSpec(
    prefix="rows-identity",
    top=_Panel(
        label="CHECKED ONCE, AT THE START",
        chip="A LOCK",
        rows=(
            # "read her loyalty tier" is the row that goes, not "put a charge on the room": the
            # sequence has to ESCALATE to money by its last row, or the unchecked decisions read
            # as harmless. Caption follows the count down from three to two.
            _Row("00:02   answered the call", tag="CHECKED", tag_kind="ok"),
            _Row("00:44   moved her checkout", "dim"),
            _Row("01:07   put a charge on the room", "dim"),
        ),
    ),
    bottom=_Panel(
        label="CHECKED AT EVERY DECISION",
        chip="STAYS LIVE",
        rows=(
            _Row("00:02   answered the call", tag="CHECKED", tag_kind="ok"),
            _Row("00:44   moved her checkout", tag="CHECKED", tag_kind="ok"),
            _Row("01:07   put a charge on the room", tag="CHECKED", tag_kind="ok"),
        ),
    ),
)

#: Question 1's one concrete moment, which had no visual at all: the agent's own session record,
#: beside what its "verified" badge actually resolves to. The badge is the whole argument — it
#: looks like proof of a person and is proof of a handset — and it was being carried entirely by
#: Henry's face for 30s. "Spoofable" is the script's verified Claim 1, not a flourish.
_ROWS_CLAIM = _RowsSpec(
    prefix="rows-claim",
    top=_Panel(
        label="WHAT THE CALL WROTE DOWN",
        chip="THE SESSION",
        rows=(
            _Row('caller_name    "John"', tag="HE SAID SO", tag_kind="deny"),
            _Row("contact        JOHN M.", tag="VERIFIED", tag_kind="ok"),
            _Row("source         caller ID", "dim"),
        ),
    ),
    bottom=_Panel(
        label='WHAT "VERIFIED" MEANT',
        chip="CALLER ID",
        rows=(
            _Row("which handset dialled", tag="KNOWN", tag_kind="ok"),
            _Row("who was holding it", tag="UNKNOWN", tag_kind="deny"),
            _Row("network metadata, and spoofable", "dim"),
        ),
    ),
)

#: The two IMDA fragments are VERBATIM and deliberately separate. The source deck ran them as one
#: quote whose ellipsis crossed a sentence boundary, dropped "by the agent" from inside the
#: quotation marks, capitalised "prefer", and called the framework "voluntary" — a word IMDA never
#: uses. All four are corrected here; see the script's verification log. Do not re-join them.
_ROWS_LAYERS = _RowsSpec(
    prefix="rows-layers",
    top=_Panel(
        label="INSTRUCTIONS, IN PLAIN ENGLISH",
        chip="FAILS OPEN",
        rows=(
            _Row("“never read another customer’s record”"),
            _Row("asked a way nobody wrote a rule for", "hot", tag="IT BENDS", tag_kind="deny"),
        ),
    ),
    bottom=_Panel(
        label="A HARD LIMIT UNDERNEATH",
        chip="FAILS CLOSED",
        rows=(
            _Row("may this caller read this record?"),
            _Row("if it cannot tell", tag="NO", tag_kind="ok"),
        ),
    ),
    # ONE fragment, not two. The footer sits below the burned caption and there is room for two
    # lines before the safe-area floor — so the second fragment ("prefer deterministic rather
    # than non-deterministic limits") is dropped, not shrunk. It restated what the bottom panel
    # already shows: FAILS CLOSED, and "if it cannot tell → NO". The surviving fragment is the
    # one the panel cannot show, because it is about a control that sits UNDER the agent.
    # Verbatim and unaltered — fewer quotes, never edited ones.
    footnotes=(
        "“…impose access controls that prevent the tool from being called by the agent at all.”",
    ),
    attribution="IMDA · Model AI Governance Framework for Agentic AI v1.5 · §2.1.2 · non-binding",
)

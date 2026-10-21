from __future__ import annotations

import re

from ..role_vocabulary import DEFAULT_SEGMENTS as _DEFAULT_SEGMENTS
from ..role_vocabulary import load as _load_vocabulary
from .names import _has_letters

# --- company -------------------------------------------------------------

# Trailing legal-entity suffixes. Stripped so the name reads as a company a human would
# say out loud mid-sentence ("agents at Telavista Health move..."), not as a filing.
_LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(?:"
    r"inc|inc\.|incorporated|llc|l\.l\.c\.?|llp|lllp|ltd|ltd\.|limited|"
    r"corp|corp\.|corporation|co|co\.|company|plc|p\.l\.c\.?|"
    r"gmbh|ag|nv|n\.v\.?|bv|b\.v\.?|sa|s\.a\.?|sas|srl|s\.r\.l\.?|spa|s\.p\.a\.?|"
    r"pte|pte\.|pvt|pvt\.|pty|oy|ab|as|a\/s|aps|kk|k\.k\.?|"
    r"lp|l\.p\.?|sc|s\.c\.?|pc|p\.c\.?|pbc|p\.b\.c\.?|"
    r"bancorp|holdings|holding"
    r")\.?$",
    re.IGNORECASE,
)
# Trailing parenthetical qualifiers: "(a Halyard Company)", "(formerly MSyn)", "(f.k.a X)".
_PARENTHETICAL_RE = re.compile(r"\s*\((?:[^()]*)\)\s*$")
_TRADEMARK_RE = re.compile(r"[®™©℗]")
# Sentence-ending punctuation *inside* a styled brand ("All About You! Collaborative
# Health Care Services"). Dropped rather than truncated at — truncating would guess
# where the brand ends, and the glyph is the only part that misreads mid-sentence.
_SENTENCE_PUNCT_RE = re.compile(r"[!?]")
_COMPANY_NON_NAMES = frozenset({"n/a", "na", "none", "null", "unknown", "-", "--", "company"})
# A deal/shell-entity name captured where the operating company belongs, e.g.
# "B. Rowan Principal 150 Merger" (a SPAC vehicle, not the employer anyone would recognise).
# It renders mid-sentence — "Once agents at B. Rowan Principal 150 Merger move…" — and reads
# as obviously wrong to the recipient, which is the whole cost.
#
# Anchored deliberately narrowly: a trailing "Merger", or an explicit vehicle/shell token.
# A bare "merger" anywhere would false-flag real brands (Mergermarket), so it never matches
# mid-word or mid-name.
_TRANSACTION_ENTITY_RE = re.compile(
    r"(?:\bmerger$|\bmerger\s+(?:corp|sub|co)\b|\bacquisition\s+corp\b|"
    r"\bholdco\b|\bspac\b|\bshell\s+corp\b)",
    re.IGNORECASE,
)


# --- segment -------------------------------------------------------------

#: The canonical segment vocabulary, lowercase. Storage is lowercase and presentation
#: capitalises at the boundary: ``prospects_import._segment_from_size`` already emits
#: lowercase, and ``prospects_import`` already ``.capitalize()``s on the way out to the
#: HubSpot ``GTM_Segment`` column. Titlecase values in a stored CSV are that presentation
#: form leaking back into storage, which is the defect this normalises.
#:
#: Not a judgement about which segment a row belongs to — only about how the value is
#: spelled. ``unspecified`` is a real, kept value: a row whose segment nobody determined
#: is not a startup by default.
#:
#: ``builder`` joined 2026-09-04 with the third segment (agent factories). It was added here
#: because this tuple is the vocabulary EVERY downstream comparison normalises against — an
#: absent value does not fail loudly, it degrades: ``clean_segment`` returns the raw string
#: unchanged, ``check_row`` files a ``segment-unknown`` warning, and ``hook_coverage`` finds no
#: matrix cell and books the row as unassignable. A segment a tenant can select into a run mix
#: (``segment_mix`` in ``PROFILE.md``) but that this tuple does not know is a segment whose rows
#: are measured as noise. ``tests/lint/test_profile_targeting_invariants.py`` now pins the two
#: together so the next segment cannot be added in the profile alone.
#:
#: MOVED 2026-09-21 — the values now live in :mod:`gtm_core.role_vocabulary` beside the
#: persona and seat vocabulary, because they are the same KIND of fact: which segments a
#: tenant sells to is an ICP decision, and a tenant that had to edit this module to add one
#: could not. The paragraph above is exactly the argument for making it overridable: a
#: segment this tuple does not know degrades silently, so the tenant that needs a new one is
#: the tenant least able to see why its coverage reads zero. This name stays as the DEFAULT
#: view, re-exported unchanged so every existing importer is untouched; a run that knows its
#: profile should call :func:`segments_for` instead.
SEGMENTS = _DEFAULT_SEGMENTS


def segments_for(profile: str | None = None) -> tuple[str, ...]:
    """This profile's segment vocabulary, falling back to :data:`SEGMENTS`.

    ``profile=None`` resolves the session's bound profile from the environment, the same
    way :func:`gtm_core.role_vocabulary.load` does — so a caller that never learned about
    profiles keeps working and a caller that knows one gets the right answer.
    """
    return _load_vocabulary(profile).segments


# --- qualification score ---------------------------------------------------

#: Highest value a per-account QUALIFICATION score can plausibly take.
#:
#: NOT a tenant threshold, and deliberately not one: publish/Tier-A cut-offs are the profile's
#: (``knowledge/icp-personas.md``) and differ per segment. This is a structural bound on the
#: *shape* of the number — a qualification rubric is a small-integer verdict whose ceiling the
#: company-neutral machinery documents as 10-12 (``skills/prospect/references/gates-and-scoring.md``,
#: "Tier-A threshold - default ~70% of the rubric ceiling ... >=7 of 10, >=8 of 12"), with the heat
#: axis capped AT that ceiling rather than added above it. So 12 is the ceiling, not a headroom
#: figure.
#:
#: CHOSEN ON THE REAL DATA rather than by feel, 2026-09-04, over 1,822 published rows separated by
#: ``GTM_Rubric_Version`` into 1,073 known-good (qualification rubric, observed range 0-11) and 749
#: known-bad (spend ranking, observed range 4-53):
#:
#:     bound  catches bad        false-flags good
#:        12  741/749  (99%)     0 of 1,073
#:        14  735/749  (98%)     0
#:        20  643/749  (86%)     0
#:
#: 12 dominates: nothing looser catches more, and none of them cost a false positive. The 8 rows
#: no magnitude bound can reach scored 4-11 — a low spend ranking is numerically indistinguishable
#: from a mid qualification verdict, which is exactly why the column names were separated at source
#: rather than relying on this check.
#:
#: It exists because the column carrying that verdict is declared "numeric, no denominator", so
#: nothing objected when a *weighted spend ranking* (0-53, from ``icp-scoring.toml`` via the
#: enrichment queue) was written into it on the 2026-07-24 and 2026-08-11 bulk runs. 749 published
#: rows carry a spend ranking where a verdict belongs; one of those runs also invented a ``Tier C``
#: for the bottom of that distribution. The column collision itself is fixed at source (the queue
#: column is ``icp_backlog_score``); this is the second line, for any other scorer that reaches for
#: ``GTM_Score`` next.
#:
#: A tenant whose rubric genuinely exceeds this should raise the constant with its reason, not
#: silence the finding — the point is that a scale change becomes a decision someone makes.
QUALIFICATION_SCORE_MAX = 12


def clean_segment(segment: str) -> str:
    """Return the canonical lowercase spelling of a segment value.

    Case- and whitespace-only repair, deliberately: anything that is not already one of
    :data:`SEGMENTS` when lowercased is returned UNCHANGED, because mapping an unknown
    value onto a known one would be inventing a segment rather than normalising one.
    That keeps the function safe to run over every row on every sweep.

    Measured on 2026-08-21 across 87 prospect CSVs / 36,210 rows: ``Enterprise`` 17,318,
    ``Startup`` 9,204, ``startup`` 5,488, ``enterprise`` 3,941, ``unspecified`` 259 — a
    2:1 split with no majority convention, which is why a comparison against the hook
    matrix's segment axis cannot simply trust the stored string.
    """
    lowered = (segment or "").strip().lower()
    return lowered if lowered in SEGMENTS else (segment or "").strip()


def clean_company(company: str) -> str:
    """Return a company name safe to drop mid-sentence and to possessivize.

    Repairs: take the head segment of a pipe/bullet-delimited LinkedIn headline, drop
    trademark glyphs, drop a trailing parenthetical qualifier, strip a trailing legal
    suffix, and strip trailing sentence punctuation.

    Every step reverts if it would leave fewer than 2 characters or no letters, so a
    company legitimately *named* e.g. "Co" survives intact.
    """
    raw = (company or "").strip()
    if not raw:
        return ""

    out = raw
    # A scraped headline: "Canopy GBS | SAP Consulting | AI & Automation |" -> head only.
    if "|" in out or "•" in out or "·" in out:
        head = re.split(r"\s*[|•·]\s*", out)[0].strip()
        if len(head) >= 2 and _has_letters(head):
            out = head

    out = _TRADEMARK_RE.sub("", out).strip()
    candidate = re.sub(r"\s+", " ", _SENTENCE_PUNCT_RE.sub("", out)).strip()
    if len(candidate) >= 2 and _has_letters(candidate):
        out = candidate

    prev = None
    while prev != out:
        prev = out
        candidate = _PARENTHETICAL_RE.sub("", out).strip()
        if len(candidate) >= 2 and _has_letters(candidate):
            out = candidate
        candidate = _LEGAL_SUFFIX_RE.sub("", out).strip()
        if len(candidate) >= 2 and _has_letters(candidate):
            out = candidate

    # A dangling conjunction left by suffix removal: "McNeil &" -> "McNeil".
    candidate = re.sub(r"\s*[&+,]\s*$", "", out).strip()
    if len(candidate) >= 2 and _has_letters(candidate):
        out = candidate

    # Trailing sentence punctuation reads as a full stop mid-sentence.
    candidate = out.rstrip(" .!?;:,")
    if len(candidate) >= 2 and _has_letters(candidate):
        out = candidate

    out = re.sub(r"\s+", " ", out).strip()
    return out if _has_letters(out) else raw


def starts_with_article(company: str) -> bool:
    """True when ``company`` already begins with an article, so a template that writes
    "the {{Company}} stack" renders "the The Meridian Group stack".

    The article is part of the brand ("Silver Path"), so stripping it would be wrong.
    The template is what has to change: "the stack at {{Company}}" reads correctly whether
    or not the name carries its own article — and, unlike the possessive form, whether or
    not it ends in a sibilant.
    """
    return bool(re.match(r"^(the|a|an)\s", (company or "").strip(), re.IGNORECASE))


def ends_in_sibilant(company: str) -> bool:
    """True when ``company`` already ends in s/x/z, so a ``{{Company}}'s`` construction
    renders a double sibilant: "Gears & Vectors's stack", "Vantos's stack".

    Not a data defect — the *template* is what's wrong. A copy that says
    "the stack at {{Company}}" reads correctly for every name in the list.
    """
    return bool(re.search(r"[sxz]$", (company or "").strip(), re.IGNORECASE))

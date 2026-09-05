from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from .config import MIN_SEGMENT_FIT, MIN_SIGNAL_ATTESTATION, SIGNAL_TERM_NOISE_SHARE
from .declared import DeclaredCell
from .matrix import Matrix, _cells_equal

# --- does the LIST match the cell? ----------------------------------------------------
#
# Everything above this line checks a spec against the matrix, or specs against each
# other. Nothing checks a spec against the people it is actually sent to -- which is how
# four specs can declare four valid, distinct cells and still be aimed at the wrong grid.
# Measured 2026-08-21 on `agent-gateway-cross-org`: the builder spec declares
# `CTO / Founding Engineer x MCP / A2A in build`, a cell that exists, whose copy matches
# it, applied to a list that is 43/61 (70%) enterprise while that persona is in the
# matrix's STARTUP grid. `lint_hook_cell`, `argument_distinctness` and `persona_coverage`
# all pass on it.


def _norm_segment(value: str) -> str:
    """Canonical segment spelling for either side of the comparison.

    ``merge_hygiene.clean_segment`` owns the vocabulary; this module must not hold a
    second copy of it, because a comparison that normalises differently from the writer
    is a comparison that silently stops matching.

    The two sides arrive in different shapes and BOTH have to land on the same string:
    a CSV cell is the bare value (``"Enterprise"``), while the matrix's segment is its
    section heading verbatim (``"Enterprise (per ICP value-prop ranking)"``). So after
    the CSV-shaped normalisation fails, fall back to finding a canonical segment among
    the heading's own tokens. Without this the check reported 0% fit for all four specs
    on 2026-08-21 — including the architect list, which is 18/18 correctly aimed.
    """
    from ..merge_hygiene import SEGMENTS, clean_segment

    cleaned = clean_segment(value).lower()
    if cleaned in SEGMENTS:
        return cleaned
    tokens = set(re.split(r"[^a-z0-9]+", cleaned))
    found = [s for s in SEGMENTS if s in tokens]
    # Exactly one, or the heading is ambiguous and guessing would be worse than failing
    # the comparison openly.
    return found[0] if len(found) == 1 else cleaned


#: Words too common to distinguish one signal label from another.
_SIGNAL_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in into is it its of on or that the to
    with new first next own per via when where which while who your their our""".split()
)


@dataclass(frozen=True)
class SegmentFit:
    """How much of a spec's list sits in the segment its declared cell belongs to."""

    spec: str
    declared_segment: str
    counts: Counter = field(default_factory=Counter)

    @property
    def rows(self) -> int:
        return sum(self.counts.values())

    @property
    def matched(self) -> int:
        return self.counts.get(self.declared_segment, 0)

    @property
    def share(self) -> float:
        return self.matched / self.rows if self.rows else 0.0

    def failed(self, threshold: float = MIN_SEGMENT_FIT) -> bool:
        # An unknown declared segment is not a pass: it means the comparison could not
        # be made, and a check that passes by being unable to look is the failure mode
        # this whole module exists to close.
        return bool(self.rows) and self.share < threshold


@dataclass(frozen=True)
class SignalFit:
    """How much of a spec's list carries evidence for its declared signal."""

    spec: str
    signal: str
    rows: int = 0
    attested: int = 0
    terms: tuple[str, ...] = ()
    #: Which distinctive terms actually matched, most common first — the reason the
    #: number is what it is, so a WARN can be judged rather than merely obeyed.
    hits: Counter = field(default_factory=Counter)
    #: Terms dropped as non-discriminating because they matched more than
    #: :data:`SIGNAL_TERM_NOISE_SHARE` of the rows. Reported, never silently discarded: a
    #: label whose terms are ALL noise is unmeasurable, and the operator has to be able to
    #: see that rather than read a suspiciously healthy attestation number.
    noise_terms: tuple[str, ...] = ()
    #: Rows that attested via list-wide terms ONLY. The measure that matters: a label can
    #: yield a discriminating term that happens to match almost nothing, so counting terms
    #: says little and counting ROWS says everything.
    noise_only: int = 0
    #: Rows decided by their own recorded :data:`SIGNAL_COLUMN` rather than by term matching.
    #:
    #: A row that states which signal it attests is not a row this function needs to guess
    #: about, and guessing anyway produced a report that said two opposite things at once:
    #: on 2026-08-30 a spec whose single row recorded ``M&A / consolidation`` passed the
    #: ``cell-row-mismatch`` equality test and was simultaneously warned as ``0%``
    #: unattested, because the row's evidence says "acquisition" and the label says "M&A".
    #: The heuristic is the fallback for rows with nothing recorded — never an second
    #: opinion that overrides one.
    recorded: int = 0

    @property
    def inferred(self) -> int:
        """Rows whose attestation had to be guessed from prose."""
        return max(0, self.rows - self.recorded)

    @property
    def share(self) -> float:
        return self.attested / self.rows if self.rows else 0.0

    @property
    def noise_dominated(self) -> bool:
        """Every term this label yields is corpus-wide, so its attestation number is not
        evidence about this signal.

        **Reported, not subtracted.** Removing noise terms from the match was tried first and
        was wrong: a genuine 100% attestation and a spurious one are indistinguishable by
        frequency alone, so filtering silently zeroed a fixture where 57 rows really did all
        attest ``mcp``. Telling the two apart needs a background corpus this function does not
        receive. What it CAN do is say so — a high score carried entirely by list-wide words is
        a number the operator must not read as coverage, and the fix belongs in the matrix
        label (or a research pass), not in a threshold."""
        # Two earlier definitions of this were inert on the very draw it was written for, and
        # a positive control caught both. "Are all the terms noise" failed because the hiring
        # label also yields `coe` and `hiring` — clean terms that matched almost nothing.
        # "Did any clean term match at all" failed because exactly one row of nine matched
        # `coe`, so a single lucky hit vouched for eight rows that had none.
        #
        # Counting ROWS is what holds: 8 of 9 attesting rows rested on `agent`/`platform`
        # alone. A majority resting on list-wide words means the score describes the list, not
        # the signal, whatever the term inventory looks like.
        # Scoped to the rows this function actually had to guess about. A list where every
        # row records its signal has no term matching behind its number at all, so "the
        # terms are all list-wide" says nothing about it either way.
        if not self.inferred:
            return False
        return bool(self.attested) and self.noise_only > self.attested / 2

    def failed(self, threshold: float = MIN_SIGNAL_ATTESTATION) -> bool:
        # Nothing was inferred ⇒ nothing to warn about. A recorded row that attests the
        # WRONG signal is a `cell-row-mismatch` ERROR, which is the check that owns that
        # question; repeating it here as a WARN would double-report one defect and, worse,
        # contradict a passing equality test on rows that are simply correct.
        if not self.inferred:
            return False
        return bool(self.rows and self.terms) and self.share < threshold


def signal_terms(matrix: Matrix) -> dict[str, frozenset[str]]:
    """``{signal label -> the terms that best identify it}``.

    Derived from the matrix's own column labels, never from a term list held here: the
    hook matrix is the tenant's asset and ``gtm_core`` carries no tenant vocabulary
    (``CLAUDE.md``). Two rules, both learned from getting this wrong on 2026-08-21:

    **Acronyms beat words.** A label's UPPERCASE tokens (``MCP``, ``A2A``, ``AP2``) name
    the thing; its lowercase ones (``entering``, ``architecture``, ``build``) are
    connective English that matches any sentence. When a label has uppercase tokens,
    only those are used. The first version ignored casing and scored
    ``MCP / A2A in build`` on the word "build", reporting 7/61 attestation from rows
    that never mentioned an agent protocol.

    **Distinctiveness is scoped to the segment grid, not the whole matrix.** ``mcp`` and
    ``a2a`` appear in both an enterprise and a startup column; a matrix-wide uniqueness
    rule deletes them from both and keeps only the generic remainder — discarding the
    strongest terms available. Within one grid they are unique, which is the question
    that matters: did this list attest THIS column rather than a sibling column.
    """
    by_segment: dict[str, list[str]] = {}
    for cell in matrix.cells.values():
        by_segment.setdefault(cell.segment, [])
        if cell.signal not in by_segment[cell.segment]:
            by_segment[cell.segment].append(cell.signal)

    out: dict[str, frozenset[str]] = {}
    for labels in by_segment.values():
        tokenised = {}
        for label in labels:
            raw = [t for t in re.split(r"[^A-Za-z0-9]+", label) if len(t) >= 3]
            strong = {t.lower() for t in raw if t.isupper()}
            weak = {
                t.lower() for t in raw if not t.isupper() and t.lower() not in _SIGNAL_STOPWORDS
            }
            tokenised[label] = strong or weak
        seen = Counter(t for tokens in tokenised.values() for t in tokens)
        for label, tokens in tokenised.items():
            # A term shared with a sibling column in the SAME grid cannot tell the two
            # apart, so it is dropped -- but only against siblings, never matrix-wide.
            distinctive = frozenset(t for t in tokens if seen[t] == 1)
            out[label] = distinctive or frozenset(tokens)
    return out


def segment_fit(
    spec: str,
    declared: DeclaredCell | None,
    matrix: Matrix | None,
    segments: Counter,
) -> SegmentFit | None:
    """Compare a spec's recipients against the segment of the cell it declares.

    Returns ``None`` when the comparison cannot be made at all -- no declaration, no
    usable matrix, or a cell the matrix does not place in a segment. That is reported
    by its own existing finding (``hook-cell-missing`` / ``hook-cell-unknown``) rather
    than being restated here as a fit failure.
    """
    if declared is None or matrix is None or not matrix.ok:
        return None
    cell = matrix.find(declared.persona, declared.signal)
    if cell is None or not cell.segment:
        return None
    # Accumulate, never rebuild as a dict comprehension: normalisation MERGES keys
    # ("Enterprise" and "enterprise" both land on "enterprise"), and a comprehension
    # silently keeps whichever came last instead of summing them. That bug reported
    # builder as 60% startup on a list that is 70% enterprise -- the check inverting
    # the very defect it was written to catch.
    counts: Counter = Counter()
    for value, n in segments.items():
        if value:
            counts[_norm_segment(value)] += n
    return SegmentFit(
        spec=spec,
        declared_segment=_norm_segment(cell.segment),
        counts=counts,
    )


def _discriminating_terms(
    terms: frozenset[str], lowered_rows: list[str]
) -> tuple[frozenset[str], frozenset[str]]:
    """Split a signal's terms into the ones that discriminate and the ones that are noise.

    ``signal_terms`` derives its terms from the matrix's own column label, which works when
    the label is acronym-shaped (``MCP/A2A/AP2 entering architecture`` -> ``mcp, a2a, ap2``)
    and fails when it is ordinary English. ``Hiring for "agent platform" / AI CoE`` reduces to
    ``agent, coe, hiring, platform`` — and in an agent-identity prospect list, ``agent`` and
    ``platform`` appear nearly everywhere. Measured 2026-08-23 on a real per-cell draw: 8 of 9
    rows "attested" the hiring signal on those two words alone, while read as prose **not one
    was about hiring or a centre of excellence**. The 25% attestation threshold was satisfied
    entirely by noise, and the cell reported coverage it did not have.

    A cross-label check is not enough on its own — it catches ``platform`` and ``partner``,
    which appear in two column labels each, but misses ``agent``, because the other labels
    spell it ``agents`` and ``agentic``. Frequency in the corpus being matched is the measure
    that actually works, because it asks the right question: does this term separate these
    rows from the rest of the list, or does it describe the whole list?
    """
    if not lowered_rows:
        return terms, frozenset()
    ceiling = SIGNAL_TERM_NOISE_SHARE * len(lowered_rows)
    keep, noise = set(), set()
    for term in terms:
        pattern = re.compile(rf"\b{re.escape(term)}\b")
        matches = sum(1 for row in lowered_rows if pattern.search(row))
        (noise if matches > ceiling else keep).add(term)
    return frozenset(keep), frozenset(noise)


def signal_fit(
    spec: str,
    declared: DeclaredCell | None,
    matrix: Matrix | None,
    evidence: list[str],
    recorded_signals: Sequence[str] | None = None,
) -> SignalFit | None:
    """Count recipients whose recorded evidence attests the spec's declared signal.

    ``evidence`` is one joined string per row (clause + evidence + why-now): whichever
    field the researcher happened to write the signal into should count, since the
    question is whether the SIGNAL was observed, not which column holds it.

    ``recorded_signals`` is that row's own :data:`SIGNAL_COLUMN`, positionally aligned with
    ``evidence`` and blank where the row records nothing. A row that STATES which signal it
    attests is decided by equality against the declared label; only rows that state nothing
    fall back to term matching. Term matching is a documented-weak heuristic
    (see :func:`_discriminating_terms`), so letting it overrule a row's own recorded fact
    was never the intent — and in practice it contradicted the equality test outright: a
    row recording ``M&A / consolidation`` whose source says "acquisition" matches no term
    in the label and was reported as 0% attested while ``cell-row-mismatch`` passed it.

    Omitting the argument reproduces the pre-2026-08-30 behaviour exactly, which is what a
    list written before the column existed should get.
    """
    if declared is None or matrix is None or not matrix.ok:
        return None
    cell = matrix.find(declared.persona, declared.signal)
    if cell is None:
        return None
    recorded_signals = list(recorded_signals or [])
    recorded_signals += [""] * (len(evidence) - len(recorded_signals))
    terms = signal_terms(matrix).get(cell.signal, frozenset())
    # Noise is measured over the rows the heuristic actually judges. Including recorded
    # rows in the denominator would let a list that mostly records its signals dilute the
    # corpus-frequency test that protects the few rows still being guessed at.
    lowered_rows = [
        text.lower()
        for text, rec in zip(evidence, recorded_signals, strict=True)
        if not rec.strip()
    ]
    _, noise = _discriminating_terms(terms, lowered_rows)
    hits: Counter = Counter()
    attested = 0
    noise_only = 0
    recorded = 0
    for text, rec in zip(evidence, recorded_signals, strict=True):
        if rec.strip():
            # The row states its signal. Equality decides it, tolerantly of separator and
            # spacing, exactly as `cell-row-mismatch` compares the full cell.
            recorded += 1
            if _cells_equal(rec, cell.signal):
                attested += 1
            continue
        lowered = text.lower()
        matched = [t for t in terms if re.search(rf"\b{re.escape(t)}\b", lowered)]
        if matched:
            attested += 1
            hits.update(matched)
            if not set(matched) - noise:
                noise_only += 1
    return SignalFit(
        spec=spec,
        signal=cell.signal,
        rows=len(evidence),
        attested=attested,
        terms=tuple(sorted(terms)),
        hits=hits,
        noise_terms=tuple(sorted(noise)),
        noise_only=noise_only,
        recorded=recorded,
    )


# --- what a ROW itself has, derived rather than hand-typed ---------------------------
#
# Everything above this line either reads what a SPEC declares or infers a row's signal
# heuristically from free text (`signal_fit`'s term matching, proven noise-dominated on a
# real draw -- see `_discriminating_terms`'s docstring). Nothing turns a row's own recorded
# facts into its half of a cell. `HOOK_CELL_COLUMN` exists for exactly this and is unpopulated
# on every live row (measured 2026-08-23: 0/207), because writing it by hand duplicates the
# persona a title already implies and drifts from it the moment nobody keeps the two in sync.
#
# The persona half of a cell is already derivable (`persona_of(title)`) and the segment half
# is already an enumerated column, so the ONLY atom worth recording is the signal -- which
# `signal_fit` cannot reliably infer from prose. `derive_row_cell` turns
# `persona_of(title) x segment x SIGNAL_COLUMN` into a `Cell`, so `HOOK_CELL_COLUMN` becomes a
# derivation instead of a second hand-typed fact that can disagree with the title.


def signal_columns_for_segment(matrix: Matrix, segment: str) -> tuple[str, ...]:
    """The closed vocabulary of signal labels valid for one segment's grid.

    Scoped to the row's OWN grid, never matrix-wide: ``MCP/A2A/AP2 entering architecture``
    (enterprise) and ``MCP / A2A in build`` (startup) are different columns that happen to
    share vocabulary, and a row cannot attest a signal that only exists in the other grid's
    persona x signal space -- that is precisely the class of mis-declaration
    ``cell-segment-fit`` already measures at the spec level (see ``PENDING.md``, builder
    2026-08-21: a valid cell declared onto the wrong grid for 70% of its own list).
    """
    norm = _norm_segment(segment)
    out: list[str] = []
    for cell in matrix.cells.values():
        if _norm_segment(cell.segment) != norm:
            continue
        if cell.signal not in out:
            out.append(cell.signal)
    return tuple(out)

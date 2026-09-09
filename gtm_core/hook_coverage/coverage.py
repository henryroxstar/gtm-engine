from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .config import (
    EXEMPLARS,
    MIN_ARGUMENTS,
    MIN_RECIPIENTS,
    MIN_SEGMENT_FIT,
    MIN_SIGNAL_ATTESTATION,
)
from .declared import DeclaredCell
from .distinctness import PairOverlap, SharedPhrase
from .fit import SegmentFit, SignalFit
from .matrix import Matrix, persona_key_of_label

# --- the campaign-level report ------------------------------------------------------


@dataclass
class Coverage:
    """What one campaign's message axis actually looks like."""

    campaign: str = ""
    profile: str = ""
    matrix: Matrix | None = None
    #: (sequence_id, csv, spec) triples the campaign is built from.
    sources: list[dict] = field(default_factory=list)
    rows: int = 0
    seats: Counter = field(default_factory=Counter)
    personas: Counter = field(default_factory=Counter)
    #: Titles the provisional resolver could not place, with counts. Named, never
    #: hidden: this is the honest size of what the report cannot see.
    unresolved: Counter = field(default_factory=Counter)
    #: Rows whose title DOES resolve to a matrix persona, but not to any cell in the row's
    #: OWN segment grid -- e.g. a `CEO / Founder` title (Startup-only in the matrix) on an
    #: enterprise row. Keyed ``"{persona}/{segment}"``. Distinct from `unresolved` (no
    #: persona at all): together the two are every row this campaign's matrix cannot place,
    #: which is what `render()`'s "unassignable" line sums. This is a fact about the LIST
    #: and the MATRIX, not a defect in any one email, so it is reported here rather than as
    #: a linter rule -- fixing it is the operator's call (widen the matrix or the resolver,
    #: or drop the row), never something `gtm_core` can correct on its own.
    unassignable: Counter = field(default_factory=Counter)
    declared: dict[str, DeclaredCell | None] = field(default_factory=dict)
    #: spec -> the capability group its beat 3 argues from, ``""`` when undeclared. The
    #: distribution across this dict is what ``argument-monotone`` counts.
    capabilities: dict[str, str] = field(default_factory=dict)
    #: Drafted (never-staged) cells folded into this audit, by spec name. Reported so a
    #: reader can tell which findings are about live copy and which about a pilot.
    drafts: set[str] = field(default_factory=set)
    #: 1:1 Tier-A outreach packs folded into this audit, by key. Same purpose as
    #: ``drafts``: a pack is a manual artifact with no enrolled list, so a reader must be
    #: able to tell which findings are about it. ``hook_cell`` is a sequence-spec field and
    #: is NOT expected on a pack, so ``hook-cell-missing`` deliberately skips these.
    packs: set[str] = field(default_factory=set)
    #: spec -> (Counter of the cell each ROW recorded at research time, rows scanned).
    #: Empty counter with a non-zero count means the list predates ``hook_cell`` on the row.
    row_cells: dict[str, tuple[Counter, int]] = field(default_factory=dict)
    overlaps: list[PairOverlap] = field(default_factory=list)
    shared: list[SharedPhrase] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    #: Advisory findings that do NOT set ``failed``. Kept as a separate list rather than
    #: a level field on one list, so that every existing caller of ``findings`` keeps its
    #: meaning: everything in it still fails the run.
    warnings: list[str] = field(default_factory=list)
    #: Per-spec list-vs-cell fit, keyed by spec filename.
    segment_fits: dict[str, SegmentFit] = field(default_factory=dict)
    signal_fits: dict[str, SignalFit] = field(default_factory=dict)
    min_recipients: int = MIN_RECIPIENTS
    min_arguments: int = MIN_ARGUMENTS
    min_segment_fit: float = MIN_SEGMENT_FIT
    min_signal_attestation: float = MIN_SIGNAL_ATTESTATION

    @property
    def specs(self) -> int:
        return len(self.declared)

    @property
    def declared_count(self) -> int:
        return sum(1 for d in self.declared.values() if d is not None)

    @property
    def arguments(self) -> int:
        """Distinct arguments shipped: declared cells, else distinguishable specs.

        Before any spec declares a cell (today) the only evidence of an argument is
        the copy itself, so fall back to counting specs no sibling duplicates. That is
        deliberately generous — and it still returns 1 for this campaign.
        """
        ids = {
            d.argument_id or f"{d.persona} x {d.signal}"
            for d in self.declared.values()
            if d is not None
        }
        if ids:
            return len(ids)
        if not self.declared:
            return 0
        # Undeclared: fall back to the copy itself. A phrase carried verbatim by
        # EVERY spec means one scaffold and, on the evidence of the 2026-08-18 set,
        # one argument -- the case bag-of-word overlap scores as distinct.
        if any(p.count >= len(self.declared) for p in self.shared):
            return 1
        duplicated = {p.b for p in self.overlaps if p.same_argument}
        return max(len(self.declared) - len(duplicated), 1)

    @property
    def unresolved_rows(self) -> int:
        return sum(self.unresolved.values())

    @property
    def unassignable_rows(self) -> int:
        """Total rows no matrix cell can hold: unresolved persona + wrong-grid persona."""
        return self.unresolved_rows + sum(self.unassignable.values())

    @property
    def failed(self) -> bool:
        return bool(self.findings)


def persona_coverage(
    personas: Counter,
    declared: dict[str, DeclaredCell | None],
    matrix: Matrix | None = None,
    *,
    min_recipients: int = MIN_RECIPIENTS,
) -> list[str]:
    """Which personas hold enough recipients to matter and have no spec addressing them.

    One aggregated finding naming the personas, not one per persona — a campaign
    missing six personas should report a rate with exemplars, the
    :mod:`gtm_core.finding_budget` contract, rather than printing six walls. Findings
    are formatted ``"rule: subject — detail"`` so ``split_rule`` reads them unchanged.
    """
    addressed = set()
    for d in declared.values():
        if d is None:
            continue
        key = persona_key_of_label(d.persona)
        addressed.add(key or d.persona.lower())

    populated = [(p, n) for p, n in personas.most_common() if n >= min_recipients]
    missing = [(p, n) for p, n in populated if p not in addressed]
    if not missing:
        return []
    shown = ", ".join(f"{p} ({n})" for p, n in missing[:EXEMPLARS])
    more = f", +{len(missing) - EXEMPLARS} more" if len(missing) > EXEMPLARS else ""
    return [
        f"persona-unaddressed: {len(missing)} of {len(populated)} populated persona(s) "
        f"— no spec addresses {shown}{more} "
        f"(>= {min_recipients} recipients each)"
    ]

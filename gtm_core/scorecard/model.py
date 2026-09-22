"""Types for the scorecard engine. No I/O, no tenant facts — those live in ``scorecard.toml``.

The load-bearing type here is :data:`Result`, a **sum type**: a row comes back either
:class:`Scored` (a number, a tier, a derivation) or :class:`Categorised` (a category and the name
of the input that is missing), never a blend of the two. That is what makes "a low score that
actually means missing data" unrepresentable rather than merely discouraged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

#: Fields that measure OUR RESEARCH rather than the account. An axis may never award points for
#: one, and the loader refuses a card that tries (§R13 — a permanent ban lives in code, not prose).
#:
#: The litmus for adding one: *would this change if we researched harder without the company
#: changing?* If yes, it is a coverage proxy. On 2026-09-21 an ICP-fit axis was
#: ``len(description) >= 60`` and a scoring pass's mean tracked how many sales teams had listed a
#: company (1 team 9.79 -> 2 teams 11.31 -> 3 teams 11.94) rather than anything about the company.
#:
#: NOTE THE BOUNDARY. This denylist governs fields that AWARD POINTS. The same facts are
#: legitimate — required, even — as a **sufficiency gate**, where thin research produces a
#: category naming the unlock instead of a number. "We have not researched this" must change
#: whether the row is scorable; it must never change the score.
COVERAGE_PROXY: frozenset[str] = frozenset(
    {
        "dossier_length",
        "has_evidence_url",
        "team_count",
        "description_length",
        "row_completeness",
    }
)


class ScoreCardError(ValueError):
    """A card that cannot be trusted to score. Raised at LOAD, never mid-run.

    Subclasses :class:`ValueError` so callers can keep the one ``except (OSError, ValueError)``
    the rest of ``gtm_core`` already uses for "bad config or bad file".
    """


@dataclass(frozen=True)
class Axis:
    """One scoring axis. Exactly one of ``weights`` or ``components`` is populated.

    A **weights** axis reads one named input and looks its value up in a closed table — a
    vocabulary, so an unrecognised value has no weight and the row never scores.

    A **components** axis awards each named component independently; a component grants its
    points only on the literal boolean ``True``.
    """

    name: str
    max: int
    #: Weights axis only: the input field whose value is looked up in :attr:`weights`.
    input: str | None = None
    #: Closed vocabulary -> points. A value outside it is not a zero, it is a refusal.
    weights: Mapping[str, float] = field(default_factory=dict)
    #: Components axis only: component name -> points, granted on ``True`` alone.
    components: Mapping[str, float] = field(default_factory=dict)
    #: Component name -> the required input that must be satisfied before that component may be
    #: read at all. "No dated why-now" and "nobody looked for one" are different answers, and
    #: only the first is a legitimate zero.
    requires: Mapping[str, str] = field(default_factory=dict)
    #: Value (or component name) -> the operator-facing phrase used in the derivation.
    phrases: Mapping[str, str] = field(default_factory=dict)
    #: A gated sub-rubric that SCALES one label rather than sitting beside it as its own axis.
    modulated_by: str | None = None
    #: Which value of :attr:`input` the modulator applies to.
    modulates: str | None = None

    @property
    def reads(self) -> tuple[str, ...]:
        """Every field this axis awards points for — what the coverage-proxy ban governs."""
        return (self.input,) if self.input else tuple(self.components)


@dataclass(frozen=True)
class ScoreCard:
    version: str
    source: str
    ceiling: int
    #: Tier label -> inclusive floor, e.g. ``{"A": 80, "B": 65, "C": 50}``. Below the lowest
    #: floor is :attr:`bottom_tier`.
    tiers: Mapping[str, int]
    bottom_tier: str
    #: Checked IN THIS ORDER; the first unsatisfied one names the category. Order is a tenant
    #: decision because it decides which of two missing inputs the operator is told about.
    required_inputs: tuple[str, ...]
    axes: tuple[Axis, ...]
    #: Required-input name -> the category text a row gets when it is unsatisfied.
    categories: Mapping[str, str]
    #: Upstream-verdict token -> category text. Checked BEFORE any sufficiency check: an upstream
    #: exclusion is authoritative, and scoring the row would imply it is still in play.
    exclusions: Mapping[str, str] = field(default_factory=dict)

    def axis_reading(self, name: str) -> Axis | None:
        """The axis that awards points for ``name``, or gates a component on it."""
        for axis in self.axes:
            if name in axis.reads or name in axis.requires.values():
                return axis
        return None


@dataclass(frozen=True)
class Scored:
    """A row that had every required input. Carries a number and never a category."""

    score: int
    tier: str
    derivation: tuple[str, ...]

    def sentence(self) -> str:
        """The derivation as one plain sentence — what an operator reads to challenge a score.

        Phone-readable is the bar (PRD §6), so this is prose with ``;`` between axes, not JSON.
        """
        return f"Priority score {self.score}/100 (Tier {self.tier}). " + "; ".join(self.derivation)


@dataclass(frozen=True)
class Categorised:
    """A row that could not be scored. Carries a category and never a number."""

    category: str
    #: The required input the row is REPORTED against — the first unsatisfied one in the card's
    #: declared order, or the exclusion token. Names the unlock the operator acts on.
    missing_input: str
    #: EVERY unsatisfied required input, in declared order, not just the reported one.
    #:
    #: The reported category is a choice: with two inputs missing, declaration order picks which
    #: the operator is told about, and no row in the 2026-09-21 corpus records two gaps, so that
    #: order is declared rather than evidenced (PENDING SR2). Carrying the full list makes the
    #: choice visible instead of load-bearing — "classify the ICP" reads very differently when
    #: the row is also unresearched and out of market, and an operator who fixes only the named
    #: gap and re-runs would otherwise meet the next one with no warning.
    #: Empty for an exclusion, which is a verdict about the account, not a gap in our inputs.
    missing_inputs: tuple[str, ...] = ()


#: A row's outcome. ``Scored | Categorised`` — there is no third state and no overlap.
Result = Scored | Categorised


@dataclass(frozen=True)
class Batch:
    """The outcome of scoring a list of rows, with conservation asserted at construction.

    A row that vanishes from the denominator produces a confidently wrong distribution, so the
    invariant ``scored + categorised == input_count`` is checked here rather than assumed
    (test plan §4.4).
    """

    results: tuple[Result, ...]
    input_count: int

    def __post_init__(self) -> None:
        if len(self.results) != self.input_count:
            raise ScoreCardError(
                f"conservation violated: {self.input_count} rows in, {len(self.results)} results "
                "out — a row was dropped from the denominator"
            )

    @property
    def scored(self) -> tuple[Scored, ...]:
        return tuple(r for r in self.results if isinstance(r, Scored))

    @property
    def categorised(self) -> tuple[Categorised, ...]:
        return tuple(r for r in self.results if isinstance(r, Categorised))

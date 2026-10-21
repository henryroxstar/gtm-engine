"""Apply a card to a row: the sufficiency gate first, then the axes.

THE ORDER IS THE FEATURE. A row is asked "do you have every input this rubric needs?" before it
is asked "how good are you". A missing input produces a **category naming the unlock**, never a
low number, because the two are indistinguishable once they are both an integer and only one of
them is a finding about the account.

Everything that grants is a closed list. Everything else refuses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .model import Axis, Batch, Categorised, Result, ScoreCard, ScoreCardError, Scored
from .record import AGENT_EVIDENCE_INPUT, agent_evidence_from_record

#: The only value a boolean input may take to be satisfied. Not ``"true"``, not ``1``, not
#: truthiness — ``bool("false")`` is ``True``, which is how a string became a fact before.
_GRANTS = True

#: NOTE ON :mod:`.evidence`. This module used to call ``evidence.classify`` specially for the
#: agent-evidence axis. A mutation campaign (2026-09-22) showed the branch was an EQUIVALENT
#: MUTANT: deleting it changed no behaviour and no test could see the difference, because a
#: card's ``weights`` are already an exact-match closed list and give the same guarantee for
#: EVERY axis. Keeping a branch no test can distinguish is the thing this PRD argues against, so
#: it is gone. :mod:`.evidence` remains the canonical, separately-tested vocabulary a tenant card
#: should declare for an agent-evidence axis — the rule now lives in one place instead of two
#: that could drift.
#:
#: The branch that DOES exist for that axis (:func:`_from_record`) is a different rule, and it is
#: not equivalent: it replaces the axis input with the word the row's own ``signal_agent_kind``
#: supports, and refuses a supplied word that contradicts it (PH15, :mod:`.record`).


def score_row(card: ScoreCard, row: Mapping[str, Any]) -> Result:
    """One row's :class:`Scored` or :class:`Categorised` outcome. Never both, never neither."""
    if not isinstance(row, Mapping):
        raise ScoreCardError(f"row is not a mapping: {type(row).__name__}")

    excluded = _exclusion(card, row)
    if excluded is not None:
        return excluded
    row = _from_record(card, row)

    # Collect EVERY gap, report the first. Which of two missing inputs the operator hears about
    # is the card's declared order; which ones exist is a fact, and the row should carry both.
    gaps = tuple(n for n in card.required_inputs if not _satisfied(card, n, row.get(n)))
    if gaps:
        category = card.categories.get(gaps[0])
        if not category:
            raise ScoreCardError(f"no category text for required input {gaps[0]!r}")
        return Categorised(category=category, missing_input=gaps[0], missing_inputs=gaps)

    parts: list[str] = []
    total = 0.0
    for axis in card.axes:
        points, phrase = _axis_points(axis, row)
        total += points
        parts.append(f"{phrase} ({_trim(points)} of {_trim(axis.max)})")

    score = round(total)
    return Scored(score=score, tier=_tier(card, score), derivation=tuple(parts))


def score_rows(card: ScoreCard, rows: list[Mapping[str, Any]]) -> Batch:
    """Every row, in order. :class:`Batch` asserts nothing was dropped (test plan §4.4)."""
    results: list[Result] = []
    for index, row in enumerate(rows, start=1):
        try:
            results.append(score_row(card, row))
        except ScoreCardError as exc:
            # Name the row. A malformed row is refused loudly, never skipped — a skipped row
            # leaves the denominator and the distribution is confidently wrong.
            raise ScoreCardError(f"row {index}: {exc}") from exc
    return Batch(results=tuple(results), input_count=len(rows))


# --------------------------------------------------------------------------------------------


def _exclusion(card: ScoreCard, row: Mapping[str, Any]) -> Categorised | None:
    """An upstream verdict beats every sufficiency question.

    A partner, a strategy exclusion or a row that is not a company is not *missing* an input —
    it has been decided about, and putting a buyer score on it would imply it is still in play.
    An UNRECOGNISED token refuses too: a card that cannot name the exclusion has not been taught
    about it, and silently ignoring it would score the row.
    """
    token = row.get("exclusion")
    if token is None or (isinstance(token, str) and not token.strip()):
        return None
    if isinstance(token, str) and token in card.exclusions:
        return Categorised(category=card.exclusions[token], missing_input=token)
    raise ScoreCardError(
        f"unrecognised exclusion {token!r} — the card declares {sorted(card.exclusions)}. "
        "An exclusion this card cannot name must not be scored past"
    )


def _from_record(card: ScoreCard, row: Mapping[str, Any]) -> Mapping[str, Any]:
    """The row with its agent-evidence input taken from its signal record, when the card reads
    one. A card with no such axis is not this module's business, so its rows pass untouched."""
    if card.axis_reading(AGENT_EVIDENCE_INPUT) is None:
        return row
    return {**row, AGENT_EVIDENCE_INPUT: agent_evidence_from_record(row)}


def _satisfied(card: ScoreCard, name: str, value: Any) -> bool:
    """Is this required input answered? Two kinds, both closed.

    A **vocabulary** input is answered when its value is a key in the weights of the axis that
    reads it — so a value that is PRESENT but unrecognised ("Present", "Not ICP fit — US-only")
    is as unanswered as a missing one, and categorises rather than raising. A **boolean** input
    (a component, or a component's precondition) is answered only by the literal ``True``.
    """
    axis = card.axis_reading(name)
    if axis is not None and axis.input == name:
        return isinstance(value, str) and value in axis.weights
    return value is _GRANTS


def _axis_points(axis: Axis, row: Mapping[str, Any]) -> tuple[float, str]:
    if axis.weights:
        return _weighted(axis, row)
    return _componentwise(axis, row)


def _weighted(axis: Axis, row: Mapping[str, Any]) -> tuple[float, str]:
    value = row.get(axis.input)
    if not isinstance(value, str) or value not in axis.weights:
        # Unreachable via score_row (the gate ran first) but not unreachable via a direct call,
        # and a weight-lookup miss must never become a silent zero.
        raise ScoreCardError(f"axis {axis.name!r}: {value!r} is not one of {sorted(axis.weights)}")
    points = axis.weights[value]
    phrase = axis.phrases.get(value, value)

    if axis.modulated_by and value == axis.modulates:
        points, detail = _modulate(axis, row, points)
        phrase = f"{phrase}{detail}"
    return points, phrase


def _modulate(axis: Axis, row: Mapping[str, Any], base: float) -> tuple[float, str]:
    """Scale a label's points by a gated sub-rubric, rather than adding a second axis beside it.

    A label says which bucket the account is in; the sub-rubric says how strongly it sits there.
    Adding them would double-count the same judgement and let a weak member of a strong bucket
    outrank a strong member of a weaker one.
    """
    mod = row.get(axis.modulated_by)
    if not isinstance(mod, Mapping):
        raise ScoreCardError(
            f"axis {axis.name!r}: {axis.modulated_by!r} must be a mapping with 'subtotal' and "
            f"'max', got {type(mod).__name__}"
        )
    subtotal, ceiling = mod.get("subtotal"), mod.get("max")
    if not isinstance(subtotal, int) or not isinstance(ceiling, int) or ceiling <= 0:
        raise ScoreCardError(
            f"axis {axis.name!r}: {axis.modulated_by!r} needs integer 'subtotal' and a positive "
            f"'max', got {subtotal!r}/{ceiling!r}"
        )
    if subtotal > ceiling:
        raise ScoreCardError(
            f"axis {axis.name!r}: subtotal {subtotal} exceeds its own max {ceiling}"
        )
    segment = str(mod.get("segment", "")).strip()
    detail = f", required subtotal {subtotal}/{ceiling}{f' {segment}' if segment else ''}"
    return base * subtotal / ceiling, detail


def _componentwise(axis: Axis, row: Mapping[str, Any]) -> tuple[float, str]:
    """Each component grants on ``True`` alone; anything else is zero, which is the safe way to
    be wrong. A component gated on an unsatisfied precondition is refused, not zeroed."""
    points = 0.0
    said: list[str] = []
    for component, weight in axis.components.items():
        gate = axis.requires.get(component)
        if gate is not None and row.get(gate) is not _GRANTS:
            raise ScoreCardError(
                f"axis {axis.name!r}: component {component!r} needs {gate!r}, which is not "
                "satisfied — that is a category, not a zero"
            )
        granted = row.get(component) is _GRANTS
        points += weight if granted else 0.0
        key = component if granted else f"no_{component}"
        said.append(axis.phrases.get(key, key))
    return points, " + ".join(said)


def _tier(card: ScoreCard, score: int) -> str:
    for label, floor in sorted(card.tiers.items(), key=lambda kv: -kv[1]):
        if score >= floor:
            return label
    return card.bottom_tier


def _trim(value: float) -> str:
    """``12.0`` -> ``12``, ``11.111`` -> ``11.1``. The derivation is read by a person."""
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)

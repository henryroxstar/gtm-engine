"""Compare two runs of the same thing — and refuse when they are not two things.

TWO FAILURES THIS EXISTS TO STOP, both of which produce a confident, wrong, green result.

**A verifier that compares a file to itself.** During this feature's own history a comparison
helper was pointed at one path twice and reported a clean pass. Nothing was wrong with the diff;
the inputs were identical, so of course it matched. A comparison of a thing with itself is not
evidence about anything, so :func:`compare` raises rather than returning "no differences".

**A hardcoded expected count.** ``assert changed == 2006`` fired three false failures in one
session because the corpus legitimately grew. An expected value that is typed rather than derived
measures when the constant was written, not what the run did (§R14). :func:`expect_from` derives
the expectation from the input and says what it derived it from.

Stdlib-only, no I/O of its own: callers hand in already-loaded rows.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


class ComparisonError(ValueError):
    """The comparison cannot mean anything — refused before it can report a false pass."""


@dataclass(frozen=True)
class Difference:
    key: Hashable
    field: str
    before: Any
    after: Any

    def __str__(self) -> str:
        return f"{self.key}.{self.field}: {self.before!r} -> {self.after!r}"


@dataclass(frozen=True)
class Comparison:
    """What changed between two runs, with both denominators kept."""

    compared: int
    differences: tuple[Difference, ...]
    only_before: tuple[Hashable, ...]
    only_after: tuple[Hashable, ...]

    @property
    def identical(self) -> bool:
        return not (self.differences or self.only_before or self.only_after)

    @property
    def changed_keys(self) -> tuple[Hashable, ...]:
        return tuple(dict.fromkeys(d.key for d in self.differences))

    def summary(self) -> str:
        return (
            f"compared {self.compared} · changed {len(self.changed_keys)} "
            f"· only-before {len(self.only_before)} · only-after {len(self.only_after)}"
        )


def compare(
    before: Iterable[Mapping[str, Any]],
    after: Iterable[Mapping[str, Any]],
    *,
    key: Callable[[Mapping[str, Any]], Hashable],
    fields: Iterable[str],
    label: str = "comparison",
) -> Comparison:
    """Row-by-row diff on ``fields``, joined on ``key``.

    Refuses, rather than reporting a clean pass, when the two sides cannot be a real comparison:
    the same object passed twice, or two collections that are element-wise identical. A caller
    that genuinely wants to assert stability should compare a *recorded* earlier state against a
    freshly computed one — which is never the same object.
    """
    if before is after:
        raise ComparisonError(
            f"{label}: both sides are the SAME object — a thing always equals itself, so this "
            "comparison cannot fail and is not evidence"
        )
    left = list(before)
    right = list(after)
    if left == right:
        raise ComparisonError(
            f"{label}: the two sides are identical ({len(left)} rows) — either the same input "
            "was passed twice, or nothing was re-computed. Compare a recorded earlier state "
            "against a freshly derived one"
        )

    wanted = tuple(fields)
    if not wanted:
        raise ComparisonError(f"{label}: no fields to compare — this would pass vacuously")

    lhs = _by_key(left, key, f"{label}/before")
    rhs = _by_key(right, key, f"{label}/after")
    shared = [k for k in lhs if k in rhs]

    differences = tuple(
        Difference(key=k, field=f, before=lhs[k].get(f), after=rhs[k].get(f))
        for k in shared
        for f in wanted
        if lhs[k].get(f) != rhs[k].get(f)
    )
    return Comparison(
        compared=len(shared),
        differences=differences,
        only_before=tuple(k for k in lhs if k not in rhs),
        only_after=tuple(k for k in rhs if k not in lhs),
    )


def expect_from(
    rows: Iterable[Mapping[str, Any]], predicate: Callable[[Mapping[str, Any]], bool]
) -> int:
    """The expected count, DERIVED from the input — never typed into an assertion (§R14).

    ``assert changed == expect_from(rows, is_scored)`` survives the corpus growing. ``assert
    changed == 2006`` records the day someone ran it.
    """
    return sum(1 for row in rows if predicate(row))


def _by_key(
    rows: list[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], Hashable], where: str
) -> dict[Hashable, Mapping[str, Any]]:
    """Index by ``key``, refusing a key that is not an identity.

    A grouping key is a claim about identity: prove it before grouping on it. A key that
    collapses two rows into one silently halves the denominator — on 2026-08-30 a tally keyed on
    a row identity rather than a recipient identity turned 1298 records over 599 recipients into
    1298 singleton groups and reported a 0.0% flip rate for a pool where 36% had changed.
    """
    out: dict[Hashable, Mapping[str, Any]] = {}
    for position, row in enumerate(rows, start=1):
        k = key(row)
        if k in out:
            raise ComparisonError(
                f"{where}: key {k!r} appears twice (row {position}) — it is not an identity for "
                "these rows, and grouping on it would silently shrink the denominator"
            )
        out[k] = row
    return out

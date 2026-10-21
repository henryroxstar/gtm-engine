"""Stratified variant assignment — the missing half of an A/B.

**The problem this exists for.** ``gtm_core.cells`` already models the unit of analysis as
``<segment>:<seat>:<variant>`` and marks a pair of cells comparable only when they differ in
exactly one dimension. But nothing ever *assigned* a variant: a recipient's segment and seat
are properties of the person, and which spec they received followed from which list they were
on — so variant correlated with audience by construction. Every campaign manifest in this
repo says so in its own words::

    "The two lanes are a natural contrast, not a designed A/B... the signal-led lane also
     got the better accounts, so a difference measures argument AND fit together."

A comparison where the arms differ in audience *and* message cannot be read as either.

**What this does.** Assigns a variant **within** a fixed ``(segment, seat)`` stratum, so the
only thing that differs between arms at the same stratum is the message. That is what makes
``comparable_on`` mean something.

**Deterministic, not random.** A stable hash of ``(experiment, stratum, identity)``:

* **reproducible** — re-running the assignment produces the same arms, so an analysis can be
  recomputed months later from the same inputs rather than from a stored roll;
* **balanced within a stratum** rather than in expectation, which matters at the sizes this
  system actually runs (tens per cell, not thousands);
* **salted per experiment**, so a person is not permanently in arm A across weeks — the bias
  that would otherwise accumulate silently in a tenant with a stable pool.

This is **not** randomisation and it is **not** a holdout. There is no control arm here; a
held-back group is a separate decision that needs send volume this system does not yet have,
and calling a deterministic split "random" in a readout would be the more expensive error.

Leaf module by design — stdlib only, no imports from the rest of ``gtm_core``. The assigner
and anything auditing an assignment must read ONE rule, for the same reason
:mod:`gtm_core.lane_verdicts` is its own file.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence

#: Field separator for the hash input. A character that cannot appear in an email, a segment
#: or a seat, so ``("a|b", "c")`` and ``("a", "b|c")`` can never hash alike.
_SEP = "\x1f"


class AssignmentError(ValueError):
    """An assignment that could not be made in a way anyone could later read."""


def stratum_of(segment: str | None, seat: str | None) -> tuple[str, str]:
    """The comparison stratum for one recipient.

    ``None`` becomes ``"unknown"`` rather than being dropped. A recipient whose seat could not
    be resolved is still a real recipient who will receive a real email, and silently excluding
    them from assignment would mean the arms do not sum to the list — the denominator error
    that makes every downstream rate wrong by an amount nobody can see.
    """
    return (
        (segment or "unknown").strip().lower() or "unknown",
        (seat or "unknown").strip().lower() or "unknown",
    )


def assign_variant(
    identity: str,
    stratum: tuple[str, str],
    variants: Sequence[str],
    salt: str,
) -> str:
    """Pick one variant for ``identity`` within ``stratum``.

    ``identity`` is whatever uniquely names the recipient in the pool — an email, lowercased.
    ``salt`` is the experiment slug, so the same person lands differently in a later run.

    The stratum is part of the hash input, not just a grouping applied afterwards. That is the
    whole mechanism: two people in different strata get independent draws, so the split is
    balanced *inside* each stratum rather than only across the list as a whole.
    """
    if not variants:
        raise AssignmentError("no variants to assign — an experiment needs at least one arm")
    if len(set(variants)) != len(variants):
        raise AssignmentError(
            f"variants must be distinct, got {list(variants)}. Two arms with one name are one "
            f"arm wearing two labels, and every per-arm number would be a sum of both"
        )
    ident = (identity or "").strip().lower()
    if not ident:
        raise AssignmentError(
            "cannot assign without an identity — an unidentified recipient cannot be held in "
            "the same arm across touches, so the arm would change under them mid-sequence"
        )
    key = _SEP.join((salt, stratum[0], stratum[1], ident))
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return variants[int.from_bytes(digest[:8], "big") % len(variants)]


def assign_all(
    recipients: Sequence[tuple[str, str | None, str | None]],
    variants: Sequence[str],
    salt: str,
) -> dict[str, str]:
    """Assign every recipient. ``recipients`` is ``(identity, segment, seat)``.

    Returns ``{identity: variant}``. A duplicate identity keeps its first assignment rather
    than being re-drawn, mirroring ``gtm_core.cells.email_index``'s first-writer-wins: a
    person enrolled twice is an attribution problem to surface, never two arms to average.
    """
    out: dict[str, str] = {}
    for identity, segment, seat in recipients:
        ident = (identity or "").strip().lower()
        if not ident or ident in out:
            continue
        out[ident] = assign_variant(ident, stratum_of(segment, seat), variants, salt)
    return out


def balance(
    assignments: dict[str, str],
    recipients: Sequence[tuple[str, str | None, str | None]],
) -> dict[tuple[str, str], Counter]:
    """Per-stratum arm counts — what a reviewer should look at before a send.

    A stratum whose arms are lopsided is not a bug in the hash; at small n it is what a fair
    split looks like. The number worth acting on is a stratum where one arm is EMPTY, because
    that stratum silently contributes to one arm's total and to no comparison at all.
    """
    out: dict[tuple[str, str], Counter] = {}
    for identity, segment, seat in recipients:
        ident = (identity or "").strip().lower()
        if ident not in assignments:
            continue
        out.setdefault(stratum_of(segment, seat), Counter())[assignments[ident]] += 1
    return out


def collapsed_strata(
    balances: dict[tuple[str, str], Counter], variants: Sequence[str]
) -> list[tuple[str, str]]:
    """Strata where at least one arm got nobody — the ones that can carry no comparison.

    Named rather than counted, because the fix is per stratum: either accept that this seat
    is single-arm and exclude it from the readout, or source more of it before sending.
    """
    return sorted(s for s, counts in balances.items() if any(counts[v] == 0 for v in variants))

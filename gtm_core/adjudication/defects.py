"""Defect-class normalisation — one spelling per class, one scope per class.

``Adjudication.defect_class`` is free text from the judge ("kebab-case name of the single
worst defect"). Measured on the 2026-09-01 sweep it came back in **36 spellings** for a
handful of classes: ``fact_creates_problem`` and ``fact-creates-problem``, ``wrong-person``
beside ``right_person``, three phrasings of "the frame does not fit the seat". Every
consumer that compared the raw string — :data:`~gtm_core.adjudication.disposal.TARGETING_DEFECTS`
membership, :func:`~gtm_core.adjudication.verdicts.novel_classes` — silently missed the
variants: 53 of 87 drops were counted as misfiled targeting defects when ~65 were.

This module is the single place a raw class becomes a canonical one, and the single place a
canonical class is assigned a **scope** — what kind of thing the defect is a property of,
which is what routing needs:

* ``argument`` — the copy aimed the wrong argument at a right account (fix the spec or the fact)
* ``contact`` — right account, wrong person (re-resolve the contact)
* ``account`` — the account itself is the problem (a human decides; never auto-routed)
* ``data`` — the research record is corrupt (fix upstream, not the copy)
* ``unknown`` — a class this map has never seen. Fail-safe direction: an unknown class is
  treated as recoverable (argument-like), **never** as account-scoped, because laundering a
  novel string into "do not contact" is the quiet way to lose a prospect.

Canonical spelling is ``snake_case``, matching :data:`gtm_core.eval_calibration.LABEL_FIELDS`
and :data:`~gtm_core.adjudication.disposal.TARGETING_DEFECTS`. Linter rule ids are kebab-case;
they normalise through the same function so a rule and a judge class compare equal.

The alias table is measured, not guessed: every entry names a spelling observed on disk.
A novel spelling is normalised (case, separators) but never *mapped* — it keeps its own
name so :func:`novel_classes` can still surface it as a rule candidate.
"""

from __future__ import annotations

import re

#: Canonical class → scope. Anything not here is ``"unknown"``. ``fact_supports_pitch`` and
#: ``bridge_depends_on_fact`` keep their own names (argument-scoped, so they route to
#: Repair) but are NOT folded onto ``fact_earns_its_place``: :data:`TARGETING_DEFECTS`
#: deliberately excludes them, and an alias here would widen "mis-aimed" by the back door.
#: They are, however, subtracted from the quality card — :mod:`gtm_core.messaging.card`
#: derives its question set from this map minus that measured-identical pair, because a
#: routing class and a question a human is asked are different things.
DEFECT_SCOPE: dict[str, str] = {
    "fact_earns_its_place": "argument",
    "fact_supports_pitch": "argument",
    "bridge_depends_on_fact": "argument",
    "frame_fits_seat": "argument",
    "right_person": "contact",
    "wrong_entity_type": "account",
    "corrupted_scrape": "data",
    # Added 2026-09-24 with the outbound fact registry: the body claims a capability
    # beyond what its claim's recorded `status` allows, or cites a figure no `measured`
    # proof supports. Argument-scoped — the account and the person are fine and the copy
    # is re-aimable, which is exactly why it must not read as account-scoped.
    "claim_within_status": "argument",
}

#: Observed spellings (already lower-cased, ``-``→``_``) → canonical class. Only SPELLING
#: variants fold (a negation is the judge naming the finding instead of the criterion);
#: a different criterion never does, however related — see ``DEFECT_SCOPE`` above.
_ALIASES: dict[str, str] = {
    # the fact axis: `fact_creates_problem` is the historical alias of the 2026-09-02
    # survivor; its negated spellings are the same class
    "fact_creates_problem": "fact_earns_its_place",
    "fact_creates_no_problem": "fact_earns_its_place",
    "fact_creates_no_problem_for_seat": "fact_earns_its_place",
    "fact_does_not_create_problem": "fact_earns_its_place",
    # the contact axis
    "wrong_person": "right_person",
    "wrong_seat": "right_person",
    "seniority_mismatch": "right_person",
    # the frame axis
    "frame_doesnt_fit_seat": "frame_fits_seat",
    "frame_does_not_fit_seat": "frame_fits_seat",
    "frame_misaligned": "frame_fits_seat",
    "frame_unclear": "frame_fits_seat",
    # the account axis
    "vendor_operator_role_confusion": "wrong_entity_type",
    "vendor_not_buyer": "wrong_entity_type",
    "account_fit": "wrong_entity_type",
}

_SEPARATORS = re.compile(r"[\s\-]+")


def normalize_defect_class(raw: str | None) -> str:
    """Canonical spelling of a judge (or linter) class. Empty in → empty out.

    ``"Fact-Creates-No-Problem"`` → ``"fact_earns_its_place"``; ``"cta-overclaim"`` →
    ``"cta_overclaim"`` (normalised, not mapped — it is nobody's alias).
    """
    cls = _SEPARATORS.sub("_", (raw or "").strip().lower()).strip("_")
    return _ALIASES.get(cls, cls)


def defect_scope(raw: str | None) -> str:
    """``argument`` / ``contact`` / ``account`` / ``data`` / ``unknown`` for a raw class."""
    cls = normalize_defect_class(raw)
    return DEFECT_SCOPE.get(cls, "unknown") if cls else "unknown"

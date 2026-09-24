"""The quality card — one question set; every surface declares a SUBSET of it.

Three surfaces ask a human or a model whether a rendered outbound email is any good: the
operator's blind labeling sheet (:mod:`gtm_core.build_eval_sheet`), the judge rubric
(:mod:`agent.mcp.judge.rubric`), and the label files whose schema
:mod:`gtm_core.eval_calibration` owns. Until this module existed they carried three
different lists, in two spellings, and nothing compared them.

**The card is the MEASURED canonical set, not a typed one.** :data:`CARD` is derived from
:data:`gtm_core.adjudication.defects.DEFECT_SCOPE` — every canonical defect class the
pipeline routes on — minus the pair the label side merged away after measuring them
(:data:`_KAPPA_MERGED`). Nothing here types a count, and no doc may quote one: a doc that
wants the card's size cites this module and lets the number be printed.

**Each surface declares its own subset, and the subsets must cover the card.** A surface
may ask fewer questions than the card holds — the judge cannot check a claim's registry
status from the rendered body, and the generic lane has no fact to ask about — but it may
never ask a question the card does not hold, and a card question NO surface asks is an
orphan, not a subset. Both properties are tested in
``tests/unit/test_messaging_card.py``; the union check is what turns "we forgot to wire it"
into a red test instead of a silently unasked question.

Stdlib only, and no egress (§R6): the card is a vocabulary, not an I/O surface.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..adjudication.defects import DEFECT_SCOPE

#: The two defect classes that are NOT separate card questions, and what they fold into.
#:
#: MEASURED, not decided here. ``fact_creates_problem``, ``fact_supports_pitch`` and
#: ``bridge_depends_on_fact`` were three separate questions through 2026-09-01; over the
#: 28 labels of the second 2026-09-01 round Cohen's kappa between them was 1.00 / 0.84 /
#: 0.84, and across all 121 label records on disk exactly one had the three disagreeing.
#: A kappa of 1.00 says two of them were, on real operator labels, the same question. The
#: label side merged them (``gtm_core.eval_calibration._MERGED_LABEL_FIELDS``); this module
#: honours that merge rather than re-splitting it, because a card that asks one thing twice
#: measures the operator's patience, not the copy.
#:
#: ``DEFECT_SCOPE`` deliberately keeps both keys as their own *routing* classes (a judge
#: that returns one still routes to Repair), which is why they are subtracted here rather
#: than deleted there. Regression-guarded by
#: ``test_the_kappa_merged_pair_is_not_re_split``.
_KAPPA_MERGED: dict[str, str] = {
    "fact_supports_pitch": "fact_earns_its_place",
    "bridge_depends_on_fact": "fact_earns_its_place",
}

#: Every question on the card, in canonical spelling, derived from the defect map. Adding
#: a canonical defect class adds a card question by construction — which is the point: a
#: class the pipeline routes on but nobody ever asks about is the failure mode this
#: derivation removes.
CARD: tuple[str, ...] = tuple(k for k in DEFECT_SCOPE if k not in _KAPPA_MERGED)

#: What each card question ASKS, in one place. The judge rubric renders these verbatim;
#: the operator sheet renders its own shorter register beside the same keys, because a
#: sheet is read by someone who already knows the product and a prompt is not.
QUESTIONS: dict[str, str] = {
    "fact_earns_its_place": (
        "Does the fact this email opens on earn its place? Three things at once, because "
        "they were measured to be one: does it create a problem for THIS person in THIS "
        "seat; does the recipient's own recorded evidence establish what the body then "
        "claims; and does the sentence right after it actually depend on THIS fact rather "
        "than reading the same under any other true fact about any other company. A true, "
        "on-topic, correctly-attributed fact that fails any of those is the most common "
        "defect in this pipeline, and it is invisible to every regex."
    ),
    "frame_fits_seat": (
        "Would someone in this seat recognise this framing as their problem — not their "
        "colleague's, and not their vendor's?"
    ),
    "right_person": (
        "Is this person plausibly the one who would act on this? A great email to someone "
        "who cannot buy, cannot decide, and does not own the problem is a wasted send and "
        "a complaint risk."
    ),
    "wrong_entity_type": (
        "Is this company a buyer of this at all, or does it sell what we sell, regulate "
        "it, or already ship it? The copy can be perfect and the account still wrong. "
        "This is the only question about the COMPANY, and a no is durable — it "
        "disqualifies the account from every future send, not just this one."
    ),
    "corrupted_scrape": (
        "Is the recipient record this email renders actually a person at a company — or "
        "is it scraped junk showing through: a page headline where the company name "
        "should be, a bio fragment as a title, a placeholder or a bare initial as a "
        "name? A body can be flawless and still unsendable because the record under it "
        "is corrupt."
    ),
    "claim_within_status": (
        "Does the email stay inside what the sender can stand behind — no capability "
        "claimed beyond its recorded status, and no figure the proof does not support? "
        "A claim we cannot back is the one defect that costs more after the reply than "
        "before it."
    ),
}

#: The judge rubric's subset. **This set CHANGED on 2026-09-24**, from
#: ``(fact_earns_its_place, frame_fits_seat, right_person)`` to the tuple below —
#: ``corrupted_scrape`` was added because it is a card question no surface asked, and an
#: unasked card question is an orphan, not a subset. A verdict scored before that date was
#: produced by a different instrument and is **not comparable** with one scored after: pooling
#: the two would move a rate without anyone having changed the copy. That is why every record
#: now carries :func:`fingerprint` of the items it was actually scored against — see
#: ``agent.mcp.judge.rubric.rubric_version`` and ``Adjudication.rubric_version``.
#:
#: ``claim_within_status`` is deliberately ABSENT: the judge
#: reads the rendered body and the recipient context, never the fact registry, so it could
#: only guess at a claim's status — and the deterministic ``claim-status`` / ``proof-status``
#: linter rules are authoritative. A soft opinion sitting beside a hard gate is how a gate gets
#: argued with. ``wrong_entity_type`` is absent for the same shape of reason: it is an account
#: judgment with durable consequences, which the operator surface owns.
#:
#: **"Authoritative" was half true until 2026-09-24, and that is why the word is load-bearing
#: here.** Those rules ran on the render path only: the pack subcommand took no ``--profile``,
#: built no registry, and never called ``lint_derivation`` — so on a 1:1 pack the judge was
#: skipping a question on the strength of a gate that was not there. Both pack entry points now
#: run them (``tests/linter/outreach/rules_batch.py``). Deleting either wiring re-opens the gap
#: rather than changing a linter's coverage, because THIS comment is what stands in for the
#: question nobody asks. ``claim_within_status`` does still reach a human: it is on
#: :data:`LABEL_QUESTIONS`, so the blind labeling sheet asks the operator directly.
JUDGE_QUESTIONS: tuple[str, ...] = (
    "fact_earns_its_place",
    "frame_fits_seat",
    "right_person",
    "corrupted_scrape",
)

#: The generic lane's subset — DECLARED, not computed by pattern-matching the word "fact"
#: out of the judge's list. A generic-lane row carries no per-row signal by construction
#: (``gtm_core.lane_router``), so asking whether its opening fact earns its place asks it
#: to produce the thing its lane exists to do without. Everything else still applies: a
#: corrupt company name renders on a generic row exactly as on a signal-led one.
SEAT_ONLY_QUESTIONS: tuple[str, ...] = (
    "frame_fits_seat",
    "right_person",
    "corrupted_scrape",
)

#: What a label record may carry, and therefore what the blind sheet asks. Order is display
#: order: the account question stays last because it is the expensive one and reads as a
#: closing judgment.
LABEL_QUESTIONS: tuple[str, ...] = (
    "fact_earns_its_place",
    "frame_fits_seat",
    "right_person",
    "claim_within_status",
    "wrong_entity_type",
)

#: The sheet asks exactly what a label can store. Declared separately from
#: :data:`LABEL_QUESTIONS` rather than aliased to it, so that if the two ever diverge a
#: test says so by name instead of the sheet silently collecting an answer with nowhere
#: to live.
SHEET_QUESTIONS: tuple[str, ...] = LABEL_QUESTIONS

#: Canonical key → the spelling that surface stores it under. ONE entry, and it is not a
#: drifting variant: ``account_fit`` predates the card (2026-08-23), is the key in every
#: label file on disk and in ``schemas/email-eval-label.schema.json``, and exists because
#: every label sub-check must read as "Y is good" — ``wrong_entity_type`` reads the other
#: way round. It stays, and it is MAPPED: ``defects._ALIASES`` already folds it onto
#: ``wrong_entity_type``, so a label field and a judge defect class compare equal, and
#: ``test_card_keys_are_canonical_defects`` asserts the round trip rather than trusting it.
#: A new alias needs the same two-way proof; a new spelling with no entry here is a bug.
LABEL_SPELLING: dict[str, str] = {"wrong_entity_type": "account_fit"}


def label_fields(keys: Iterable[str]) -> tuple[str, ...]:
    """``keys`` in the spelling the label/sheet surface stores them under."""
    return tuple(LABEL_SPELLING.get(k, k) for k in keys)


def fingerprint(keys: Iterable[str]) -> str:
    """A short, stable id for the question set ``keys`` — the identity of an instrument.

    DERIVED, never typed, so it cannot go stale the way a hand-bumped version constant
    does: a question added, removed or **reworded** changes the id by itself, on the same
    commit, with nobody remembering to bump anything.

    Two deliberate properties:

    * **Order-insensitive.** The flip-rate control (PRD §3.2) scores the same rubric with
      its items reversed; that is one instrument asked two ways, and the payload's
      ``reverse_rubric`` already records which way. Making order change the id would split
      every flip-rate pair into two incomparable populations and destroy the control.
    * **Text-sensitive.** The wording IS the question. A rewritten item is a different
      measurement even under the same key, so the text is hashed with it. A typo fix
      therefore starts a new population — cheap, because this is a data column and not a
      gate, and the alternative is silently pooling answers to two different questions.
    """
    payload = "\n".join(f"{k}\n{QUESTIONS.get(k, '')}" for k in sorted(set(keys)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def questions_for(keys: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """``(key, question)`` pairs, in the order given. Raises on a key off the card."""
    out = []
    for key in keys:
        if key not in QUESTIONS:
            raise KeyError(f"{key!r} is not a card question; the card is {CARD}")
        out.append((key, QUESTIONS[key]))
    return tuple(out)

"""One quality card, three surfaces — and the three may only ever take a SUBSET of it.

The PRD asked for a card whose three surfaces (the operator's labeling sheet, the judge
rubric, the label schema) hold **equal** question sets. Measured, they do not and should
not: the judge cannot read the fact registry, and a generic-lane row has no fact to ask
about. So the property tested here is the one that actually holds — subset, canonical
spelling, and *coverage*:

* every surface's questions are a subset of ``card.CARD``;
* every key on every surface resolves through ``defects.normalize_defect_class`` to a card
  member, so an alias can never drift free of the class it names;
* the union of the three subsets IS the card — a question no surface ever asks is an
  orphan, not a subset, and the union check is what catches it.

Each test carries its negative control, because a card test that passes on a broken card
is worse than no card test: it is the same shape of failure as the three surfaces silently
disagreeing for three weeks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.mcp.judge import rubric
from gtm_core import build_eval_sheet, eval_calibration
from gtm_core.adjudication.defects import DEFECT_SCOPE, normalize_defect_class
from gtm_core.messaging import card

# --------------------------------------------------------------------- the surfaces, read live


def _surface_keys() -> dict[str, tuple[str, ...]]:
    """What each surface ACTUALLY asks, read from the consuming module.

    Deliberately not read from ``card`` itself: the thing under test is whether the three
    consumers took their questions from the card, so reading the card's own copy of their
    subsets would make every assertion below vacuous. Canonicalised through
    ``normalize_defect_class`` so the label surface's ``account_fit`` spelling compares
    equal to the class it names.
    """
    return {
        "judge": tuple(normalize_defect_class(k) for k, _ in rubric.RUBRIC_ITEMS),
        "sheet": tuple(normalize_defect_class(k) for k in build_eval_sheet.SHEET_FIELDS),
        "label": tuple(normalize_defect_class(k) for k in eval_calibration.LABEL_FIELDS),
    }


def _check_surfaces_are_subsets_covering_the_card(surfaces: dict[str, tuple[str, ...]]) -> None:
    """The whole contract, as one callable so the negative controls can invoke it too."""
    card_set = set(card.CARD)
    union: set[str] = set()
    for name, keys in surfaces.items():
        assert len(set(keys)) == len(keys), f"{name} asks a question twice: {keys}"
        assert set(keys) <= card_set, (
            f"{name} asks {sorted(set(keys) - card_set)}, which the card does not hold"
        )
        union |= set(keys)
    assert union == card_set, (
        f"card questions no surface asks: {sorted(card_set - union)}; "
        f"surface questions off the card: {sorted(union - card_set)}"
    )


# ------------------------------------------------------------------------ one card, three subsets


def test_card_is_one_tuple():
    """Each surface's set is a subset of ``CARD``; together they cover it exactly."""
    _check_surfaces_are_subsets_covering_the_card(_surface_keys())


def test_a_surface_that_invents_a_question_is_caught():
    """Negative control for ``test_card_is_one_tuple``.

    Monkeypatching a surface to ask something off the card must turn the contract red —
    otherwise the test above proves only that the assertion runs.
    """
    surfaces = _surface_keys()
    surfaces["judge"] = (*surfaces["judge"], "vibes_are_good")
    with pytest.raises(AssertionError, match="which the card does not hold"):
        _check_surfaces_are_subsets_covering_the_card(surfaces)


def test_no_surface_invents_a_question():
    """Union ⊆ CARD and CARD ⊆ union, stated as the two directions separately.

    The second direction is the one with teeth: a card question wired into nothing is
    invisible to every subset check, and reads as a shipped question until someone looks
    for its answers and finds none.
    """
    union = set().union(*_surface_keys().values())
    assert union <= set(card.CARD)
    assert set(card.CARD) <= union, (
        f"orphaned card questions (no surface asks them): {sorted(set(card.CARD) - union)}"
    )

    # Control: drop a surface's coverage of a question only it asks, and the second
    # direction fails while the first still passes.
    thinned = {k: v for k, v in _surface_keys().items() if k != "judge"}
    union_without_judge = set().union(*thinned.values())
    assert union_without_judge <= set(card.CARD)
    assert not set(card.CARD) <= union_without_judge


# -------------------------------------------------------------------------- canonical spellings


def test_card_keys_are_canonical_defects():
    """Every card key is a canonical defect class — no spelling variant anywhere.

    ``claim_within_status`` is on the card because it is now IN ``DEFECT_SCOPE`` (scope
    ``argument``), not because the card bolted it on: a card question the router cannot
    scope would route to ``unknown`` the moment the judge returned it.
    """
    for key in card.CARD:
        assert key in DEFECT_SCOPE, f"{key} is not a canonical defect class"
        assert normalize_defect_class(key) == key, f"{key} is itself an alias"
    assert DEFECT_SCOPE["claim_within_status"] == "argument"
    assert normalize_defect_class("Claim-Within-Status") == "claim_within_status"


def test_the_account_fit_alias_stays_and_is_mapped_both_ways():
    """The one alias, resolved explicitly: it STAYS, and it is mapped.

    ``account_fit`` is the label surface's spelling of ``wrong_entity_type``. It is not
    retired, for two measured reasons: it is the key in every label file on disk and in
    ``schemas/email-eval-label.schema.json`` (renaming it would invalidate labels this
    pipeline has not finished collecting), and every label sub-check must read as "Y is
    good", which ``wrong_entity_type`` does not. So it is declared in one place
    (``card.LABEL_SPELLING``) and folded in one place (``defects._ALIASES``), and this
    test asserts the two agree in both directions.
    """
    assert card.LABEL_SPELLING == {"wrong_entity_type": "account_fit"}
    for canonical, spelling in card.LABEL_SPELLING.items():
        assert canonical in card.CARD
        assert normalize_defect_class(spelling) == canonical
        assert spelling not in card.CARD, "the alias must not also be a card question"
    assert "account_fit" in eval_calibration.LABEL_FIELDS
    assert "wrong_entity_type" not in eval_calibration.LABEL_FIELDS

    # Control: an undeclared spelling is not silently tolerated — it normalises to itself
    # and so fails the subset check.
    assert normalize_defect_class("account_fits") == "account_fits"
    assert "account_fits" not in card.CARD


# ------------------------------------------------------------------- the kappa merge, pinned down


def test_the_kappa_merged_pair_is_not_re_split():
    """``fact_supports_pitch`` and ``bridge_depends_on_fact`` are NOT card questions.

    Measured, 2026-09-01/02: over the 28 labels of the second 2026-09-01 round, Cohen's
    kappa between ``fact_creates_problem``, ``fact_supports_pitch`` and
    ``bridge_depends_on_fact`` was **1.00 / 0.84 / 0.84**, and across all 121 label
    records on disk exactly one had the three disagreeing. A kappa of 1.00 means two of
    them were, on real operator labels, the same question. The label side merged them
    (``eval_calibration._MERGED_LABEL_FIELDS``); the judge rubric merged them the same
    day.

    They remain in ``DEFECT_SCOPE`` as routing classes, which is exactly how a future
    reader gets tempted to restore them as card questions ("the card is the defect
    classes, and there are seven of those"). This test is why they must not: a card that
    asks one thing three times measures the labeler's patience.
    """
    for key in ("fact_supports_pitch", "bridge_depends_on_fact"):
        assert key in DEFECT_SCOPE, "still a routing class"
        assert key not in card.CARD, f"{key} was re-split back onto the card"
        for name, keys in _surface_keys().items():
            assert key not in keys, f"{key} reappeared on the {name} surface"
    assert "fact_earns_its_place" in card.CARD, "the survivor must be on the card"

    # The merge map this module honours is the label side's, not a second copy of it.
    for old, survivor in card._KAPPA_MERGED.items():
        assert eval_calibration._MERGED_LABEL_FIELDS[old] == survivor


# -------------------------------------------------------------------- the generic-lane subset


def test_the_generic_lane_subset_is_declared_not_derived():
    """Seat-only scoring reads a DECLARED tuple, never "the card minus anything with
    'fact' in the name".

    A derived-by-substring subset silently changes meaning the next time a question is
    named — and the generic lane's exclusion is a measured, argued one (a generic row
    carries no per-row signal by construction, so the fact question asks it to produce the
    thing its lane exists to do without), not a spelling rule.
    """
    seat_only = card.SEAT_ONLY_QUESTIONS
    assert isinstance(seat_only, tuple)
    assert seat_only, "an empty seat-only rubric would score nothing"
    assert set(seat_only) < set(card.JUDGE_QUESTIONS), "must be a REAL subset of the rubric"
    assert set(seat_only) <= set(card.CARD)

    # It is what the judge actually renders for a generic-lane row, in order.
    assert [k for k, _ in rubric.rubric_for("generic")] == list(seat_only)
    assert [k for k, _ in rubric.rubric_for("signal")] == list(card.JUDGE_QUESTIONS)

    assert rubric.REQUIRES_SIGNAL == frozenset(card.JUDGE_QUESTIONS) - set(seat_only)


def test_the_generic_rubric_follows_the_declaration_not_the_spelling(monkeypatch):
    """Control for the test above, and the only one that discriminates.

    Today the declared tuple and "the judge's questions minus anything with 'fact' in the
    name" happen to agree, so equality proves nothing. Move the declaration to a set the
    substring rule could never produce, and the generic rubric must move with it.
    """
    by_substring = tuple(k for k in card.JUDGE_QUESTIONS if "fact" not in k)
    assert set(by_substring) == set(card.SEAT_ONLY_QUESTIONS), "premise of this control"

    monkeypatch.setattr(card, "SEAT_ONLY_QUESTIONS", ("fact_earns_its_place", "right_person"))
    assert [k for k, _ in rubric.rubric_for("generic")] == ["fact_earns_its_place", "right_person"]


def test_the_written_sheet_asks_the_sheets_declared_subset(tmp_path, monkeypatch):
    """The declaration has to reach the bytes the operator reads.

    ``SHEET_FIELDS`` and ``LABEL_FIELDS`` hold the same tuple today, so a call site that
    passed the wrong one would render an identical sheet and no assertion above would
    notice — a check that cannot discriminate is not a check. Moving the declaration to
    something the label surface would never produce makes the sheet move with it.
    """
    row = eval_calibration.GoldenRow(
        row_id="0" * 16,
        spec="spec.md",
        csv="rows.csv",
        touch=1,
        email="dana@brightpath.example",
        subject="a subject",
        body="a body",
        context={"title": "Managing Partner", "company": "Brightpath"},
    )
    monkeypatch.setattr(build_eval_sheet, "SHEET_FIELDS", ("right_person",))
    out = build_eval_sheet.write_golden_set("demo", [row], content_root=tmp_path)
    sheet = Path(out["sheet"]).read_text()

    assert "`right_person:` ___" in sheet
    for field in set(eval_calibration.LABEL_FIELDS) - {"right_person"}:
        assert f"`{field}:` ___" not in sheet, (
            f"the sheet asked {field}, which its declared subset does not hold — "
            "the renderer is reading a list other than SHEET_FIELDS"
        )


def test_the_card_question_texts_are_the_rubric_the_judge_renders():
    """The rubric is the card's wording, not a paraphrase of it."""
    for key, text in rubric.RUBRIC_ITEMS:
        assert text == card.QUESTIONS[key]
    rendered = rubric.rubric_text()
    for key in card.JUDGE_QUESTIONS:
        assert f"`{key}`" in rendered
    for key in set(card.CARD) - set(card.JUDGE_QUESTIONS):
        assert f"`{key}`" not in rendered, f"{key} reached a rubric that does not declare it"

    with pytest.raises(KeyError):
        card.questions_for(("not_a_card_question",))


# ------------------------------------------------------------- the judge's model/transport is NOT ours


def test_every_judge_record_carries_the_rubric_version_it_was_scored_by():
    """The confound, given a field.

    ``rubric`` stayed ``"full"`` across 2026-09-24, when ``corrupted_scrape`` joined the
    judge's question set — so pooling a holdout on ``rubric`` alone would mix answers to
    two different instruments and move a rate with nobody having changed a word of copy.
    Same class as ``backend`` and ``judge_batch``, which CLAUDE.md already calls "a visible
    confound"; this is the field that makes THIS one visible.
    """
    from agent.mcp.judge.scoring import build_record

    row = {"email": "dana@brightpath.example", "title": "Managing Partner", "lane": "signal"}
    common = {
        "spec_path": "spec.md",
        "csv_path": "rows.csv",
        "subject": "s",
        "body": "b",
        "touch_n": 1,
        "backend": "api",
        "judge_batch": 1,
        "repair_attempt": 0,
        "repaired": False,
    }
    rec = build_record(row, verdict=None, **common)
    assert rec.rubric_version == rubric.rubric_version("signal")
    assert rec.rubric_version, "an empty version is indistinguishable from a pre-2026-09-24 record"
    assert rec.to_dict()["rubric_version"] == rec.rubric_version

    # A row scored on a different question set records a different version — the two lane
    # scopes are two instruments, and `rubric` already says which, so this must agree.
    generic = build_record({**row, "lane": "generic"}, verdict=None, **common)
    assert generic.rubric_version != rec.rubric_version


def test_a_changed_rubric_yields_a_different_version(monkeypatch):
    """Negative control. A version that does not move when the questions move is worse
    than no version: it is a claim of comparability that is false.

    The patch target is ``RUBRIC_ITEMS`` — what the judge actually renders — not
    ``card.JUDGE_QUESTIONS``. ``rubric_version`` reads the rendered items on purpose, so it
    records the instrument that ran rather than the one the card currently declares; a
    module patched after import must not be able to make the two disagree.
    """
    before = rubric.rubric_version("signal")
    assert before == card.fingerprint(k for k, _ in rubric.rubric_for("signal"))

    monkeypatch.setattr(rubric, "RUBRIC_ITEMS", card.questions_for(("frame_fits_seat",)))
    assert rubric.rubric_version("signal") != before, "rubric_version ignored the change"

    # Rewording an item is a different question under the same key, and must move it too.
    same_keys = ("frame_fits_seat", "right_person")
    worded = card.fingerprint(same_keys)
    monkeypatch.setattr(card, "QUESTIONS", dict(card.QUESTIONS) | {"right_person": "Can they act?"})
    assert card.fingerprint(same_keys) != worded, "a rewritten question kept the old identity"


def test_the_version_is_order_insensitive_so_the_flip_control_survives():
    """The flip-rate control (PRD §3.2) scores the SAME rubric with its items reversed.

    If order changed the id, every flip-rate pair would split into two populations that
    look incomparable, and the control that exists to detect order-sensitivity would be
    destroyed by the field meant to protect comparisons. ``reverse_rubric`` in the judge
    payload already records which direction was asked.
    """
    assert card.fingerprint(card.JUDGE_QUESTIONS) == card.fingerprint(
        tuple(reversed(card.JUDGE_QUESTIONS))
    )
    assert rubric.rubric_text() != rubric.rubric_text(reverse=True), "the control still transforms"


def test_the_tally_surfaces_two_rubric_versions_rather_than_pooling_them(tmp_path):
    """Downstream posture, copied from the one next door — ``backends`` is reported per
    unit, not refused and not grouped away, so ``rubric_versions`` is too."""
    from gtm_core.adjudication import Adjudication, read_records, tally, write_records

    def _rec(version: str, verdict: str) -> Adjudication:
        return Adjudication(
            email="dana@brightpath.example",
            verdict=verdict,
            score=3,
            touch=1,
            row_id="r1",
            backend="api",
            rubric="full",
            rubric_version=version,
        )

    path = tmp_path / "adjudication.jsonl"
    write_records([_rec("aaaaaaaaaaaa", "send")], path)
    assert read_records(path)[0].rubric_version == "aaaaaaaaaaaa", "must survive a round trip"

    mixed = tally(
        {"run-1": [_rec("aaaaaaaaaaaa", "send")], "run-2": [_rec("bbbbbbbbbbbb", "drop")]}
    )
    row = mixed.rows[0]
    assert row.rubric_versions == ("aaaaaaaaaaaa", "bbbbbbbbbbbb"), (
        "the tally pooled two instruments without saying so"
    )
    assert row.to_dict()["rubric_versions"] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]

    # Control: one instrument across both runs reports one version, so the signal above is
    # the mix and not just "the field is populated".
    same = tally({"run-1": [_rec("aaaaaaaaaaaa", "send")], "run-2": [_rec("aaaaaaaaaaaa", "drop")]})
    assert same.rows[0].rubric_versions == ("aaaaaaaaaaaa",)


def test_the_rubric_change_did_not_touch_the_judges_model_or_transport():
    """Out of scope, and pinned so it stays that way.

    CLAUDE.md binds the judge to a Claude model for a **PII** reason, not a cost one: it
    reads rendered bodies carrying prospect names, titles and companies. A rubric edit is a
    change to the measurement; it must not become a change to which model sees that PII, or
    to the key-first / OAuth-fallback selection that decides how it is reached.
    """
    from agent.mcp.judge import scoring
    from gtm_core.models import resolve_model

    spec = resolve_model("judge")
    assert scoring._SPEC.role == spec.role
    assert scoring.JUDGE_MODEL == spec.model
    assert "claude" in spec.model.lower(), "the judge must stay on Claude (PII binding)"

    backend, reason = scoring.select_backend()
    assert backend in {"api", "sdk"}
    assert (backend == "api") == bool(spec.api_key()), "key-first selection must be unchanged"
    assert spec.api_key_env in reason

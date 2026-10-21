"""C5 — the sampling curve: how many drafts of each shot, priced before any of them is dispatched.

PRD test ids C5-T1 (an over-budget curve dispatches NOTHING), C5-T2 (a within-budget one dispatches
exactly n and meters n), C5-T3 (no brief renders exactly what shipped before C5), C5-T4 (a
selection with no criterion is refused) and C5-T5 (a partial pool is reported as k of what actually
came back).
"""

from __future__ import annotations

import pytest

from gtm_core.sampling_curve import (
    DEFAULT_HOOK_POOL,
    SamplingCurve,
    SamplingCurveError,
    preflight,
    price,
    select_record,
)


def _brief(per_shot, criterion="operator_contact_sheet") -> dict:
    return {
        "decisions": {
            "sampling_curve": {
                "value": {"per_shot": per_shot, "criterion": criterion},
                "source": "operator",
            }
        }
    }


# ── C5-T3: no brief renders exactly what shipped before ───────────────────────────────────────


def test_with_no_brief_the_curve_is_the_pre_c5_shape():
    """C5-T3. The widening is opt-in per piece; a run that decides nothing must not change."""
    curve = SamplingCurve.from_brief(None)
    assert curve.per_shot == {1: DEFAULT_HOOK_POOL}
    assert curve.n_for(1) == DEFAULT_HOOK_POOL
    assert all(curve.n_for(i) == 1 for i in range(2, 8))
    assert curve.total(6) == DEFAULT_HOOK_POOL + 5


def test_a_brief_that_declares_no_sampling_curve_also_gets_the_default():
    """ "We did not decide this one" is a legitimate state, not an error."""
    assert SamplingCurve.from_brief({"decisions": {}}).per_shot == {1: DEFAULT_HOOK_POOL}


def test_a_profile_that_overrides_the_hook_pool_is_honoured():
    assert SamplingCurve.from_brief(None, hook_pool=5).per_shot == {1: 5}


# ── reading a declared curve ──────────────────────────────────────────────────────────────────


def test_a_declared_curve_is_read_per_shot_with_one_as_the_unstated_answer():
    """A shot absent from the curve takes one draft — that is what "sample once" means, not a
    default hiding a decision."""
    curve = SamplingCurve.from_brief(_brief([{"shot": 1, "n": 4}, {"shot": 3, "n": 2}]))
    assert (curve.n_for(1), curve.n_for(2), curve.n_for(3)) == (4, 1, 2)
    assert curve.total(4) == 4 + 1 + 2 + 1


def test_a_curve_with_no_criterion_is_refused():
    """C5-T4, at read time: a pool whose winner is chosen by an unnamed process is unauditable."""
    with pytest.raises(SamplingCurveError, match="criterion"):
        SamplingCurve.from_brief(_brief([{"shot": 1, "n": 3}], criterion=""))


@pytest.mark.parametrize("n", [0, -1, 1.5, None, "three"])
def test_a_curve_asking_for_a_non_positive_draft_count_is_refused(n):
    """Zero is a request to cut the shot from the script, which is a script edit, not a curve."""
    with pytest.raises(SamplingCurveError):
        SamplingCurve.from_brief(_brief([{"shot": 1, "n": n}]))


@pytest.mark.parametrize("shot", [0, -1, "one", None])
def test_a_curve_naming_something_that_is_not_a_shot_is_refused(shot):
    with pytest.raises(SamplingCurveError, match="not a shot"):
        SamplingCurve.from_brief(_brief([{"shot": shot, "n": 2}]))


# ── C5-T1 / C5-T2: the whole curve is priced before anything is dispatched ────────────────────


def test_an_over_budget_curve_is_denied_and_dispatches_nothing():
    """C5-T1. Denied as a WHOLE: a partial batch spends the money and leaves no finished film."""
    dispatched: list[int] = []
    curve = SamplingCurve.from_brief(_brief([{"shot": 1, "n": 5}, {"shot": 2, "n": 5}]))

    verdict = preflight(curve, shot_count=4, per_render_credits=10.0, month_total=100.0, cap=150.0)
    if verdict.approved:  # pragma: no cover — the point is that it is not
        dispatched.extend(range(verdict.renders))

    assert verdict.approved is False
    assert verdict.renders == 12 and verdict.cost == 120.0
    assert dispatched == [], "an over-budget curve reached the dispatcher"
    assert "as a WHOLE" in verdict.reason


def test_a_within_budget_curve_dispatches_exactly_the_curve():
    """C5-T2. The positive control, and the arithmetic that has to be right for T1 to mean much."""
    curve = SamplingCurve.from_brief(_brief([{"shot": 1, "n": 3}, {"shot": 4, "n": 2}]))
    verdict = preflight(curve, shot_count=5, per_render_credits=7.5, month_total=10.0, cap=200.0)

    assert verdict.approved is True
    assert verdict.renders == 3 + 1 + 1 + 2 + 1
    assert verdict.cost == pytest.approx(8 * 7.5)


def test_a_curve_landing_exactly_on_the_cap_is_allowed():
    """The boundary: the cap is a ceiling, and spending up to it is what a cap is for."""
    curve = SamplingCurve.default(hook_pool=2)
    verdict = preflight(curve, shot_count=1, per_render_credits=50.0, month_total=0.0, cap=100.0)
    assert verdict.approved is True


def test_one_credit_over_the_cap_is_denied():
    """Positive control for the boundary above."""
    curve = SamplingCurve.default(hook_pool=2)
    verdict = preflight(curve, shot_count=1, per_render_credits=50.5, month_total=0.0, cap=100.0)
    assert verdict.approved is False


def test_pricing_the_whole_curve_beats_pricing_shot_by_shot():
    """The property the module exists for, stated as a comparison.

    Shot 1's own three renders fit inside the remaining budget; the curve as a whole does not. A
    per-shot guard approves the first and refuses the last, which is the worst outcome available.
    """
    curve = SamplingCurve.from_brief(_brief([{"shot": 1, "n": 3}]))
    headroom = 100.0 - 75.0

    # Shot 1's three renders cost 30 against 25 of headroom... so even shot 1 alone is over here.
    # Make the comparison honest: price shot 1 at a rate where it DOES fit, and show that the
    # curve as a whole still does not.
    shot_one_cost = curve.n_for(1) * 8.0
    assert shot_one_cost <= headroom, (
        "the fixture no longer demonstrates the point — shot 1 must be individually affordable"
    )

    whole = preflight(curve, shot_count=6, per_render_credits=8.0, month_total=75.0, cap=100.0)
    assert whole.renders == 8
    assert whole.cost == pytest.approx(64.0)
    assert whole.approved is False, (
        "a per-shot guard would have approved shot 1 and refused the last shot, spending the "
        "money and leaving no finished film. The whole-curve guard exists to refuse it up front."
    )


@pytest.mark.parametrize("bad", [0, -1])
def test_pricing_a_curve_with_no_shots_is_refused(bad):
    with pytest.raises(SamplingCurveError, match="shot_count"):
        price(SamplingCurve.default(), shot_count=bad, per_render_credits=1.0)


# ── C5-T4 / C5-T5: the selection record ───────────────────────────────────────────────────────


def test_a_selection_with_no_criterion_is_refused():
    """C5-T4. Which member won is only meaningful alongside how it was chosen."""
    with pytest.raises(SamplingCurveError, match="needs a criterion"):
        select_record(criterion="   ", chosen=1, requested=3, succeeded=3)


def test_a_partial_pool_is_reported_as_k_of_what_actually_arrived():
    """C5-T5. A pool of five where two failed is a choice made from THREE, and "1 of 5" overstates
    how much was looked at — the exact number a later reader judges the pick by."""
    record = select_record(criterion="operator_contact_sheet", chosen=1, requested=5, succeeded=3)
    assert record["draft_pool_size"] == 5
    assert record["pool_succeeded"] == 3
    assert record["k_of_n"] == "1 of 3 (5 requested)"


def test_a_complete_pool_does_not_clutter_the_record_with_a_requested_note():
    record = select_record(criterion="operator_contact_sheet", chosen=2, requested=4, succeeded=4)
    assert record["k_of_n"] == "2 of 4"


def test_a_pool_where_nothing_came_back_is_a_failure_not_a_selection():
    with pytest.raises(SamplingCurveError, match="nothing to select from"):
        select_record(criterion="operator_contact_sheet", chosen=1, requested=3, succeeded=0)


def test_a_rank_beyond_what_survived_is_refused():
    """Ranking against members that never arrived makes the selection not what it says."""
    with pytest.raises(SamplingCurveError, match="out of range"):
        select_record(criterion="operator_contact_sheet", chosen=4, requested=5, succeeded=3)


def test_more_successes_than_requests_is_refused():
    with pytest.raises(SamplingCurveError, match="not within"):
        select_record(criterion="operator_contact_sheet", chosen=1, requested=2, succeeded=3)


# ── the record round-trips into the manifest's own validation ─────────────────────────────────


def test_the_selection_record_satisfies_the_manifests_pool_rules():
    """A record this module produces must be one `render_manifest` accepts — otherwise the two
    halves of the same fact disagree, and the disagreement surfaces at write time on a real run."""
    from gtm_core.render_manifest import validate_draft_pool

    record = select_record(criterion="operator_contact_sheet", chosen=2, requested=5, succeeded=3)
    validate_draft_pool(
        record["draft_pool_size"], record["draft_rank"], None, record["pool_succeeded"]
    )


def test_the_manifest_refuses_a_rank_that_beat_members_which_never_arrived():
    """The same guard, from the manifest's side."""
    from gtm_core.render_manifest import ManifestError, validate_draft_pool

    with pytest.raises(ManifestError, match="exceeds pool_succeeded"):
        validate_draft_pool(5, 4, None, 3)


def test_the_manifest_still_accepts_a_pre_c5_pool_with_no_succeeded_count():
    """Additive: every manifest written before C5 stays valid."""
    from gtm_core.render_manifest import validate_draft_pool

    validate_draft_pool(3, 1, 0.42, None)


# ── the unit bridge (review finding: credits were compared straight against a USD cap) ────────


def test_the_multiplier_takes_credits_into_the_caps_unit():
    """8 renders x 10 credits = 80 credits. At 0.5 USD/credit that is 40 USD against a 40 cap:
    approved. Without the bridge the same curve read as 80 "units" against 40 and was denied."""
    curve = SamplingCurve.from_brief(_brief([{"shot": 1, "n": 3}]))
    v = preflight(
        curve,
        shot_count=6,
        per_render_credits=10.0,
        month_total=0.0,
        cap=40.0,
        credits_to_cap_unit=0.5,
    )
    assert v.approved is True
    assert v.cost_credits == 80.0 and v.cost == 40.0, "both sides of the conversion are recorded"
    unbridged = preflight(curve, shot_count=6, per_render_credits=10.0, month_total=0.0, cap=40.0)
    assert unbridged.approved is False and unbridged.cost == 80.0


def test_a_non_positive_multiplier_is_refused():
    """A zero rate would price every curve at nothing — the opposite of a guard."""
    with pytest.raises(SamplingCurveError, match="credits_to_cap_unit"):
        preflight(
            SamplingCurve.default(),
            shot_count=1,
            per_render_credits=1.0,
            month_total=0.0,
            cap=1.0,
            credits_to_cap_unit=0.0,
        )


def test_the_cli_refuses_to_run_with_neither_a_profile_nor_a_typed_total(capsys):
    """Without a profile the tool cannot derive the total; it must say so, never assume 0."""
    from gtm_core.sampling_curve import main

    with pytest.raises(SystemExit):
        main(["preflight", "--shots", "3", "--per-render", "10", "--cap", "100"])
    assert "--month-total is required without --profile" in capsys.readouterr().err

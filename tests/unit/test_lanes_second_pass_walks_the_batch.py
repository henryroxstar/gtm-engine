"""`_second_pass` walks the WHOLE batch: a row it skips — held, excluded, or carrying a
protective trigger it must not overwrite — is skipped, never the end of the pass.

The 2026-09-23 hunt turned each `continue` in `gtm_core/lanes/router.py::_second_pass` into
`break`, and both mutants survived every scoped test: every existing duplicate-contact fixture
puts the skipped row LAST in score order, so ending the pass early changed nothing. In
production `prospects route` sorts by score, and the top-scored row of a list is exactly the
one most likely to be a prior contact or a suppressed account — the pass would stop at row one
and the second contact at every other account would sail through as `personalised`, which is
the double-touch the second pass exists to prevent. Fictional fixtures only (§R9).
"""

from __future__ import annotations

import datetime

import pytest

from gtm_core import lanes
from gtm_core.adjudication import Adjudication
from gtm_core.lanes.context import RouterContext

AS_OF = datetime.date(2026, 9, 3)
LEAD = "lead@harborline.example"
FIRST = "a@lantern.example"
SECOND = "b@lantern.example"


def _row(**kw) -> dict:
    base = {
        "first": "Jordan",
        "last": "Vance",
        "email": FIRST,
        "title": "Chief Information Security Officer",
        "company": "Quarry Systems",
        "company_domain": "lantern.example",
        "segment": "enterprise",
        "tier": "B",
        "score": "80",
        "why_now": "Quarry Systems opened an AI governance program covering autonomous agents",
        "signal_observed": "2026-08-20",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "",
        "account_id": "",
        "suppression": "",
    }
    base.update(kw)
    return base


def _rec(email: str, verdict: str) -> Adjudication:
    return Adjudication(
        email=email, verdict=verdict, score=3, repair_attempt=0, body_hash="h1", touch=1
    )


def _lead(**kw) -> dict:
    return _row(
        email=LEAD,
        first="Ash",
        company="Harborline",
        company_domain="harborline.example",
        score="99",
        **kw,
    )


@pytest.mark.parametrize("leading", ["held", "excluded", "decided-protective"])
def test_a_skipped_row_sorted_first_does_not_end_the_second_pass(leading):
    ctx = RouterContext(profile="acme", as_of=AS_OF)
    decisions: dict = {}
    if leading == "held":
        lead = _lead()
        ctx.prior_emails = {LEAD}  # → hold: prior-contact
    elif leading == "excluded":
        lead = _lead(suppression="out-of-market")  # → excluded: suppressed
    else:
        lead = _lead()
        ctx.prior_emails = {LEAD}
        # A recorded salvage keeps the protective trigger on a non-hold lane — the row the
        # second pass must step over WITHOUT touching (PS-R I6).
        decisions = {
            ("prior-contact", "d:harborline.example"): {
                "decision": "salvage",
                "trigger": "prior-contact",
            }
        }
    rows = [lead, _row(email=FIRST, score="90"), _row(email=SECOND, first="Sam", score="50")]
    result = lanes.route(
        rows, [_rec(FIRST, "send"), _rec(SECOND, "send")], ctx, decisions=decisions
    )

    by = {r.email: r for r in result.routed}
    # The premise: the skipped row really is first in the order the pass walks.
    assert result.routed[0].email == LEAD
    if leading == "held":
        assert by[LEAD].lane == "hold" and by[LEAD].trigger == "prior-contact"
    elif leading == "excluded":
        assert by[LEAD].lane == "excluded" and by[LEAD].trigger == "suppressed"
    else:
        assert by[LEAD].lane == "repair" and by[LEAD].trigger == "prior-contact"

    assert by[FIRST].lane == "personalised"
    assert by[SECOND].lane == "hold" and by[SECOND].trigger == "duplicate-contact"
    assert result.hold_counts["duplicate-contact"] == 1


def test_a_skipped_row_sorted_first_still_lets_tier_a_generic_fire_later():
    """The other second-pass hold, same shape: a suppressed row first, then a tier-A row that
    earned only the generic email — it must still be held for a person to decide."""
    ctx = RouterContext(profile="acme", as_of=AS_OF)
    rows = [
        _lead(suppression="out-of-market"),
        _row(email=FIRST, tier="A", score="90", signal_observed="", why_now=""),
    ]
    result = lanes.route(rows, [], ctx)
    assert result.routed[0].email == LEAD and result.routed[0].lane == "excluded"
    assert result.routed[1].lane == "hold" and result.routed[1].trigger == "tier-a-generic"


def test_judge_detail_names_the_defect_class():
    """The repair detail an operator reads says WHICH defect the judge found — the canonical
    class, `(right_person)` for a `wrong-person` record — and says `unclassed` only when the
    record carries no class at all."""
    ctx = RouterContext(profile="acme", as_of=AS_OF)
    classed = Adjudication(
        email=FIRST,
        verdict="re-angle",
        score=1,
        repair_attempt=0,
        body_hash="h1",
        touch=1,
        defect_class="wrong-person",
    )
    r = lanes.route_row(_row(), ctx, [classed])
    assert r.lane == "repair" and r.detail == "judge re-angle (right_person)"
    r = lanes.route_row(_row(), ctx, [_rec(FIRST, "re-angle")])
    assert r.lane == "repair" and r.detail == "judge re-angle (unclassed)"

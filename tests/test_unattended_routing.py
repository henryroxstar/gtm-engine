from __future__ import annotations

import datetime

from gtm_core.lanes.context import RouterContext
from gtm_core.lanes.router import route_row


def _row(**kw) -> dict:
    base = {
        "first": "Test",
        "last": "Person",
        "title": "CEO",
        "email": "test@example.com",
        "company": "Example",
        "company_domain": "example.com",
        "verdict": "send",
        "why_now": "Example opened a new AI lab in New York today",
        "signal_observed": "2026-09-01",
    }
    base.update(kw)
    return base


def test_unattended_routing_generic_to_hold():
    """Verify that in unattended mode, accounts lacking a story (generic lane) are routed to hold."""
    ctx = RouterContext(profile="acme", as_of=datetime.date(2026, 9, 3))

    # Missing why_now means it goes to generic normally
    row = _row(why_now="", signal_observed="")

    # 1. Normal mode -> generic
    routed = route_row(row, ctx, None, unattended=False)
    assert routed.lane == "generic"

    # 2. Unattended mode -> hold
    routed_unattended = route_row(row, ctx, None, unattended=True)
    assert routed_unattended.lane == "hold"
    assert routed_unattended.trigger == "unattended-generic"


def test_unattended_routing_keeps_personalised():
    """Verify that unattended mode does not touch personalised rows."""
    from gtm_core.adjudication import Adjudication

    ctx = RouterContext(profile="acme", as_of=datetime.date(2026, 9, 3))

    row = _row()
    # Good judge verdict
    recs = [Adjudication(email="test@example.com", verdict="send", touch=1, score=3)]

    # Normal mode -> personalised
    routed = route_row(row, ctx, recs, unattended=False)
    assert routed.lane == "personalised"

    # Unattended mode -> still personalised
    routed_unattended = route_row(row, ctx, recs, unattended=True)
    assert routed_unattended.lane == "personalised"


_UNATTENDED_DETAIL = "unattended mode fail-closed for generic lane candidate"


def _decided(decision: str, *, trigger: str = "unattended-generic", key: str = "d:example.com"):
    return {(trigger, key): {"decision": decision, "detail": _UNATTENDED_DETAIL}}


def test_a_recorded_answer_to_the_unattended_hold_is_honoured_on_the_next_unattended_run():
    """The operator answered this account's unattended hold; the next unattended run must not ask
    again. Until 2026-10-06 the fail-close ran after the decision lookup and ignored it, so 712
    recorded answers were re-asked on every run."""
    ctx = RouterContext(profile="acme", as_of=datetime.date(2026, 9, 3))
    row = _row(why_now="", signal_observed="")

    generic = route_row(row, ctx, None, unattended=True, decisions=_decided("generic"))
    assert (generic.lane, generic.decided) == ("generic", "decided:generic:unattended-generic")

    salvage = route_row(row, ctx, None, unattended=True, decisions=_decided("salvage"))
    assert salvage.lane == "repair"

    suppress = route_row(row, ctx, None, unattended=True, decisions=_decided("suppress"))
    assert suppress.lane == "excluded"


def test_only_a_recorded_answer_for_this_account_and_this_hold_opens_the_fail_close():
    """No human answer, no send: another account's answer, an answer to a different hold, and a
    blanket policy line all leave the row held."""
    ctx = RouterContext(profile="acme", as_of=datetime.date(2026, 9, 3))
    ctx.policy_auto["unattended-generic"] = "generic"
    row = _row(why_now="", signal_observed="")

    for decisions in (
        {},
        _decided("generic", key="d:other.example"),
        _decided("generic", trigger="tier-a-generic"),
        {("unattended-generic", "d:example.com"): {"decision": "generic", "detail": "stale"}},
    ):
        routed = route_row(row, ctx, None, unattended=True, decisions=decisions)
        assert (routed.lane, routed.trigger) == ("hold", "unattended-generic"), decisions

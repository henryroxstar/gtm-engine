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

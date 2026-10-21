"""A `duplicate-contact: generic` decision ("email both") admits only a DIFFERENT seat.

The 2026-09-23 "send to both seats" decisions were recorded on the assumption that the two
people at an account hold different seats — each gets that seat's copy. `_apply_decision`
keys on (trigger, account) plus a seat-free detail string, so a pair where both contacts
resolve to the SAME seat (two CTO-seat people, two CEO-seat people) was admitted too: one
account, the same email twice. PH18: admit only when both seats resolve and differ, else
re-hold and name the seats. `suppress` is untouched (its branch never read the detail), and
the seat-free detail is kept for a differing pair so the recorded `generic` decisions still
match. Fictional fixtures only (§R9).
"""

from __future__ import annotations

import datetime

from gtm_core import lanes
from gtm_core.adjudication import Adjudication
from gtm_core.lanes.context import RouterContext

AS_OF = datetime.date(2026, 9, 3)
FIRST = "a@lantern.example"
SECOND = "b@lantern.example"
BASE = "another contact at this account is already in the personalised lane"
KEY = ("duplicate-contact", "d:lantern.example")


def _row(**kw) -> dict:
    base = {
        "first": "Jordan",
        "last": "Vance",
        "email": FIRST,
        "title": "Chief Technology Officer",
        "company": "Quarry Systems",
        "company_domain": "lantern.example",
        "segment": "enterprise",
        "tier": "B",
        "score": "90",
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


def _rec(email: str) -> Adjudication:
    return Adjudication(
        email=email, verdict="send", score=3, repair_attempt=0, body_hash="h1", touch=1
    )


def _route(second_title: str, decision: dict | None):
    rows = [_row(), _row(email=SECOND, first="Sam", title=second_title, score="50")]
    decisions = {KEY: {"trigger": "duplicate-contact", **decision}} if decision else {}
    result = lanes.route(
        rows,
        [_rec(FIRST), _rec(SECOND)],
        RouterContext(profile="acme", as_of=AS_OF),
        decisions=decisions,
    )
    by = {r.email: r for r in result.routed}
    assert by[FIRST].lane == "personalised"
    return by[SECOND]


def test_generic_admits_a_second_contact_in_a_different_seat():
    """Positive control: CTO seat + CEO seat, the recorded seat-free detail still matches."""
    r = _route("Chief Executive Officer", {"decision": "generic", "detail": BASE})
    assert r.lane == "generic"
    assert r.decided == "decided:generic:duplicate-contact"
    assert r.detail == BASE


def test_generic_does_not_admit_a_second_contact_in_the_same_seat():
    """CTO + VP Engineering both resolve to the `cto` seat — one account, the same email twice."""
    r = _route("VP Engineering", {"decision": "generic", "detail": BASE})
    assert r.lane == "hold" and r.trigger == "duplicate-contact"
    assert r.decided == ""
    assert r.detail.startswith(BASE)
    assert "this contact cto, colleague cto" in r.detail


def test_generic_does_not_admit_when_a_seat_is_unresolved():
    """Different people, but nothing proves they get different copy: held, and it says which."""
    r = _route("Office Manager", {"decision": "generic", "detail": BASE})
    assert r.lane == "hold" and r.trigger == "duplicate-contact"
    assert "this contact unresolved, colleague cto" in r.detail


def test_generic_does_not_admit_when_the_colleague_seat_is_unresolved():
    """The mirror case: the higher-scored FIRST contact is the unresolved one. A resolved
    second seat differs from "nothing", which proves nothing about the copy either gets."""
    rows = [
        _row(title="Office Manager"),
        _row(email=SECOND, first="Sam", title="Chief Executive Officer", score="50"),
    ]
    decisions = {KEY: {"trigger": "duplicate-contact", "decision": "generic", "detail": BASE}}
    result = lanes.route(
        rows,
        [_rec(FIRST), _rec(SECOND)],
        RouterContext(profile="acme", as_of=AS_OF),
        decisions=decisions,
    )
    by = {r.email: r for r in result.routed}
    assert by[FIRST].lane == "personalised"
    assert by[SECOND].lane == "hold" and by[SECOND].trigger == "duplicate-contact"
    assert by[SECOND].decided == ""
    assert "this contact ceo, colleague unresolved" in by[SECOND].detail


def test_generic_recorded_against_the_seat_detail_is_still_refused():
    """The re-hold names the seats; a `generic` typed against THAT detail is refused too."""
    first = _route("VP Engineering", None)
    r = _route("VP Engineering", {"decision": "generic", "detail": first.detail})
    assert r.lane == "hold" and r.decided == ""


def test_suppress_still_excludes_a_same_seat_contact():
    """Unchanged: suppress never read the detail, so the recorded ones keep matching."""
    r = _route("VP Engineering", {"decision": "suppress", "detail": BASE})
    assert r.lane == "excluded" and r.decided == "decided:suppress:duplicate-contact"


def test_salvage_recorded_against_the_seat_detail_applies():
    """`swap: this person, not the first` is not a two-seat send, so it is not seat-gated."""
    first = _route("VP Engineering", None)
    assert first.lane == "hold"
    r = _route("VP Engineering", {"decision": "salvage", "detail": first.detail})
    assert r.lane == "repair" and r.decided == "decided:salvage:duplicate-contact"


def test_a_third_contact_is_checked_against_every_admitted_colleague():
    """CTO first, CEO admitted, then a second CEO-seat person: differs from the first, not
    from the colleague already admitted beside them."""
    rows = [
        _row(),
        _row(email=SECOND, first="Sam", title="Chief Executive Officer", score="50"),
        _row(email="c@lantern.example", first="Kai", title="Founder and CEO", score="40"),
    ]
    decisions = {KEY: {"trigger": "duplicate-contact", "decision": "generic", "detail": BASE}}
    result = lanes.route(
        rows,
        [_rec(r["email"]) for r in rows],
        RouterContext(profile="acme", as_of=AS_OF),
        decisions=decisions,
    )
    by = {r.email: r for r in result.routed}
    assert by[SECOND].lane == "generic"
    assert by["c@lantern.example"].lane == "hold"
    assert "colleague cto, ceo" in by["c@lantern.example"].detail

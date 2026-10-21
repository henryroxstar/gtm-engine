"""A connector can be live, in credit, and still refusing the call.

On 2026-09-21 ``gtm_core.preflight --need intent`` reported *"intent available via
vibe+rocketreach · Safe to spend"* while RocketReach ``company_search`` sat at 1000/1000 for the
day, 0 remaining — a ~12 hour lockout. ``ping`` returns pong and ``account`` returns a healthy
credit balance, so both of the things preflight looked at were genuinely fine. The block lived in
the ``rate_limits`` array, which preflight never read.

The consequence is not "one call failed". The RocketReach intent facet is the **credit-free** half
of the double-intent axis, so with it silently gone heat is structurally capped at 2 and the run
can conclude "no double-intent accounts" when it could not look.

**These tests run against a RECORDED REAL payload**, not a hand-written dict. The test plan is
explicit about why: a dict written to match the parser cannot fail on the parser's assumption. The
recorded response also carries a genuine ``null`` limit (``bulk_job``/``one_month``) that no
hand-written fixture would have included, and which the parser has to survive.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.preflight import RATE_LIMITED, adjudicate, exhausted_actions

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rocketreach_account.recorded.json"


@pytest.fixture(scope="module")
def account() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def limits(account) -> dict:
    return {"rocketreach": account}


ALL_OK = {"vibe": "ok", "rocketreach": "ok", "apollo": "absent", "saleshandy": "ok"}


# --------------------------------------------------------------------------------------------
# 1. The fixture really is the condition it claims to be
# --------------------------------------------------------------------------------------------


def test_the_recorded_payload_contains_a_genuinely_exhausted_block(account) -> None:
    """Instrument check. If the recording were of a healthy account, every test below would
    pass while proving nothing."""
    spent = [r for r in account["rate_limits"] if r["remaining"] == 0]
    assert {
        "action": "company_search",
        "duration": "one_day",
        "limit": 1000,
        "used": 1000,
        "remaining": 0,
    } in spent


def test_the_recorded_payload_still_looks_healthy_to_the_old_checks(account) -> None:
    """The reason this was invisible: liveness and credits were both fine. This pins the trap
    rather than assuming it."""
    premium = next(c for c in account["credit_usage"] if c["credit_type"] == "premium_lookup")
    assert premium["remaining"] > 0


# --------------------------------------------------------------------------------------------
# 2. Parsing
# --------------------------------------------------------------------------------------------


def test_the_exhausted_action_is_found_with_its_window(limits) -> None:
    spent = exhausted_actions(limits)
    assert spent[("rocketreach", "company_search")] == "one_day"


def test_a_null_limit_is_not_read_as_exhaustion(limits) -> None:
    """``bulk_job``/``one_month`` has ``limit: null, remaining: null`` in the real response —
    no ceiling, not a lockout. A truthiness check would have called it spent."""
    assert ("rocketreach", "bulk_job") not in exhausted_actions(limits)


def test_healthy_actions_are_not_reported_as_spent(limits) -> None:
    spent = exhausted_actions(limits)
    assert ("rocketreach", "person_lookup") not in spent
    assert ("rocketreach", "person_search") not in spent


def test_either_payload_shape_is_accepted(account) -> None:
    """The operator pastes whichever they have — the whole ``account`` response or just the
    array inside it."""
    whole = exhausted_actions({"rocketreach": account})
    array = exhausted_actions({"rocketreach": account["rate_limits"]})
    assert whole == array


@pytest.mark.parametrize(
    "junk",
    [None, {}, {"rocketreach": None}, {"rocketreach": "nope"}, {"rocketreach": [1, "x", {}]}],
)
def test_an_unreadable_limits_payload_reports_nothing_spent_and_never_raises(junk) -> None:
    """Preflight runs before any spend; crashing here would block a run over a malformed paste.
    Reporting nothing spent is the pre-2026-09-22 behaviour, which is no worse than before."""
    assert exhausted_actions(junk) == {}


# --------------------------------------------------------------------------------------------
# 3. The verdict changes — this is the point
# --------------------------------------------------------------------------------------------


def test_without_the_limits_the_old_wrong_answer_is_reproduced(account) -> None:
    """Negative control. This is the 2026-09-21 verdict, and it is what the fix must differ
    from — otherwise the test below proves nothing about rate limits."""
    verdict = adjudicate(ALL_OK, ["double_intent"])
    assert verdict["capabilities"]["double_intent"]["available"] is True
    assert verdict["proceed"] is True


def test_with_the_limits_double_intent_is_reported_unavailable(limits) -> None:
    verdict = adjudicate(ALL_OK, ["double_intent"], limits=limits)
    cap = verdict["capabilities"]["double_intent"]
    assert cap["available"] is False
    assert verdict["proceed"] is False
    assert "double_intent" in verdict["blocking"]


def test_the_note_names_the_action_and_the_window_not_just_the_connector(limits) -> None:
    """ "rocketreach unavailable" sends an operator to reconnect a connector that works fine."""
    note = adjudicate(ALL_OK, ["double_intent"], limits=limits)["capabilities"]["double_intent"][
        "note"
    ]
    assert "company_search" in note
    assert "one_day" in note
    assert "0 remaining" in note


def test_intent_falls_back_to_vibe_rather_than_claiming_rocketreach(limits) -> None:
    """``intent`` has a second provider, so it survives — but it must survive *via vibe*, and
    say that RocketReach is locked out."""
    cap = adjudicate(ALL_OK, ["intent"], limits=limits)["capabilities"]["intent"]
    assert cap["available"] is True
    assert cap["via"] == "vibe"
    assert cap["rate_limited"] == ["rocketreach"]


def test_a_capability_whose_action_is_healthy_is_unaffected(limits) -> None:
    """``contacts`` calls ``person_lookup``, which has 2671 remaining. An exhausted
    ``company_search`` must not take it down — the limit is per action, not per connector."""
    cap = adjudicate(ALL_OK, ["contacts"], limits=limits)["capabilities"]["contacts"]
    assert cap["available"] is True
    assert cap["via"] == "rocketreach"
    assert cap["rate_limited"] == []


def test_an_observed_rate_limited_status_is_not_ok() -> None:
    """The brain can also observe the lockout directly (the API says "Try again in N seconds").
    That status must not win the waterfall either."""
    observed = dict(ALL_OK, rocketreach=RATE_LIMITED)
    cap = adjudicate(observed, ["contacts"])["capabilities"]["contacts"]
    assert cap["via"] != "rocketreach"


def test_the_cli_accepts_the_recorded_payload_end_to_end(capsys) -> None:
    """§4.6: a flag nothing executes is a flag that rots."""
    from gtm_core.preflight import main

    code = main(
        [
            "--profile",
            "fixtureco",
            "--observed",
            json.dumps(ALL_OK),
            "--need",
            "double_intent",
            "--limits",
            json.dumps({"rocketreach": json.loads(FIXTURE.read_text(encoding="utf-8"))}),
        ]
    )
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["capabilities"]["double_intent"]["available"] is False

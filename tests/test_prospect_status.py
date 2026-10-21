"""Tests for the six-way operator status derivation (PS7).

Two responsibilities pinned separately: :func:`status_of` maps a routed row's
``(lane, reason)`` to one of six operator-facing words, and :func:`needs_address` flags a
named contact this pipeline cannot reach. The live-distribution test below is a UNIT test
— it builds a fixture matching the exact 2026-09-10 ``lanes-state.jsonl`` counts rather
than reading the real file, so it stays correct even after that file is rewritten by a
later run.
"""

from __future__ import annotations

from collections import Counter

import pytest

from gtm_core import prospect_status as ps
from gtm_core.lanes.model import EXCLUDE_ORDER, HOLD_ORDER

# --------------------------------------------------------------------------- contract


def test_hardcoded_hold_triggers_match_the_live_constant():
    """`prospect_status.py` hand-copies HOLD_ORDER rather than importing `lanes.model`
    (see that module's docstring for why) — this is the tripwire against drift."""
    assert ps._HOLD_TRIGGERS == frozenset(HOLD_ORDER)


def test_hardcoded_exclude_triggers_match_the_live_constant():
    assert ps._EXCLUDE_TRIGGERS == frozenset(EXCLUDE_ORDER)


def test_hardcoded_retired_ledger_statuses_match_the_live_constant():
    """`prospect_status.py` hand-copies `gtm_core.prospects_state.RETIRED_STATUSES` for
    `needs_address()` rather than importing it — the module stays leaf (no imports beyond
    `__future__`), so the import happens here, in the test, instead. This is the tripwire
    against drift: a status added or renamed in `prospects_state.py` fails this test
    rather than silently going unrecognised by `needs_address()`."""
    from gtm_core.prospects_state import RETIRED_STATUSES

    assert ps._RETIRED_LEDGER_STATUSES == frozenset(RETIRED_STATUSES)


# --------------------------------------------------------------------------- status_of


def test_personalised_and_generic_always_ready_to_send():
    assert ps.status_of("personalised", "") == "ready_to_send"
    assert ps.status_of("personalised", "researcher-send") == "ready_to_send"
    assert ps.status_of("generic", "") == "ready_to_send"
    assert ps.status_of("generic", "no-signal-clause") == "ready_to_send"


def test_repair_always_being_fixed():
    assert ps.status_of("repair", "") == "being_fixed"
    assert ps.status_of("repair", "tier-a-generic") == "being_fixed"
    assert ps.status_of("repair", "prior-contact") == "being_fixed"


@pytest.mark.parametrize("trigger", sorted(set(HOLD_ORDER) - {"researcher-drop"}))
def test_every_hold_trigger_except_researcher_drop_is_waiting_on_you(trigger):
    assert ps.status_of("hold", trigger) == "waiting_on_you"


def test_hold_researcher_drop_is_not_emailing():
    assert ps.status_of("hold", "researcher-drop") == "not_emailing"


def test_hold_choice_prefixed_trigger_is_waiting_on_you():
    assert ps.status_of("hold", "salvage:duplicate-contact") == "waiting_on_you"
    assert ps.status_of("hold", "policy:salvage:tier-a-generic") == "waiting_on_you"


def test_hold_unknown_trigger_raises():
    with pytest.raises(ps.UnmappedStatus):
        ps.status_of("hold", "not-a-real-trigger")


def test_excluded_already_enrolled_is_in_sending_tool():
    assert ps.status_of("excluded", "already-enrolled") == "in_sending_tool"


@pytest.mark.parametrize("reason", ["suppressed", "optout", "competitor-direct"])
def test_excluded_closed_reasons_are_not_emailing(reason):
    assert ps.status_of("excluded", reason) == "not_emailing"


@pytest.mark.parametrize("trigger", sorted(HOLD_ORDER))
def test_excluded_via_suppress_decision_on_any_hold_trigger_is_not_emailing(trigger):
    """The shape an excluded-via-operator-suppress row's `reason` actually takes
    (`Routed.stable_reason` composing `decided="decided:suppress:<trigger>"`) —
    confirmed by reading `gtm_core/lanes/router.py::_apply_decision`, not guessed."""
    assert ps.status_of("excluded", f"suppress:{trigger}") == "not_emailing"


def test_excluded_unknown_reason_raises():
    with pytest.raises(ps.UnmappedStatus):
        ps.status_of("excluded", "not-a-real-reason")


def test_excluded_suppress_prefix_with_unknown_trigger_raises():
    with pytest.raises(ps.UnmappedStatus):
        ps.status_of("excluded", "suppress:not-a-real-trigger")


@pytest.mark.parametrize("lane", ["", "  ", "signal", "bogus-lane"])
def test_blank_or_unknown_lane_raises(lane):
    with pytest.raises(ps.UnmappedStatus):
        ps.status_of(lane, "")


# --------------------------------------------------------------------------- live distribution


def _fixture_records() -> list[dict]:
    """The exact 2026-09-10 distribution of a tenant's `lanes-state.jsonl` (lane, trigger) —
    written as `reason` here since that is the field `status_of` reads; the CLI's job
    (tested separately) is falling back to `trigger` for an old record."""
    rows: list[dict] = []
    rows += [{"lane": "generic", "reason": ""}] * 272
    rows += [{"lane": "repair", "reason": "tier-a-generic"}] * 107
    rows += [{"lane": "repair", "reason": "prior-contact"}] * 19
    rows += [{"lane": "repair", "reason": ""}] * 3
    rows += [{"lane": "excluded", "reason": "already-enrolled"}] * 100
    rows += [{"lane": "excluded", "reason": "competitor-direct"}] * 1
    rows += [{"lane": "hold", "reason": "tier-a-generic"}] * 92
    rows += [{"lane": "hold", "reason": "duplicate-contact"}] * 10
    rows += [{"lane": "hold", "reason": "researcher-drop"}] * 1
    rows += [{"lane": "personalised", "reason": ""}] * 3
    assert len(rows) == 608
    return rows


def test_live_distribution_maps_to_the_predicted_six_way_split():
    rows = _fixture_records()
    counts = Counter(ps.status_of(r["lane"], r["reason"]) for r in rows)
    assert counts["waiting_on_you"] == 102  # 92 tier-a-generic + 10 duplicate-contact
    assert counts["ready_to_send"] == 275  # 272 generic + 3 personalised
    assert counts["being_fixed"] == 129  # all repair
    assert counts["in_sending_tool"] == 100  # excluded/already-enrolled
    assert counts["not_emailing"] == 2  # 1 excluded/competitor-direct + 1 hold/researcher-drop
    assert sum(counts.values()) == 608


# --------------------------------------------------------------------------- needs_address


def test_needs_address_named_no_email_new_status_is_true():
    assert ps.needs_address({"contact_name": "Jordan Vance", "status": "new"}) is True


def test_needs_address_named_no_email_disqualified_is_false():
    assert ps.needs_address({"contact_name": "Jordan Vance", "status": "disqualified"}) is False


def test_needs_address_named_has_email_is_false():
    assert (
        ps.needs_address(
            {
                "contact_name": "Jordan Vance",
                "contact_email": "jordan@vertex.example",
                "status": "new",
            }
        )
        is False
    )


def test_needs_address_unnamed_is_false():
    assert ps.needs_address({"contact_name": "", "status": "new"}) is False
    assert ps.needs_address({"status": "new"}) is False


@pytest.mark.parametrize("status", ["disqualified", "do-not-contact", "closed-lost"])
def test_needs_address_every_retired_status_excluded(status):
    assert ps.needs_address({"contact_name": "Jordan Vance", "status": status}) is False


@pytest.mark.parametrize(
    "pseudo", ["none", "None", "NULL", "n/a", "N/A", "undefined", "unknown", "  "]
)
def test_needs_address_treats_pseudo_email_as_no_email(pseudo):
    """Stringified nulls/placeholders in contact_email should not evaluate as having an email."""
    assert (
        ps.needs_address(
            {
                "contact_name": "Jordan Vance",
                "contact_email": pseudo,
                "status": "new",
            }
        )
        is True
    )


@pytest.mark.parametrize(
    "pseudo", ["none", "None", "NULL", "n/a", "N/A", "undefined", "unknown", ""]
)
def test_needs_address_treats_pseudo_name_as_unnamed(pseudo):
    """Stringified nulls/placeholders in contact_name should not evaluate as a named contact."""
    assert (
        ps.needs_address(
            {
                "contact_name": pseudo,
                "contact_email": "",
                "status": "new",
            }
        )
        is False
    )


# --------------------------------------------------------------------------- exported vocabulary


def test_labels_and_next_step_cover_every_status_exactly():
    assert set(ps.LABELS) == set(ps.STATUSES)
    assert set(ps.NEXT_STEP) == set(ps.STATUSES)

"""PS20 T1.10/T1.11 — the page knows how old and how readable its sending figures are."""

from datetime import UTC, datetime

import pytest

from gtm_core.email_campaign_dashboard import health

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "fetched,days",
    [
        ("2026-09-24T12:00:00Z", 1),
        ("2026-09-23T12:00:00Z", 2),
        ("2026-09-22T11:00:00Z", 3),
        ("2026-09-22", 3),
        (None, None),
        ("", None),
        ("yesterday", None),
        ("2026-09-27T12:00:00Z", None),  # 2 days ahead: past the 1-day tolerance, untrustworthy
    ],
)
def test_age_in_whole_days(fetched, days):
    assert health.figures_age_days(fetched, NOW) == days


@pytest.mark.parametrize(
    "fetched,expected",
    [
        ("2026-09-24T12:00:00Z", "2026-09-24"),
        ("2026-09-22", "2026-09-22"),
        (None, None),
        ("", None),
        ("yesterday", None),
        # Unlike `figures_age_days`, a well-formed FUTURE date still parses: `figures_date`
        # answers "what date does this string name", never "is that date trustworthy".
        ("2026-09-26T12:00:00Z", "2026-09-26"),
    ],
)
def test_figures_date(fetched, expected):
    assert health.figures_date(fetched) == expected


def _status(rows=(), **snap):
    base = {"fetched": "2026-09-25T00:00:00Z", "unreadable": False, "skipped": 0, "source": "stats"}
    return {"sequences": list(rows), "snapshot": {**base, **snap}}


OK_REC = {"ok": True}


def test_fresh_readable_agreeing_page_has_no_strip():
    assert health.page_warnings(_status([{"id": "S1"}]), OK_REC, True, NOW) == []


def test_old_figures_raise_figures_old():
    st = _status([{"id": "S1"}], fetched="2026-09-22T11:00:00Z")
    assert health.page_warnings(st, OK_REC, True, NOW) == ["figures-old"]


def test_missing_fetched_with_rows_counts_as_old_but_no_rows_no_strip():
    assert health.page_warnings(_status([{"id": "S1"}], fetched=None), OK_REC, True, NOW) == [
        "figures-old"
    ]
    assert health.page_warnings(_status([], fetched=None), OK_REC, True, NOW) == []


def test_unreadable_wins_and_suppresses_reconciliation():
    assert health.page_warnings(
        _status([], unreadable=True, fetched=None), {"ok": False}, False, NOW
    ) == ["unreadable"]


def test_two_reasons_share_one_strip():
    st = _status([{"id": "S1"}], fetched="2026-09-01")
    assert health.page_warnings(st, {"ok": False}, True, NOW) == ["records-disagree", "figures-old"]


def test_sum_mismatch_is_records_disagree():
    assert health.page_warnings(_status([{"id": "S1"}]), OK_REC, False, NOW) == ["records-disagree"]


def test_figures_exactly_at_max_age_is_not_old():
    """The boundary is `>`, never `>=` — exactly `FIGURES_MAX_AGE_DAYS` old is still fresh
    enough. Pinned so a future rewrite to `>=` fails here instead of passing unnoticed."""
    st = _status([{"id": "S1"}], fetched="2026-09-23T12:00:00Z")  # exactly 2 days before NOW
    assert health.page_warnings(st, OK_REC, True, NOW) == []


def test_local_date_before_utc_midnight_is_not_a_false_future():
    """The skill writes `fetched` as a bare LOCAL date. A UTC+8 operator refreshing before
    08:00 local writes a date that, in UTC, is still "tomorrow" — fetched "2026-09-25" at
    now 2026-09-24T20:00Z is one day ahead in wall-clock terms. That must read as today
    (age 0), not as an untrustworthy future date, or every early-morning refresh would
    raise a false "figures old" strip.
    """
    local_now = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    assert health.figures_age_days("2026-09-25", local_now) == 0
    status = _status([{"id": "S1"}], fetched="2026-09-25")
    assert health.page_warnings(status, OK_REC, True, local_now) == []


def test_reconciliation_detail_names_both_sides():
    detail = health.reconciliation_detail(
        {"ok": False, "in_ledger_only": ["S1"], "in_snapshot_only": ["GHOST"]}
    )
    assert detail == (
        "missing from the live figures: S1; in the figures but not our records: GHOST"
    )


def test_reconciliation_detail_one_side_only():
    assert (
        health.reconciliation_detail(
            {"ok": False, "in_ledger_only": ["S1", "S2"], "in_snapshot_only": []}
        )
        == "missing from the live figures: S1, S2"
    )


def _camps(current=(), archived=(), unlinked=()):
    return {
        "campaigns": [
            {
                "sequences": [{"sequence_id": s} for s in current],
                "archived": [{"sequence_id": s} for s in archived],
            }
        ]
        if (current or archived)
        else [],
        "unlinked_sequences": [{"sequence_id": s} for s in unlinked],
    }


def test_no_manifest_profile_can_read_started():
    status = _status([{"id": "X", "status": None, "sent": 4}])
    assert (
        health.page_go_live(_camps(unlinked=["X"]), status, {"current": 0, "not_linked": 4})
        == "started"
    )


def test_archived_paused_row_does_not_mask_current_started():
    status = _status(
        [{"id": "S1", "status": None, "sent": 3}, {"id": "OLD", "status": "paused", "sent": 9}]
    )
    assert (
        health.page_go_live(
            _camps(current=["S1"], archived=["OLD"]), status, {"current": 3, "not_linked": 0}
        )
        == "started"
    )


def test_unreadable_page_is_unknown():
    assert (
        health.page_go_live(_camps(current=["S1"]), _status([], unreadable=True), None) == "unknown"
    )


def _m(camps, rows, rec=None, warnings=("records-disagree",)):
    return {
        "campaigns": {"campaigns": camps},
        "status": {"sequences": rows, "snapshot": {"unreadable": False}},
        "reconciliation": rec or {"ok": True, "in_ledger_only": [], "in_snapshot_only": []},
        "warnings": list(warnings),
    }


C1 = {
    "slug": "c1",
    "title": "One",
    "sequences": [{"sequence_id": "S1"}],
    "archived": [],
    "actuals": {"sent": 5},
    "archived_actuals": {},
}


def test_a_scoped_page_drops_a_sum_it_does_not_double_count():
    assert health.scoped_trust(_m([C1], [{"id": "S1", "sent": 5}]), {"S1"})["warnings"] == []


def test_a_scoped_page_keeps_a_records_gap_on_its_own_sequence_only():
    rec = {"ok": False, "in_ledger_only": ["S1", "S9"], "in_snapshot_only": ["GHOST"]}
    out = health.scoped_trust(_m([C1], [{"id": "S1", "sent": 5}], rec), {"S1"})
    assert out == {
        "warnings": ["records-disagree"],
        "reconciliation": {"ok": False, "in_ledger_only": ["S1"], "in_snapshot_only": []},
    }
    rec = {"ok": False, "in_ledger_only": ["S9"], "in_snapshot_only": ["GHOST"]}
    out = health.scoped_trust(_m([C1], [{"id": "S1", "sent": 5}], rec), {"S1"})
    assert out["warnings"] == [] and out["reconciliation"]["ok"] is True


def test_old_and_unreadable_figures_stay_on_a_scoped_page():
    m = _m([C1], [{"id": "S1", "sent": 5}], warnings=("records-disagree", "figures-old"))
    assert health.scoped_trust(m, {"S1"})["warnings"] == ["figures-old"]
    m = _m([C1], [], warnings=("unreadable",))
    m["status"]["snapshot"]["unreadable"] = True
    assert health.scoped_trust(m, {"S1"})["warnings"] == ["unreadable"]


def test_the_strip_names_the_campaigns_behind_a_disagreement():
    c2 = {**C1, "slug": "c2", "title": "Two"}
    c3 = {"slug": "c3", "title": "Three", "sequences": [{"sequence_id": "S3"}], "archived": []}
    m = _m([C1, c2, c3], [{"id": "S1", "sent": 5}, {"id": "S3", "sent": 1}])
    assert health.shared_sequences(m) == {"S1": ["One", "Two"]}
    assert health.disagree_names(m) == ["One", "Two"]
    m = _m([C1, c3], [{"id": "S3", "sent": 1}, {"id": "S3", "sent": 1}])
    assert health.repeated_rows(m) == ["S3"] and health.disagree_names(m) == ["Three"]


def test_a_shared_sequence_that_adds_up_is_not_named():
    """A shared id whose figures still add up is not what a snapshot-only gap is about: the
    strip must not blame One and Two for an X that no campaign lists."""
    zero = {**C1, "actuals": {"sent": 0}}
    c2 = {**zero, "slug": "c2", "title": "Two"}
    rec = {"ok": False, "in_ledger_only": [], "in_snapshot_only": ["X"]}
    m = _m([zero, c2], [{"id": "S1", "sent": 0}, {"id": "X", "sent": 0}], rec)
    assert health.shared_sequences(m) == {"S1": ["One", "Two"]}
    assert health.disagree_names(m) == []


def test_a_sequence_listed_as_archived_is_still_shared():
    c3 = {
        "slug": "c3",
        "title": "Three",
        "sequences": [],
        "archived": [{"sequence_id": "S1"}],
        "archived_actuals": {"sent": 5},
    }
    m = _m([C1, c3], [{"id": "S1", "sent": 5}])
    assert health.shared_sequences(m) == {"S1": ["One", "Three"]}
    assert health.disagree_names(m) == ["One", "Three"]


def test_a_scoped_page_orders_its_own_gap_before_old_figures():
    rec = {"ok": False, "in_ledger_only": ["S1"], "in_snapshot_only": []}
    m = _m([C1], [{"id": "S1", "sent": 5}], rec, warnings=("records-disagree", "figures-old"))
    assert health.scoped_trust(m, {"S1"})["warnings"] == ["records-disagree", "figures-old"]

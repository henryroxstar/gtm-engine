"""PS20 T1.2/T1.3 — one derivation for 'people contacted', and a sum that must close."""

from gtm_core.email_campaign_dashboard.aggregate import _scope_figures, sent_heading


def _m(camps, rows, unreadable=False):
    return {
        "campaigns": {"campaigns": camps},
        "status": {"sequences": rows, "snapshot": {"unreadable": unreadable}},
    }


CAMP = {
    "slug": "a",
    "targets": {},
    "sequences": [{"sequence_id": "S1"}, {"sequence_id": "S2"}],
    "archived": [{"sequence_id": "S3"}, {"sequence_id": "S4"}],
    "actuals": {"sent": 10, "loaded": 20, "replied": 1, "meetings": 0},
    "archived_actuals": {"sent": 24, "loaded": 30, "replied": 0, "meetings": 0},
}
ROWS = [
    {"id": "S1", "sent": 7},
    {"id": "S2", "sent": 3},
    {"id": "S3", "sent": 17},
    {"id": "S4", "sent": 7},
    {"id": "X", "sent": 1},
]


def test_contacted_splits_current_earlier_not_linked():
    fig = _scope_figures(_m([CAMP], ROWS))
    assert fig["contacted"] == ({"current": 10, "earlier": 24, "not_linked": 1}, None)
    assert fig["loaded"] == (20, None) and fig["sum_ok"] is True
    assert fig["replied"] == (1, None)
    assert fig["meetings"] == (0, None)


def test_current_comes_from_actuals_not_rows_and_undercount_breaks_the_sum():
    camp = {**CAMP, "actuals": {**CAMP["actuals"], "sent": 8}}
    fig = _scope_figures(_m([camp], ROWS))
    assert fig["contacted"][0]["current"] == 8 and fig["sum_ok"] is False


def test_a_row_in_two_campaigns_breaks_the_sum():
    twin = {
        **CAMP,
        "slug": "b",
        "archived": [],
        "archived_actuals": {"sent": 0},
        "sequences": [{"sequence_id": "S1"}],
        "actuals": {"sent": 7, "loaded": 7},
    }
    assert _scope_figures(_m([CAMP, twin], ROWS))["sum_ok"] is False


def test_unreadable_refuses_with_a_reason():
    fig = _scope_figures(_m([CAMP], [], unreadable=True))
    contacted, why = fig["contacted"]
    assert contacted is None and why
    assert fig["loaded"] == (None, why)
    assert fig["replied"] == (None, why)
    assert fig["meetings"] == (None, why)


def test_missing_status_refuses_like_unreadable_not_a_readable_empty_snapshot():
    """A model built with no ``status`` key at all (e.g. a caller that dropped it) must
    refuse the same way an unreadable snapshot does — reading it as an empty-but-readable
    snapshot would silently drop every campaign's ``actuals`` out of `sum_ok`'s comparison
    and mislabel an ordinary page "counted twice"."""
    fig = _scope_figures({"campaigns": {"campaigns": [CAMP]}})
    contacted, why = fig["contacted"]
    assert contacted is None and why == "no sending figures in this model"
    assert fig["loaded"] == (None, why)
    assert fig["replied"] == (None, why)
    assert fig["meetings"] == (None, why)
    assert fig["sum_ok"] is True


def test_sent_heading_is_derived():
    z = {"current": 0, "earlier": 0, "not_linked": 0}
    assert sent_heading(None) == "Sending figures unavailable"
    assert sent_heading(z) == "Nothing has been sent"
    assert sent_heading({**z, "earlier": 24}) == "Nothing sent in the current campaigns"
    assert sent_heading({**z, "current": 10, "earlier": 24}) == "10 people contacted so far"
    assert sent_heading({**z, "not_linked": 1}) == "1 person contacted so far"
    assert (
        sent_heading({"current": 1234, "earlier": 0, "not_linked": 0})
        == "1,234 people contacted so far"
    )

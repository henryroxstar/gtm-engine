"""PS20 T3.1/T3.5/T3.6 — reply labels and bounces are `_scope_figures` figures too: the
same refusal contract as `contacted`/`loaded`/`replied`/`meetings` (unreadable or missing
`status` refuses with a reason), summed the same way — over campaigns' own `actuals`,
never re-derived from raw rows — plus the `bounce_rate` helper the Results view and the
bounce-risk pill will share.
"""

from gtm_core.email_campaign_dashboard.aggregate import _scope_figures, bounce_rate
from gtm_core.email_campaign_dashboard.config import BOUNCE_RISK_PCT

LABEL_FIELDS = (
    "interested",
    "not_interested",
    "not_now",
    "out_of_office",
    "unsubscribed",
    "do_not_contact",
)


def _m(camps, rows, unreadable=False):
    return {
        "campaigns": {"campaigns": camps},
        "status": {"sequences": rows, "snapshot": {"unreadable": unreadable}},
    }


def _actuals(sent, **overrides):
    row = {"sent": sent, "loaded": sent * 2, "replied": 1, "meetings": 0}
    row.update(dict.fromkeys(LABEL_FIELDS, 0))
    row.update({"bounced": 0, "delivered": 0, "bounce_unavailable": 0})
    row.update(overrides)
    return row


CAMP_A = {
    "slug": "a",
    "targets": {},
    "sequences": [{"sequence_id": "S1"}],
    "archived": [],
    "actuals": _actuals(
        10,
        interested=2,
        not_interested=1,
        not_now=1,
        unsubscribed=1,
        bounced=5,
        delivered=95,
    ),
    # NON-ZERO on purpose: an earlier run's labels and bounces must never leak into the
    # scope's figures, and all-zero archived_actuals could not tell a current-only sum from
    # one that also adds these in.
    "archived_actuals": _actuals(
        0,
        interested=40,
        not_interested=41,
        not_now=42,
        out_of_office=43,
        unsubscribed=44,
        do_not_contact=45,
        bounced=46,
        delivered=470,
        bounce_unavailable=48,
    ),
}
CAMP_B = {
    "slug": "b",
    "targets": {},
    "sequences": [{"sequence_id": "S2"}],
    "archived": [],
    "actuals": _actuals(
        4,
        interested=1,
        out_of_office=1,
        do_not_contact=1,
        bounced=2,
        delivered=38,
        bounce_unavailable=1,
    ),
    "archived_actuals": _actuals(0),
}
ROWS = [{"id": "S1", "sent": 10}, {"id": "S2", "sent": 4}]


def test_labels_sum_across_campaigns_actuals():
    fig = _scope_figures(_m([CAMP_A, CAMP_B], ROWS))
    labels, why = fig["labels"]
    assert why is None
    assert labels == {
        "interested": 3,
        "not_interested": 1,
        "not_now": 1,
        "out_of_office": 1,
        "unsubscribed": 1,
        "do_not_contact": 1,
    }


def test_bounces_sum_across_campaigns_actuals():
    fig = _scope_figures(_m([CAMP_A, CAMP_B], ROWS))
    bounces, why = fig["bounces"]
    assert why is None
    assert bounces == {"bounced": 7, "delivered": 133, "unavailable": 1}


def test_labels_agree_with_the_campaigns_own_actuals_sum():
    """PS20's founding incident, one level up: the scope figure and the per-campaign
    figures it is built from must be the SAME arithmetic, not two numbers that happen to
    agree on this one fixture."""
    camps = [CAMP_A, CAMP_B]
    fig = _scope_figures(_m(camps, ROWS))
    labels, _ = fig["labels"]
    manual = {f: sum(c["actuals"][f] for c in camps) for f in LABEL_FIELDS}
    assert labels == manual


def test_labels_and_bounces_refuse_when_unreadable():
    fig = _scope_figures(_m([CAMP_A], [], unreadable=True))
    labels, why = fig["labels"]
    bounces, why2 = fig["bounces"]
    assert labels is None and why
    assert bounces is None and why2 == why


def test_labels_and_bounces_refuse_when_status_is_missing():
    """A model built with no ``status`` key at all refuses exactly like an unreadable
    snapshot — see ``test_scope_figures_contacted.py``'s sibling case."""
    fig = _scope_figures({"campaigns": {"campaigns": [CAMP_A]}})
    labels, why = fig["labels"]
    bounces, why2 = fig["bounces"]
    assert labels is None and why == "no sending figures in this model"
    assert bounces is None and why2 == why


def test_bounce_rate_thresholds():
    assert bounce_rate(29, 971) == 2.9
    assert bounce_rate(30, 970) == 3.0
    assert bounce_rate(31, 969) == 3.1


def test_bounce_rate_has_no_value_with_no_denominator():
    assert bounce_rate(0, 0) is None


def test_bounce_risk_pill_fires_strictly_above_the_threshold():
    assert bounce_rate(29, 971) <= BOUNCE_RISK_PCT
    assert bounce_rate(30, 970) <= BOUNCE_RISK_PCT  # exactly 3.0% — no pill
    assert bounce_rate(31, 969) > BOUNCE_RISK_PCT

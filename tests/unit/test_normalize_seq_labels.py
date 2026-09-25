"""PS20 Phase 3 T3.1 — ``_normalize_seq`` keeps the sending tool's reply labels
(notInterested/notNow/outOfOffice/unsubscribed/doNotContact) on both the raw-payload and
flat-dict paths, and records ``bounce_source`` by whether the per-email status block
CARRIES an actual bounce figure -- a present, non-blank ``bounced``/``hardBounced``/
``softBounced``/``blockBounced`` key -- not by whether the block merely exists or its
computed value happens to be truthy, so a missing or blank figure is never read as a
genuine 0.
"""

from __future__ import annotations

import pytest

from gtm_core import prospects_dashboard as pd


def _raw(prospects_extra=None, emails_status=None):
    """A fictional raw Saleshandy ``get_sequence_stats`` payload."""
    prospects = {
        "total": "10",
        "contacted": "8",
        "upcoming": "1",
        "waiting": "1",
        "open": "4",
        "replied": "2",
        "meetingBooked": "1",
        "interested": "1",
    }
    if prospects_extra:
        prospects.update(prospects_extra)
    payload = {
        "sequenceId": "seq-glacier-ridge",
        "sequenceName": "Glacier Ridge Outreach",
        "status": "active",
        "prospects": [prospects],
    }
    if emails_status is not None:
        payload["emails"] = {"status": emails_status}
    return payload


def test_raw_payload_keeps_the_five_reply_labels():
    raw = _raw(
        prospects_extra={
            "notInterested": "4",
            "notNow": "3",
            "outOfOffice": "2",
            "unsubscribed": "1",
            "doNotContact": "5",
        }
    )
    n = pd._normalize_seq(raw)
    assert n["not_interested"] == 4
    assert n["not_now"] == 3
    assert n["out_of_office"] == 2
    assert n["unsubscribed"] == 1
    assert n["do_not_contact"] == 5


def test_raw_payload_missing_the_five_labels_counts_each_as_zero():
    raw = _raw()  # no notInterested/notNow/outOfOffice/unsubscribed/doNotContact keys
    n = pd._normalize_seq(raw)
    assert n["not_interested"] == 0
    assert n["not_now"] == 0
    assert n["out_of_office"] == 0
    assert n["unsubscribed"] == 0
    assert n["do_not_contact"] == 0


def test_flat_dict_passes_the_five_labels_through():
    flat = {
        "id": "seq-tidewater",
        "name": "Tidewater Renewal",
        "status": "active",
        "not_interested": "6",
        "not_now": "7",
        "out_of_office": "8",
        "unsubscribed": "9",
        "do_not_contact": "10",
    }
    n = pd._normalize_seq(flat)
    assert n["not_interested"] == 6
    assert n["not_now"] == 7
    assert n["out_of_office"] == 8
    assert n["unsubscribed"] == 9
    assert n["do_not_contact"] == 10


def test_flat_dict_bounce_source_is_none():
    flat = {"id": "seq-tidewater", "name": "Tidewater Renewal", "status": "active", "bounced": "3"}
    n = pd._normalize_seq(flat)
    assert n["bounce_source"] is None


def test_genuine_zero_bounces_in_the_email_block_are_not_replaced_by_the_prospect_figure():
    """The per-email status block is PRESENT with a genuine 0 bounced count, while the
    looser prospect-level figure says 5. The block must win because it is present --
    not because its value happens to be truthy (the old ``... or p.bounced`` fallback
    used truthiness and so replaced a genuine 0 with the prospect-level 5).
    """
    raw = _raw(
        prospects_extra={"bounced": "5"},
        emails_status={
            "delivered": "40",
            "opened": "10",
            "replied": "2",
            "bounced": "0",
            "hardBounced": "0",
            "softBounced": "0",
            "blockBounced": "0",
        },
    )
    n = pd._normalize_seq(raw)
    assert n["bounced"] == 0
    assert n["bounce_source"] == "emails"


def test_no_email_block_falls_back_to_the_prospect_level_figure():
    raw = _raw(prospects_extra={"bounced": "3"})  # no "emails" key at all
    n = pd._normalize_seq(raw)
    assert n["bounced"] == 3
    assert n["bounce_source"] == "prospects"


def test_empty_email_status_block_falls_back_to_prospects():
    """A block that is a dict but carries none of the four bounce keys is NOT the
    source -- an absent figure must never be read as a genuine 0."""
    raw = _raw(prospects_extra={"bounced": "3"}, emails_status={})
    n = pd._normalize_seq(raw)
    assert n["bounced"] == 3
    assert n["bounce_source"] == "prospects"


def test_email_status_block_with_only_delivered_falls_back_to_prospects():
    """``delivered`` is not a bounce key, so a block carrying only it still has no
    bounce figure -- same "absence refuses" outcome as an empty block."""
    raw = _raw(prospects_extra={"bounced": "3"}, emails_status={"delivered": "40"})
    n = pd._normalize_seq(raw)
    assert n["bounced"] == 3
    assert n["bounce_source"] == "prospects"


def test_email_status_block_with_a_genuine_zero_bounced_is_the_source():
    """A block carrying ``bounced: 0`` -- and nothing else -- still counts: the key is
    PRESENT, so it wins over the looser prospect-level figure even though it is 0."""
    raw = _raw(prospects_extra={"bounced": "9"}, emails_status={"bounced": "0"})
    n = pd._normalize_seq(raw)
    assert n["bounced"] == 0
    assert n["bounce_source"] == "emails"


@pytest.mark.parametrize(
    "emails_value",
    [
        ["not", "a", "dict"],  # non-dict `emails`
        "not a dict",
        None,
        {"status": None},  # dict `emails`, non-dict `status`
        {"status": ["not", "a", "dict"]},
        {"status": "not a dict"},
    ],
)
def test_non_dict_emails_or_status_falls_back_to_prospects_without_crashing(emails_value):
    raw = _raw(prospects_extra={"bounced": "3"})
    raw["emails"] = emails_value
    n = pd._normalize_seq(raw)
    assert n["bounced"] == 3
    assert n["bounce_source"] == "prospects"

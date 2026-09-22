"""The LIVE provider payload shapes, pinned (verified 2026-09-22 against a real account).

Every field name in the inbound lane was a guess until somebody read the API, and every
guess was wrong. The 2026-09-21 change corrected the PATHS and left the fields — so the
sweep fetched threads and read nothing from them: `t.get("id")` was `None`, so the thread
id was empty, so every candidate was skipped. Opt-out detection was inert against the real
API, silently, for as long as it had existed.

These fixtures are the real shapes with the identities replaced (the live thread is a real
prospect's reply — §R9). They exist so the next field rename fails a test instead of
quietly switching detection off again.
"""

from __future__ import annotations

from agent.optout_sweep import _normalise_thread
from gtm_core.optout_watch import (
    find_optouts_in_thread,
    first_inbound_message,
    message_body,
    message_is_inbound,
    message_sender,
    new_threads,
    thread_id_of,
    thread_ts,
)

#: A unified-inbox LIST item, exactly the live key set, fictionalised.
LIVE_ITEM = {
    "bgColor": "#fff",
    "categoryId": 8,
    "categoryUpdatedBy": None,
    "content": "",
    "emailId": "gw4R1nJgba",
    "emailThreadId": "vP7on0yYdP",
    "entity": None,
    "entityType": 1,
    "failedReason": None,
    "fromEmail": "dana@acme.example",
    "fromName": "Dana Example",
    "hashId": "vP7on0yYdP",
    "isEmailAccountDeleted": 0,
    "isFinished": 0,
    "isRead": 1,
    "isRepliedByProspect": 1,
    "isSubSequence": 0,
    "isUnsubscribed": 0,
    "prospectName": "Dana Example",
    "scheduleContent": None,
    "scheduledAt": None,
    "sentAt": "2026-08-11T14:34:43.000Z",
    "sentiment": "Negative",
    "sequenceId": "Y8aLLYelaN",
    "sequenceName": "A sequence",
    "sequenceUser": None,
    "status": None,
    "subject": "Re: your note",
    "timezone": None,
}

#: `get_thread`'s payload: a BARE LIST of messages, not an object with `messages`.
LIVE_THREAD_DETAIL = {
    "message": "Email thread fetched",
    "payload": [
        {
            "emailId": "gw4R1nJgba",
            "fromEmail": "sender@ourcompany.example",
            "fromName": "Our Sender",
            "fromProspectId": None,  # we sent this one
            "content": "<p>original outreach</p>",
            "sentAt": "2026-08-10T09:00:00.000Z",
            "subject": "your note",
            "to": ["dana@acme.example"],
            "toProspectId": 209175356,
        },
        {
            "emailId": "hx5S2oKhcb",
            "fromEmail": "dana@acme.example",
            "fromName": "Dana Example",
            "fromProspectId": 209175356,  # the PROSPECT sent this one
            "content": "<p>Unsubscribe</p>",
            "sentAt": "2026-08-11T14:34:43.000Z",
            "subject": "Re: your note",
            "to": ["sender@ourcompany.example"],
            "toProspectId": None,
        },
    ],
}


def test_the_thread_id_is_email_thread_id_not_id():
    """A live item has NO `id`. Reading one gave "" and the sweep skipped every thread."""
    assert "id" not in LIVE_ITEM
    assert thread_id_of(LIVE_ITEM) == "vP7on0yYdP"


def test_the_list_timestamp_is_sent_at():
    """`lastMessageTimestamp` is absent, so the watermark saw "" for every thread — it
    could neither select new threads nor advance past them."""
    assert "lastMessageTimestamp" not in LIVE_ITEM
    assert thread_ts(LIVE_ITEM) == "2026-08-11T14:34:43.000Z"
    assert new_threads({"threads": [LIVE_ITEM]}, {}) == [LIVE_ITEM]


def test_the_message_fields_are_content_from_email_and_sent_at():
    msg = LIVE_THREAD_DETAIL["payload"][1]
    assert "body" not in msg and "senderEmail" not in msg and "direction" not in msg
    assert message_body(msg) == "<p>Unsubscribe</p>"
    assert message_sender(msg) == "dana@acme.example"


def test_direction_comes_from_from_prospect_id():
    """There is no `direction` field. A message the PROSPECT sent carries a
    `fromProspectId`; one we sent has None — a positive signal, so direction IS known."""
    ours, outbound_known = message_is_inbound(LIVE_THREAD_DETAIL["payload"][0])
    theirs, inbound_known = message_is_inbound(LIVE_THREAD_DETAIL["payload"][1])
    assert (ours, outbound_known) == (False, True)
    assert (theirs, inbound_known) == (True, True)


def test_the_bare_list_payload_normalises_for_the_matcher():
    norm = _normalise_thread(LIVE_THREAD_DETAIL, LIVE_ITEM, thread_id_of(LIVE_ITEM))
    assert norm["threadId"] == "vP7on0yYdP"
    assert norm["subject"] == "Re: your note"  # the subject is on the LIST item
    assert len(norm["messages"]) == 2


def test_the_whole_live_shape_detects_the_optout_end_to_end():
    """The regression that matters: this exact shape produced NO detection before the fix."""
    norm = _normalise_thread(LIVE_THREAD_DETAIL, LIVE_ITEM, thread_id_of(LIVE_ITEM))
    match = find_optouts_in_thread(norm)
    assert match is not None, "the live shape must yield an opt-out match"
    assert match.email == "dana@acme.example"
    assert match.thread_id == "vP7on0yYdP"
    assert match.message_ts == "2026-08-11T14:34:43.000Z"
    assert match.direction_known is True


def test_the_matcher_does_not_match_our_own_outbound_copy():
    """The other half: our outreach must never be read as the prospect opting out."""
    ours_only = {
        "message": "ok",
        "payload": [
            dict(LIVE_THREAD_DETAIL["payload"][0], content="<p>reply 'stop' to unsubscribe</p>")
        ],
    }
    norm = _normalise_thread(ours_only, LIVE_ITEM, "vP7on0yYdP")
    assert find_optouts_in_thread(norm) is None


def test_first_inbound_message_picks_the_prospects_message():
    norm = _normalise_thread(LIVE_THREAD_DETAIL, LIVE_ITEM, "vP7on0yYdP")
    msg = first_inbound_message(norm)
    assert message_sender(msg) == "dana@acme.example"


def test_the_outcome_ids_are_strings_not_ints():
    """`get_outcomes` returns opaque string ids; `categoryIds` takes those. The filter
    signature said `list[int]`, which would have coerced or failed on a real id."""
    import inspect

    from agent.mcp.saleshandy.server import get_inbox_threads, get_outcomes

    sig = inspect.signature(get_inbox_threads)
    assert "list[str]" in str(sig.parameters["category_ids"].annotation).replace(" ", "")
    # and the outcomes read takes NO paging — passing page/limit is an HTTP 400 live
    assert not [p for p in inspect.signature(get_outcomes).parameters if p != "self"]


def test_the_inline_category_id_is_a_different_id_space():
    """A live item DOES carry `categoryId`, but it is a small int from a different space
    than the opaque outcome ids the filter takes — so it is not a shortcut, and filter
    membership stays the only reliable join."""
    assert isinstance(LIVE_ITEM["categoryId"], int)
    assert len("edlPy0wLq0") > 3 and not str(LIVE_ITEM["categoryId"]).isalpha()

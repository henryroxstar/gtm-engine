"""Tests for the deterministic opt-out-reply detector (gtm_core.optout_watch)."""

from __future__ import annotations

import json

from gtm_core import optout_watch as ow

# --------------------------------------------------------------------------- is_optout


def test_a_bare_unsubscribe_reply_matches():
    """Regression: a single-word "Unsubscribe" reply — the shape of the real incident
    (2026-08-11) that sat un-suppressed for six days because nothing was watching."""
    assert ow.is_optout("Unsubscribe")


def test_common_phrasings_match():
    for text in (
        "please remove me from this list",
        "not interested, thanks",
        "no longer interested in this",
        "please opt me out",
        "opt out please",
        "STOP",
        "Stop.",
        "please stop emailing me",
        "stop contacting me",
        "take me off this list",
    ):
        assert ow.is_optout(text), text


def test_unrelated_text_does_not_match():
    for text in (
        "Sounds interesting, tell me more",
        "We run nonstop across timezones",
        "Let us stop and think about this proposal in more detail before deciding",
        "The vendor offers an opt-in trial for new customers",
        # "stop by" is a visit, not a suppression request — the one carve-out in
        # an otherwise recall-biased matcher.
        "Please stop by our office next week and we can talk it through",
        "Happy to chat — please stop by the booth at the conference",
        "",
    ):
        assert not ow.is_optout(text), text


def test_recall_bias_soft_nos_still_count_as_optouts():
    """`inbound-triage-rubric` is explicit that "not interested" counts and that
    ambiguous reads as an opt-out. Affordable only because this escalates, never acts —
    a regression that "tightens" these into misses is the failure mode to catch."""
    for text in (
        "Not interested right now, maybe circle back next year",
        "Please stop emailing me about this",
    ):
        assert ow.is_optout(text), text


def test_bare_stop_requires_a_short_message():
    """A short reply reading essentially "stop" is a signal; "stop" buried in a long
    reply about something else is not — avoids flooding the operator with noise."""
    assert ow.is_optout("stop")
    assert not ow.is_optout(
        "I think we should stop and reconsider our vendor selection process next quarter"
    )


# --------------------------------------------------------------------------- find_optouts_in_thread


def test_finds_the_first_inbound_optout_message():
    thread = {
        "id": "t1",
        "subject": "re: agents on regulated data",
        "messages": [
            {
                "from": "henry@x.com",
                "body": "hi there",
                "sentAt": "2026-08-01T00:00:00Z",
                "direction": "outbound",
            },
            {
                "from": "jordan@brackenhealth.example",
                "body": "Unsubscribe",
                "sentAt": "2026-08-11T14:00:00Z",
                "direction": "inbound",
            },
        ],
    }
    m = ow.find_optouts_in_thread(thread)
    assert m is not None
    assert m.email == "jordan@brackenhealth.example"
    assert m.thread_id == "t1"
    assert m.direction_known is True


def test_outbound_only_direction_is_skipped():
    thread = {
        "id": "t2",
        "subject": "s",
        "messages": [
            {
                "from": "henry@x.com",
                "body": "please unsubscribe from our internal list",
                "direction": "outbound",
            },
        ],
    }
    assert ow.find_optouts_in_thread(thread) is None


def test_unknown_direction_is_still_scanned_but_flagged():
    """Fail-open toward coverage when direction is missing (endpoint shape unverified),
    fail-closed toward auto-action — direction_known lets a human judge."""
    thread = {"id": "t3", "subject": "s", "messages": [{"from": "x@y.com", "body": "unsubscribe"}]}
    m = ow.find_optouts_in_thread(thread)
    assert m is not None
    assert m.direction_known is False


def test_no_messages_no_match():
    assert ow.find_optouts_in_thread({"id": "t4", "subject": "s", "messages": []}) is None


def test_snippet_is_truncated_and_whitespace_normalized():
    long_body = "unsubscribe " + ("x" * 400)
    thread = {
        "id": "t5",
        "subject": "s",
        "messages": [{"from": "a@b.com", "body": long_body, "direction": "inbound"}],
    }
    m = ow.find_optouts_in_thread(thread)
    assert len(m.snippet) <= 300
    assert m.snippet.endswith("…")


# --------------------------------------------------------------------------- watermark


def test_watermark_round_trip(tmp_path):
    p = tmp_path / "state.json"
    assert ow.load_watermark(p) == {}
    ow.save_watermark(p, {"global": "2026-08-01T00:00:00Z"})
    assert ow.load_watermark(p) == {"global": "2026-08-01T00:00:00Z"}


def test_missing_watermark_file_is_empty_not_an_error(tmp_path):
    assert ow.load_watermark(tmp_path / "nope.json") == {}


def test_corrupt_watermark_file_is_empty_not_an_error(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json", encoding="utf-8")
    assert ow.load_watermark(p) == {}


def test_new_threads_filters_by_last_message_at():
    payload = {
        "threads": [
            {"id": "a", "lastMessageAt": "2026-08-01T00:00:00Z"},
            {"id": "b", "lastMessageAt": "2026-08-15T00:00:00Z"},
        ]
    }
    result = ow.new_threads(payload, {"global": "2026-08-10T00:00:00Z"})
    assert [t["id"] for t in result] == ["b"]


def test_new_threads_empty_watermark_returns_everything():
    payload = {"threads": [{"id": "a", "lastMessageAt": "2026-08-01T00:00:00Z"}]}
    assert len(ow.new_threads(payload, {})) == 1


def test_new_threads_missing_timestamp_is_treated_as_new():
    """A thread with no lastMessageAt is never silently skipped."""
    payload = {"threads": [{"id": "a"}]}
    assert len(ow.new_threads(payload, {"global": "2026-08-10T00:00:00Z"})) == 1


def test_advance_watermark_takes_the_max_timestamp():
    payload = {
        "threads": [
            {"id": "a", "lastMessageAt": "2026-08-01T00:00:00Z"},
            {"id": "b", "lastMessageAt": "2026-08-15T00:00:00Z"},
        ]
    }
    out = ow.advance_watermark({}, payload)
    assert out["global"] == "2026-08-15T00:00:00Z"


def test_advance_watermark_never_goes_backwards():
    payload = {"threads": [{"id": "a", "lastMessageAt": "2026-08-01T00:00:00Z"}]}
    out = ow.advance_watermark({"global": "2026-08-15T00:00:00Z"}, payload)
    assert out["global"] == "2026-08-15T00:00:00Z"


# --------------------------------------------------------------------------- record_optout_event


class _FakeLedgers:
    def __init__(self):
        self.records = []

    def append_history(self, record):
        self.records.append(record)


def test_record_optout_event_writes_an_auditable_history_row():
    ledgers = _FakeLedgers()
    match = ow.OptOutMatch(
        thread_id="t1",
        email="jordan@brackenhealth.example",
        subject="re: agents on regulated data",
        message_ts="2026-08-11T14:00:00Z",
        snippet="Unsubscribe",
        direction_known=True,
    )
    ow.record_optout_event(ledgers, match, escalated=True)
    assert len(ledgers.records) == 1
    row = ledgers.records[0]
    assert row["event"] == "optout_detected"
    assert row["email"] == "jordan@brackenhealth.example"
    assert row["escalated"] is True
    assert "DNC" in row["action_required"]


def test_record_optout_event_notes_when_escalation_failed():
    ledgers = _FakeLedgers()
    match = ow.OptOutMatch("t1", "a@b.com", "s", "ts", "unsubscribe", True)
    ow.record_optout_event(ledgers, match, escalated=False)
    assert ledgers.records[0]["escalated"] is False


# --------------------------------------------------------------------------- CLI


def test_cli_check_exits_1_when_optouts_found(tmp_path, capsys):
    threads_json = tmp_path / "threads.json"
    state = tmp_path / "state.json"
    payload = {
        "payload": {
            "threads": [
                {
                    "id": "t1",
                    "lastMessageAt": "2026-08-11T14:00:00Z",
                    "_thread": {
                        "id": "t1",
                        "subject": "s",
                        "messages": [
                            {"from": "a@b.com", "body": "Unsubscribe", "direction": "inbound"}
                        ],
                    },
                }
            ]
        }
    }
    # Note: `check` reads the *unwrapped* payload object, matching the sweep script's
    # call shape (it passes `threads_body.get("payload")` through).
    threads_json.write_text(json.dumps(payload["payload"]), encoding="utf-8")
    rc = ow.main(["check", "--threads-json", str(threads_json), "--state", str(state)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "a@b.com" in out
    # A second run against the same file must not re-report the same thread.
    rc2 = ow.main(["check", "--threads-json", str(threads_json), "--state", str(state)])
    assert rc2 == 0


def test_cli_check_exits_0_when_clean(tmp_path):
    threads_json = tmp_path / "threads.json"
    state = tmp_path / "state.json"
    threads_json.write_text(json.dumps({"threads": []}), encoding="utf-8")
    rc = ow.main(["check", "--threads-json", str(threads_json), "--state", str(state)])
    assert rc == 0

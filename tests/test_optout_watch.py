"""Tests for the deterministic opt-out-reply detector (gtm_core.optout_watch)."""

from __future__ import annotations

import json

import pytest

from gtm_core import optout_watch as ow

# --------------------------------------------------------------------------- is_optout


def test_a_bare_unsubscribe_reply_matches():
    """Regression: a single-word "Unsubscribe" reply — the shape of the real incident
    (2026-08-11) that sat un-suppressed for six days because nothing was watching."""
    assert ow.is_optout("Unsubscribe")


def test_common_phrasings_match():
    for text in (
        "please remove me from this list",
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


def test_an_explicit_request_to_stop_is_still_an_optout():
    """The recall bias that remains, and must: anything that ASKS to be left alone counts,
    however phrased. Affordable only because this escalates and never acts."""
    for text in (
        "Please stop emailing me about this",
        "take me off this list",
        "unsubscribe me please",
        "Not interested — please remove me",  # a soft no that DOES ask: still an opt-out
    ):
        assert ow.is_optout(text), text


# ─────────────────────────────────────── SC12: a soft no is not a suppression request
#
# "Not interested" used to match here. It carries no legal deadline, does not belong on a
# Do Not Contact list, and routing it through the opt-out path spent the same-day
# compliance alert — the one with a real deadline — on a reply that has none. It also
# permanently removed people who had only said "not right now".


@pytest.mark.parametrize(
    "text",
    [
        "not interested, thanks",
        "no longer interested in this",
        "Not interested right now, maybe circle back next year",
        "no thanks, we're all set",
        "not the right time for us",
    ],
)
def test_a_soft_no_is_no_longer_an_optout(text):
    assert not ow.is_optout(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Not interested — please remove me",
        "not interested, take me off this list",
        "no longer interested, unsubscribe me",
    ],
)
def test_a_soft_no_that_also_asks_to_be_removed_is_still_an_optout(text):
    """The boundary: SC12 split the SOFT no out, not the request to stop. A reply doing
    both is still a suppression request — `remove me` earns that on its own merits."""
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
                "from": "avery@atlas.example",
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
                "from": "avery@atlas.example",
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


# --- documented field names (2026-09-21) ------------------------------------------------
#
# The endpoint these payloads come from was fixed on this date after 404ing on its first
# real account (agent.mcp.saleshandy.server.get_thread / get_inbox_threads). Its confirmed
# fields are `senderEmail`/`timestamp`/`threadId`; the ORIGINAL assumed fields
# (`from`/`sentAt`/`id`, still exercised by the tests above) are read as a fallback because
# the doc excerpt that fixed the path did not confirm they are ABSENT. These two tests pin
# that both names work, deliberately, so a later cleanup cannot drop one by accident.


def test_documented_field_names_are_read():
    thread = {
        "threadId": "t1",
        "subject": "re: agents on regulated data",
        "messages": [
            {
                "senderEmail": "jordan@brackenhealth.example",
                "body": "Unsubscribe",
                "timestamp": "2026-08-11T14:00:00Z",
                "direction": "inbound",
            }
        ],
    }
    m = ow.find_optouts_in_thread(thread)
    assert m is not None
    assert m.email == "jordan@brackenhealth.example"
    assert m.thread_id == "t1"
    assert m.message_ts == "2026-08-11T14:00:00Z"


def test_the_live_verified_key_wins_when_both_are_present():
    """Not just "either works" — the LIVE-VERIFIED name is preferred if a payload somehow
    carried both, so a provider migration lands cleanly without this code choosing the
    stale value.

    Which name is "confirmed" changed on 2026-09-22, and that is the point. This test used
    to assert `timestamp` and `senderEmail` won, because those were the assumed names. A
    real read showed the live fields are `sentAt`, `fromEmail` and `content` — so the
    precedence flipped. The assumption was never wrong on purpose; it was just never
    checked, which is the failure this whole change exists to end."""
    thread = {
        "threadId": "t1",
        "id": "stale-id",
        "messages": [
            {
                "fromEmail": "live@x.com",
                "senderEmail": "assumed@x.com",
                "from": "stale@x.com",
                "content": "unsubscribe",
                "body": "sounds great, tell me more",
                "sentAt": "2026-09-01T00:00:00Z",
                "timestamp": "2026-01-01T00:00:00Z",
                "direction": "inbound",
            }
        ],
    }
    m = ow.find_optouts_in_thread(thread)
    assert m.thread_id == "t1"
    assert m.email == "live@x.com"
    assert m.message_ts == "2026-09-01T00:00:00Z"
    # `content` is the live body, so the opt-out in it is what matched — not `body`.
    assert "unsubscribe" in m.snippet


def test_new_threads_and_advance_watermark_read_the_documented_timestamp():
    payload = {"threads": [{"id": "t1", "lastMessageTimestamp": "2026-08-11T14:00:00Z"}]}
    assert ow.new_threads(payload, {}) == payload["threads"]
    assert ow.advance_watermark({}, payload) == {"global": "2026-08-11T14:00:00Z"}


# ------------------------------------------------------------------ SC6: unreadable replies
#
# Every pattern above is English. These pin the half of the problem that IS detectable —
# a change of script — and state the half that is not.


@pytest.mark.parametrize(
    "text",
    [
        "配信停止をお願いします。今後のメールは不要です。",  # ja
        "Пожалуйста, отпишите меня от рассылки немедленно",  # ru
        "يرجى إزالة عنواني من قائمتكم البريدية فورا",  # ar
        "请将我从您的邮件列表中删除，谢谢您的理解",  # zh
        "제 이메일 주소를 목록에서 삭제해 주시기 바랍니다",  # ko
        "Παρακαλώ διαγράψτε με από τη λίστα σας αμέσως",  # el
    ],
)
def test_a_non_latin_reply_is_unreadable(text):
    assert ow.is_unreadable(text) is True
    # ...and the English matcher genuinely cannot see it, which is why this test exists.
    assert ow.is_optout(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "sure, thanks for sending that over",
        "Please unsubscribe me from this list",
        "Désolé, je ne suis pas intéressé pour le moment",  # French: Latin script
        "Bitte melden Sie mich von diesem Verteiler ab",  # German: Latin script
        "Grazie, ma preferisco non ricevere altre email",  # Italian: Latin script
        "Thanks — Renée Bövik, Eastvale Holdings",  # diacritics are still Latin
    ],
)
def test_a_latin_script_reply_is_readable_whatever_the_language(text):
    """The stated residual: a Latin-script non-English opt-out is NOT caught here. This
    test pins that boundary deliberately, so a later change that widens it is a decision
    somebody made rather than a side effect."""
    assert ow.is_unreadable(text) is False


@pytest.mark.parametrize("text", ["", "   ", "ok", "👍", "👍👍👍", "はい", "+1", "\n\n"])
def test_too_few_letters_to_have_an_opinion(text):
    """An emoji-only or two-character reply must not decide the verdict on 2-3 letters."""
    assert ow.is_unreadable(text) is False


def test_a_mostly_cjk_reply_with_a_latin_signature_is_still_unreadable():
    """Real replies carry a Latin signature block, a product name or a URL."""
    body = "お世話になっております。配信停止をお願いいたします。\n--\nQuinn Bello | Brightpath Medical | brightpath.example"
    assert ow.is_unreadable(body) is True


def test_a_latin_reply_quoting_one_cjk_word_is_not_unreadable():
    body = "Thanks for the note — our team in Tokyo (東京) will take a look next week and revert."
    assert ow.is_unreadable(body) is False


def test_a_short_non_latin_optout_under_a_long_latin_signature_is_unreadable():
    """The shape a whole-body share CANNOT see, and the one that matters most: a
    four-character opt-out above a forty-word signature is 0.05 non-Latin — the same as an
    English reply quoting one foreign place name. The opening-line test separates them."""
    body = (
        "配信停止\n--\nQuinn Bello | Head of Partnerships | Brightpath Medical | "
        "1-1-1 Kawa-cho, Tokyo | brightpath.example"
    )
    assert ow.is_unreadable(body) is True


def test_an_english_opening_followed_by_a_non_latin_body_is_unreadable():
    """The mirror case: the opening line is readable, the substance is not."""
    assert ow.is_unreadable("Hi Jane,\n\n配信停止をお願いします。今後のメールは不要です。") is True


def test_the_thresholds_are_not_degenerate():
    """Instrument check: if any threshold were degenerate every test above would pass
    vacuously — a 0.0 share calls everything unreadable, a 1.0 share nothing."""
    assert 0 < ow._UNREADABLE_NON_LATIN_SHARE < 1
    assert ow._UNREADABLE_MIN_LETTERS >= 1
    assert ow._UNREADABLE_MIN_OPENING_LETTERS >= 1


def test_both_signals_are_load_bearing():
    """§R18: neither signal may be decorative. Each case below is caught by exactly one of
    the two, so deleting either one turns one of these red."""
    whole_body_only = "Hi Jane,\n\n配信停止をお願いします。今後のメールは不要です。"
    opening_only = (
        "配信停止\n--\n" + "Quinn Bello Head of Partnerships Brightpath Medical Tokyo " * 3
    )
    assert ow.is_unreadable(whole_body_only) is True
    assert ow.is_unreadable(opening_only) is True
    assert ow.is_unreadable("Thanks — our Tokyo (東京) team will revert next week.") is False


def test_record_unreadable_event_writes_the_row_a_human_acts_on():
    rows: list = []

    class _L:
        def append_history(self, r):
            rows.append(r)

    match = ow.OptOutMatch(
        thread_id="t1",
        email="quinn@brightpath.example",
        subject="Re: your note",
        message_ts="2026-09-21T10:00:00Z",
        snippet="配信停止をお願いします",
        direction_known=True,
    )
    ow.record_unreadable_event(_L(), match, escalated=True)
    assert rows[0]["event"] == "optout_unreadable"
    assert rows[0]["email"] == "quinn@brightpath.example"
    assert rows[0]["escalated"] is True
    assert "no reply is" in rows[0]["action_required"]


# --- test plan §4.4 — a reply body that is unreadable to a REGEX, not to a person ---------
#
# `un​subscribe` renders as "unsubscribe" and matches `\bunsubscribe\b` nowhere. Real
# mail carries these: HTML rendering, tracking markup, copy-paste from a web page. A miss
# here is a missed opt-out, which is the failure this module exists to prevent — so the
# characters are removed before matching rather than enumerated as patterns.


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("zero-width space", "un​sub​scribe me"),
        ("zero-width non-joiner", "un‌subscribe"),
        ("zero-width joiner", "un‍subscribe"),
        ("soft hyphen", "un­subscribe"),
        ("word joiner", "please re⁠move me"),
        ("BOM", "﻿please remove me"),
        ("control chars", "un\x00sub\x01scribe"),
    ],
)
def test_an_invisible_character_cannot_hide_an_optout(label, text):
    assert ow.is_optout(text), label


def test_stripping_invisibles_keeps_real_separators():
    """Tabs and newlines are control characters but they separate words — collapsing them
    to nothing would join two words into one and invent a match that is not there."""
    assert ow.strip_invisibles("stop\tthinking") == "stop thinking"
    assert ow.strip_invisibles("line one\nline two") == "line one line two"
    assert ow.strip_invisibles("un​subscribe") == "unsubscribe"


def test_stripping_invisibles_does_not_invent_an_optout():
    """The positive control in the other direction: removing invisibles must not fuse
    unrelated words into an opt-out phrase."""
    for text in (
        "Sounds interesting, tell me more",
        "We run nonstop across timezones",
        "Please stop by our office next week",
        "",
    ):
        assert not ow.is_optout(text), text


def test_an_invisible_character_does_not_make_a_reply_unreadable():
    """SC6's script test must not fire on a Latin reply carrying a zero-width space: those
    characters are not alphabetic, so they never enter the ratio."""
    assert ow.is_unreadable("un​subscribe me please") is False


# ── The reply our own sequences ASK for, in the shapes a mail client actually sends ──
#
# Live read 2026-09-22: setting code 2 on all seven live sequences is the opt-out
# instruction given to every recipient, and both variants tell them to reply with one
# word — "If this isn't relevant, just reply 'stop' and I won't follow up." and
# "Reply 'Stop' if you'd prefer not to receive messages at this time."
#
# A bare "stop" is only treated as an opt-out when it is essentially the whole message,
# because "stop" is also an ordinary English word. But almost nobody sends a body that is
# literally four characters: a phone appends a signature, a desktop client quotes the
# original underneath. Measured against the un-fixed matcher, six of the eight shapes
# below read as NOT an opt-out — including every mobile reply, which is the likeliest
# way this instruction is ever followed.

_SIGNED_AND_QUOTED_STOPS = (
    ("iPhone signature", "Stop\n\n--\nSent from my iPhone"),
    ("Android signature, no sigdashes", "Stop\nSent from my Android"),
    ("Outlook mobile signature", "Stop\n\nGet Outlook for iOS"),
    (
        "sigdashes then a name block",
        "Stop\n\n-- \nJordan Vale\nHead of Platform\nAcme Robotics",
    ),
    (
        "Gmail quote underneath",
        "Stop\n\nOn Mon, 21 Sep 2026 at 09:14, Sam <s@acme.example> wrote:\n"
        "> Hi there, quick note about your agent rollout",
    ),
    (
        "Outlook divider underneath",
        "Stop\n\n________________________________\nFrom: Sam <s@acme.example>\n"
        "Sent: Monday\nSubject: quick note",
    ),
    ("marker-only quote lines", "stop\n\n> the original email, quoted back in full"),
    ("trailing courtesy then signature", "Stop, thanks\n\n--\nSent from my iPhone"),
)


@pytest.mark.parametrize(
    "label,body", _SIGNED_AND_QUOTED_STOPS, ids=[c[0] for c in _SIGNED_AND_QUOTED_STOPS]
)
def test_a_one_word_stop_survives_a_signature_or_a_quoted_original(label: str, body: str):
    """The instruction is ours; missing the reply it asks for is our defect, not theirs."""
    assert ow.is_optout(body) is True, label


_NOT_OPTOUTS_WITH_SIGNATURES = (
    (
        "prose 'stop' above a signature",
        "We had to stop the rollout last quarter — restarting in Q1.\n\n--\nSent from my iPhone",
    ),
    (
        "'stop by' above a signature",
        "Happy to chat, stop by the booth next week.\n\n--\nSent from my iPhone",
    ),
    (
        "stop only inside the QUOTED original",
        "Sure, send times.\n\nOn Mon, Sam wrote:\n> reply 'stop' and I won't follow up",
    ),
    (
        "interested reply with a signature",
        "Interested — what does onboarding look like?\n\n--\nSent from my iPhone",
    ),
)


@pytest.mark.parametrize(
    "label,body", _NOT_OPTOUTS_WITH_SIGNATURES, ids=[c[0] for c in _NOT_OPTOUTS_WITH_SIGNATURES]
)
def test_trimming_a_signature_does_not_invent_an_optout(label: str, body: str):
    """§R18's other half. Widening the bare-stop branch is only safe if it still cannot
    fire on ordinary prose — and the third case is the one that matters most: our own
    instruction text, quoted back inside the original, must not opt the sender out."""
    assert ow.is_optout(body) is False, label


# --- clear opt-outs: the narrow test that gates the sweep's automatic DNC add ---------

_GMAIL_STOP = (
    '<div dir="ltr">Stop</div><br><div class="gmail_quote gmail_quote_container">'
    '<div dir="ltr" class="gmail_attr">On Thu, Sep 24, 2026 at 10:15 PM Sam Lee wrote:<br>'
    '</div><blockquote class="gmail_quote">Not relevant? Reply stop to opt out.</blockquote></div>'
)


@pytest.mark.parametrize(
    "body",
    [
        _GMAIL_STOP,  # the live shape: ONE line of HTML, quoted original underneath
        "Unsubscribe",
        "STOP",
        "Stop.\n\nSent from my iPhone",
        "Please remove me from your list",
        "Unsubscribe &amp; remove me",
    ],
)
def test_a_short_typed_optout_is_clear(body):
    assert ow.is_clear_optout(body)


@pytest.mark.parametrize(
    "body",
    [
        # Matched ONLY by our own quoted original — the reply itself is a yes.
        '<div dir="ltr">Yes, Tuesday works</div><div class="gmail_quote"><blockquote>'
        "reply stop to opt out</blockquote></div>",
        "Don't unsubscribe me",
        "Can I opt out of the webinar?",
        "Stop by Tuesday",
        "Not interested, remove me",
        "Thanks. We are evaluating vendors next quarter so please take me off for now",
        "",
    ],
)
def test_anything_else_is_left_to_a_human(body):
    assert not ow.is_clear_optout(body)


def test_the_quoted_original_is_not_what_was_typed():
    """Before 2026-09-24 the one-line Gmail HTML was never cut, so a reply matched on the
    words of OUR email. The recall matcher still does that (a human reviews it); the
    clear test must not."""
    yes = '<div>Yes please</div><div class="gmail_quote"><blockquote>opt out</blockquote></div>'
    assert ow.typed_text(yes) == "Yes please"
    assert ow.typed_text(_GMAIL_STOP) == "Stop"


def test_find_optouts_marks_a_clear_match():
    thread = {
        "id": "t1",
        "subject": "re: hi",
        "messages": [{"fromEmail": "sam@lee.example", "content": _GMAIL_STOP, "fromProspectId": 7}],
    }
    match = ow.find_optouts_in_thread(thread)
    assert match is not None and match.clear is True

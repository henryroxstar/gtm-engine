"""Tests for the wave gate — the consumer `outcomes.jsonl` never had.

The PRD asked for a `positive_reply_rate` reading from the previous wave before the
next is staged. Nothing implemented it, and nothing wrote to the outcomes ledger
either, so the file existed and was empty forever. An unfed sink looks exactly like a
fed one until something asks it a question.

All fixtures are invented (docs/RULES.md R9).
"""

from __future__ import annotations

import json

import pytest

from gtm_core import wave_gate as wg


def _payload(**kw):
    base = {"sends": 30, "replies": 4, "positive_replies": 2, "opt_outs": 1}
    base.update(kw)
    return base


def test_no_outcomes_on_file_blocks(tmp_path):
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert not ok
    assert "No wave outcomes on file" in message
    assert "ingest" in message, "a blocking gate must say how to unblock it"


def test_a_wave_below_the_send_floor_blocks(tmp_path):
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=5, positive_replies=1),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert not ok
    assert "5 send(s)" in message


def test_a_measured_wave_passes_and_prints_the_rate(tmp_path):
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=40, positive_replies=2, opt_outs=1),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert ok
    assert "5.0%" in message, "the whole point is that the number is read out loud"


def test_the_newest_wave_is_the_one_checked(tmp_path):
    for n, sends in (("w1", 40), ("w2", 3)):
        wg.append_report(
            wg.WaveReport(wave=n, date=f"2026-08-2{sends % 10}", sends=sends),
            "acme",
            content_root=tmp_path,
        )
    ok, _ = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert not ok, "an older healthy wave must not vouch for an unmeasured newer one"


def test_ingest_normalizes_and_appends(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    src = tmp_path / "payload.json"
    src.write_text(json.dumps(_payload()), encoding="utf-8")
    # --ack-unnamed-optouts keeps this test on ONE axis. `_payload()` carries an opt-out,
    # and since 2026-09-21 ingest refuses a count with no names — a real gate, tested on its
    # own below. Without the ack this test would fail for that reason instead of its own.
    rc = wg.main(
        [
            "ingest",
            "--profile",
            "acme",
            "--json",
            str(src),
            "--wave",
            "w1",
            "--date",
            "2026-08-20",
            "--ack-unnamed-optouts",
        ]
    )
    assert rc == 0
    reports = wg.read_reports("acme", content_root=tmp_path)
    assert len(reports) == 1 and reports[0].sends == 30


def test_ingest_is_idempotent_on_wave_and_date(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    src = tmp_path / "payload.json"
    src.write_text(json.dumps(_payload()), encoding="utf-8")
    args = [
        "ingest",
        "--profile",
        "acme",
        "--json",
        str(src),
        "--wave",
        "w1",
        "--date",
        "2026-08-20",
        "--ack-unnamed-optouts",  # see test_ingest_normalizes_and_appends
    ]
    wg.main(args)
    wg.main(args)
    assert len(wg.read_reports("acme", content_root=tmp_path)) == 1


def test_ingest_refuses_a_reading_it_cannot_identify(tmp_path, monkeypatch):
    """Without a wave and a date, two waves collapse into one record."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    src = tmp_path / "payload.json"
    src.write_text(json.dumps(_payload()), encoding="utf-8")
    assert wg.main(["ingest", "--profile", "acme", "--json", str(src)]) == 1


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"sent": 30, "interested": 3}, (30, 3)),
        ({"stats": {"emails_sent": 12, "positive_sentiment": 1}}, (12, 1)),
        ({"summary": {"sends": 8, "positive": 0}}, (8, 0)),
    ],
)
def test_normalize_accepts_the_shapes_a_sequencer_reports(payload, expected):
    r = wg.normalize_payload(payload, wave="w", date="2026-08-20")
    assert (r.sends, r.positive_replies) == expected


def test_an_unparseable_count_is_zero_not_an_exception():
    r = wg.normalize_payload({"sends": "many"}, wave="w", date="2026-08-20")
    assert r.sends == 0 and not r.readable


def test_a_foreign_record_kind_is_skipped_not_guessed_at(tmp_path):
    path = wg.outcomes_jsonl("acme", content_root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kind": "something_else", "sends": 999})
        + "\n"
        + json.dumps({"kind": wg.RECORD_KIND, "wave": "w1", "date": "2026-08-20", "sends": 40})
        + "\n",
        encoding="utf-8",
    )
    reports = wg.read_reports("acme", content_root=tmp_path)
    assert len(reports) == 1 and reports[0].sends == 40


def test_a_corrupt_line_does_not_break_the_history(tmp_path):
    path = wg.outcomes_jsonl("acme", content_root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "{not json\n"
        + json.dumps({"kind": wg.RECORD_KIND, "wave": "w1", "date": "2026-08-20", "sends": 40})
        + "\n",
        encoding="utf-8",
    )
    assert len(wg.read_reports("acme", content_root=tmp_path)) == 1


def test_the_gate_does_not_judge_the_rate(tmp_path):
    """A zero-positive wave still PASSES: the requirement is that it was measured.

    Demanding a rate would block the pipeline on a baseline nobody has established.
    """
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=40, positive_replies=0, opt_outs=1),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert ok
    assert "0.0%" in message


def test_high_optout_rate_large_sample_blocks_without_ack(tmp_path):
    """Wave with >= 30 sends and > 5.0% opt-outs must be blocked unless acked."""
    # 40 sends, 3 opt-outs = 7.5% > 5.0%
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=40, positive_replies=2, opt_outs=3),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert not ok
    assert "opt-out rate is too high" in message
    assert "3/40" in message
    assert "7.5%" in message
    assert "--ack-high-optout" in message

    # Passes when acknowledged
    ok_ack, message_ack = wg.check(
        "acme", content_root=tmp_path, ack_high_optout=True, ack_unreconciled_optouts=True
    )
    assert ok_ack
    assert "7.5%" in message_ack


def test_high_optout_rate_small_sample_blocks_without_ack(tmp_path):
    """Wave with < 30 sends and > 3 raw opt-outs must be blocked unless acked."""
    # 25 sends, 4 opt-outs = 16.0% (and > 3 raw opt-outs)
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=25, positive_replies=1, opt_outs=4),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert not ok
    assert "opt-out rate is too high" in message
    assert "4/25" in message
    assert ">3 raw opt-outs ceiling" in message

    # Passes when acknowledged
    ok_ack, _ = wg.check(
        "acme", content_root=tmp_path, ack_high_optout=True, ack_unreconciled_optouts=True
    )
    assert ok_ack


def test_small_sample_acceptable_optouts_passes(tmp_path):
    """Wave with < 30 sends and <= 3 raw opt-outs passes even if percentage is high."""
    # 24 sends, 1 opt-out = 4.2% <= 3 raw opt-outs
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=24, positive_replies=1, opt_outs=1),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert ok
    assert "1 opt-out (4.2%)" in message


def test_boundary_exact_30_sends_5_percent_passes(tmp_path):
    """Exactly at 30 sends and exactly 5.0% opt-out rate (not strictly greater) passes."""
    # Note: 5% of 40 is 2; 5% of 60 is 3. At 40 sends, 2 opt-outs = 5.0% exactly.
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=40, positive_replies=2, opt_outs=2),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert ok
    assert "2 opt-out (5.0%)" in message


def test_a_small_wave_over_the_rate_still_blocks(tmp_path):
    """29 sends and 3 opt-outs is 10.3% — BLOCKS, reversing this test's original assertion.

    Until 2026-09-21 this case was pinned as a PASS, on the reasoning that a rate over fewer
    than 30 sends is too noisy to read and a raw count of 3 is tolerable. The first half of
    that is right; making the raw count a REPLACEMENT for the rate was not. It made the guard
    non-monotonic — the live 3/24 (12.5%) wave passed while a 2/30 (6.7%) wave was refused —
    so a smaller wave could walk under a domain-reputation guard by being smaller.

    The noise concern is now carried by the strict `>` instead: one opt-out in 20 sends is
    exactly 5.0% and still passes; two in twenty does not.
    """
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=29, positive_replies=1, opt_outs=3),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert not ok
    assert "3/29" in message
    assert ">5.0% threshold" in message
    assert wg.check(
        "acme", content_root=tmp_path, ack_high_optout=True, ack_unreconciled_optouts=True
    )[0]


def test_boundary_exact_30_sends_exceeding_5_percent_blocks(tmp_path):
    """At 30 sends, 2 opt-outs is 6.7% > 5.0% — must block."""
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=30, positive_replies=1, opt_outs=2),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert not ok
    assert "opt-out rate is too high" in message
    assert "2/30" in message
    assert "6.7%" in message


def test_cli_check_ack_high_optout_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=40, positive_replies=1, opt_outs=4),
        "acme",
        content_root=tmp_path,
    )
    # Default check exits 1
    assert wg.main(["check", "--profile", "acme"]) == 1
    # --ack-high-optout alone still exits 1: this wave's opt-outs are also unreconciled
    # (no suppression-ledger / optout_detected rows), which is a SEPARATE refusal.
    assert wg.main(["check", "--profile", "acme", "--ack-high-optout"]) == 1
    # Both acks exits 0
    assert (
        wg.main(["check", "--profile", "acme", "--ack-high-optout", "--ack-unreconciled-optouts"])
        == 0
    )


def test_the_optout_guard_is_monotonic_in_wave_size(tmp_path):
    """Holding opt-outs fixed, a SMALLER wave can never be more acceptable than a larger one.

    This is the property the 2026-09-21 fix restored, asserted directly rather than through
    the two boundary cases that happen to sit either side of it. The failure it pins is not
    "a threshold was wrong" but "the guard could be walked under by shrinking the wave" —
    which is exactly what a sender does when a list starts going badly.
    """
    verdicts = []
    for i, sends in enumerate(range(20, 61)):
        profile = f"p{i}"
        wg.append_report(
            wg.WaveReport(wave="w", date="2026-08-20", sends=sends, positive_replies=0, opt_outs=3),
            profile,
            content_root=tmp_path,
        )
        verdicts.append(
            (sends, wg.check(profile, content_root=tmp_path, ack_unreconciled_optouts=True)[0])
        )

    # 3 opt-outs: refused while the rate is over 5% (up to 59 sends), allowed from 60 on.
    refused = [s for s, ok in verdicts if not ok]
    allowed = [s for s, ok in verdicts if ok]
    assert refused and allowed, "the sweep must cross the boundary to prove anything"
    assert max(refused) < min(allowed), (
        f"non-monotonic: wave sizes {sorted(s for s in refused if s > min(allowed))} are "
        f"refused while smaller ones are allowed. Holding opt-outs fixed, shrinking a wave "
        f"must never make it more acceptable."
    )


def test_one_optout_in_twenty_is_the_noise_floor_and_passes(tmp_path):
    """The concern the old small-sample branch existed for, handled by the strict `>`.

    20 sends is MIN_SENDS, so this is the smallest wave that reaches the opt-out check at
    all. One opt-out is exactly 5.0% — not over it — so a single unsubscribe never blocks the
    next wave on its own. Two do.
    """
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=20, positive_replies=0, opt_outs=1),
        "floor",
        content_root=tmp_path,
    )
    assert wg.check("floor", content_root=tmp_path, ack_unreconciled_optouts=True)[0]

    wg.append_report(
        wg.WaveReport(wave="w2", date="2026-08-21", sends=20, positive_replies=0, opt_outs=2),
        "floor2",
        content_root=tmp_path,
    )
    ok, message = wg.check("floor2", content_root=tmp_path, ack_unreconciled_optouts=True)
    assert not ok
    assert "2/20" in message


# Every `check()` above isolates the RATE axis and therefore passes
# `ack_unreconciled_optouts=True`. The reconciliation axis — counted opt-outs vs. the ones
# this system can actually NAME and exclude — is a separate refusal with its own tests
# below. Keeping them apart matters: without the ack, every rate case with a single opt-out
# would block for the OTHER reason, and a test that can fail two ways tells you neither.


def wave_gate_suppression_path(content_root, profile):
    """The ledger path the gate reads, resolved through the same helper it uses."""
    from gtm_core.prospect_paths import suppression_ledger

    return suppression_ledger(profile, content_root)


# --- counted vs. nameable opt-outs -------------------------------------------------------


def _wave(tmp_path, profile, *, sends=100, opt_outs=0):
    wg.append_report(
        wg.WaveReport(
            wave="w", date="2026-08-20", sends=sends, positive_replies=1, opt_outs=opt_outs
        ),
        profile,
        content_root=tmp_path,
    )


def test_counted_optouts_with_no_identities_block(tmp_path):
    """The live 2026-09-21 condition: 3 counted, 0 nameable.

    A wave report carries a COUNT; the lane router excludes by ADDRESS. When the two
    disagree, somebody who asked to be left alone is reachable by the next wave — and no
    rate check can see it, because the rate is computed from the very count that lacks names.
    """
    _wave(tmp_path, "acme", sends=100, opt_outs=3)  # 3% — under the rate ceiling on purpose
    ok, message = wg.check("acme", content_root=tmp_path)
    assert not ok, "unnamed opt-outs must block even when the RATE is fine"
    assert "only 0 can be NAMED" in message
    assert "--ack-unreconciled-optouts" in message
    assert wg.check("acme", content_root=tmp_path, ack_unreconciled_optouts=True)[0]


def test_a_suppression_row_makes_an_optout_nameable(tmp_path):
    """Control for the test above: identical wave, one recipient-initiated suppression row."""
    _wave(tmp_path, "acme", sends=100, opt_outs=1)
    assert not wg.check("acme", content_root=tmp_path)[0]

    ledger = wave_gate_suppression_path(tmp_path, "acme")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "email,name,company_domain,reason,date,note\n"
        "someone@acme.example,,,dnc-optout,2026-08-21,replied unsubscribe\n",
        encoding="utf-8",
    )
    ok, message = wg.check("acme", content_root=tmp_path)
    assert ok, message
    assert "1/1 opt-out(s) identifiable" in message


def test_only_recipient_initiated_reasons_count_as_nameable(tmp_path):
    """List hygiene is not consent.

    ``out-of-market`` / ``competitor`` / ``eval-disqualified`` are OUR judgments about a
    person; ``dnc-optout`` is theirs about us. Counting the former as an identified opt-out
    would let a ledger full of our own exclusions satisfy a check about their wishes — which
    is exactly backwards, and would have made this gate pass on the live tree, where 82
    suppression rows contain not one recipient-initiated reason.
    """
    _wave(tmp_path, "acme", sends=100, opt_outs=1)
    ledger = wave_gate_suppression_path(tmp_path, "acme")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "email,name,company_domain,reason,date,note\n"
        "a@acme.example,,,out-of-market,2026-08-21,\n"
        "b@acme.example,,,competitor,2026-08-21,\n"
        "c@acme.example,,,eval-disqualified,2026-08-21,\n",
        encoding="utf-8",
    )
    assert wg.identifiable_optouts("acme", tmp_path) == set()
    assert not wg.check("acme", content_root=tmp_path)[0]


def test_a_detected_reply_optout_makes_it_nameable(tmp_path):
    """The reply path — the one the sequencer never auto-suppresses.

    `gtm_core.optout_watch` writes an `optout_detected` history row; `lanes.context` reads
    exactly that event name into `ctx.optouts`. This asserts the wave gate reads the same
    one, so the three of them cannot drift into disagreeing about who opted out.
    """
    _wave(tmp_path, "acme", sends=100, opt_outs=1)
    history = tmp_path / "acme" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        json.dumps({"event": "optout_detected", "email": "Someone@Acme.Example"}) + "\n",
        encoding="utf-8",
    )
    assert wg.identifiable_optouts("acme", tmp_path) == {"someone@acme.example"}
    assert wg.check("acme", content_root=tmp_path)[0]


def test_reconciliation_counts_across_every_recorded_wave(tmp_path):
    """Opt-outs are permanent; a wave is not. The denominator is every wave on file."""
    _wave(tmp_path, "acme", sends=100, opt_outs=1)
    wg.append_report(
        wg.WaveReport(wave="w2", date="2026-08-27", sends=100, positive_replies=1, opt_outs=2),
        "acme",
        content_root=tmp_path,
    )
    assert wg.optout_reconciliation("acme", tmp_path) == (3, 0)


# --- naming opt-outs at INGEST -----------------------------------------------------------
#
# The refusals above fire at `check` time — after the reading is on file, and overridable.
# That is six weeks too late: on the live 2026-07-29 wave, `opt_outs: 3` was recorded with
# the three people written nowhere the send path reads, and the provenance of the count lived
# in the free-text `source` string, which nothing can parse. Ingest is when the provider
# payload is in hand, so ingest is where the names are demanded.
#
# Each refusal below is paired with a negative control (docs/RULES.md §R18).


def _ingest(tmp_path, monkeypatch, payload, *flags, profile="acme"):
    """Run the ingest CLI against a scratch content root, payload on stdin."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    return wg.main(["ingest", "--profile", profile, "--json", "-", *flags]), payload


def _stdin(monkeypatch, payload):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _suppress(tmp_path, profile, *emails):
    ledger = wave_gate_suppression_path(tmp_path, profile)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(f"{e},,,dnc-optout,2026-08-21,asked to be removed\n" for e in emails)
    ledger.write_text("email,name,company_domain,reason,date,note\n" + rows, encoding="utf-8")


def test_ingest_refuses_an_optout_count_with_no_names(tmp_path, monkeypatch, capsys):
    """The 2026-07-29 shape exactly: a number, and nobody attached to it."""
    _stdin(monkeypatch, {"wave": "w1", "date": "2026-09-01", "sends": 30, "opt_outs": 2})
    rc, _ = _ingest(tmp_path, monkeypatch, None)
    assert rc == 1
    err = capsys.readouterr().err
    assert "names none" in err
    assert "--ack-unnamed-optouts" in err, "a blocking gate must say how to unblock it"


def test_ingest_accepts_named_and_suppressed_optouts(tmp_path, monkeypatch):
    """Negative control: the same wave, named and actually excludable, records cleanly."""
    _suppress(tmp_path, "acme", "dana@northwind.example", "kit@harbourline.example")
    _stdin(
        monkeypatch,
        {
            "wave": "w2",
            "date": "2026-09-02",
            "sends": 30,
            "opt_outs": 2,
            "opt_out_emails": ["dana@northwind.example", "kit@harbourline.example"],
        },
    )
    rc, _ = _ingest(tmp_path, monkeypatch, None)
    assert rc == 0


def test_ingest_refuses_a_name_that_suppresses_nobody(tmp_path, monkeypatch, capsys):
    """Naming is not excluding.

    `outcomes.jsonl` is an audit ledger; nothing in the build path reads it. A name that
    never reaches the suppression ledger leaves the person reachable by the next rebuild,
    while making the record LOOK accounted for — worse than leaving them unnamed.
    """
    _suppress(tmp_path, "acme", "dana@northwind.example")
    _stdin(
        monkeypatch,
        {
            "wave": "w3",
            "date": "2026-09-03",
            "sends": 30,
            "opt_outs": 2,
            "opt_out_emails": ["dana@northwind.example", "kit@harbourline.example"],
        },
    )
    rc, _ = _ingest(tmp_path, monkeypatch, None)
    assert rc == 1
    err = capsys.readouterr().err
    assert "kit@harbourline.example" in err
    assert "dana@northwind.example" not in err, "only the unsuppressed one should be named"


def test_ingest_refuses_a_partial_name_list(tmp_path, monkeypatch, capsys):
    """A short list reconciles as if it were complete — how the unnamed stay unnamed."""
    _suppress(tmp_path, "acme", "dana@northwind.example", "kit@harbourline.example")
    _stdin(
        monkeypatch,
        {
            "wave": "w4",
            "date": "2026-09-04",
            "sends": 30,
            "opt_outs": 3,
            "opt_out_emails": ["dana@northwind.example", "kit@harbourline.example"],
        },
    )
    rc, _ = _ingest(tmp_path, monkeypatch, None)
    assert rc == 1
    assert "names only 2" in capsys.readouterr().err


def test_ingest_does_not_refuse_a_wave_with_no_optouts(tmp_path, monkeypatch):
    """Negative control for all three refusals: they must not fire on a clean wave."""
    _stdin(monkeypatch, {"wave": "w5", "date": "2026-09-05", "sends": 30, "opt_outs": 0})
    rc, _ = _ingest(tmp_path, monkeypatch, None)
    assert rc == 0


def test_ingest_ack_records_the_reading_anyway(tmp_path, monkeypatch):
    """The override exists and is explicit, matching --ack-high-optout."""
    _stdin(monkeypatch, {"wave": "w6", "date": "2026-09-06", "sends": 30, "opt_outs": 2})
    rc, _ = _ingest(tmp_path, monkeypatch, None, "--ack-unnamed-optouts")
    assert rc == 0
    assert wg.read_reports("acme", content_root=tmp_path)[-1].opt_outs == 2


def test_opt_out_emails_survive_the_jsonl_round_trip(tmp_path):
    """JSON has no tuple; a list read back must not change the record's identity."""
    wg.append_report(
        wg.WaveReport(
            wave="w",
            date="2026-09-07",
            sends=30,
            opt_outs=1,
            opt_out_emails=("Dana@Northwind.Example",),
        ),
        "acme",
        content_root=tmp_path,
    )
    back = wg.read_reports("acme", content_root=tmp_path)[-1]
    assert back.opt_out_emails == ("dana@northwind.example",)


def test_a_record_written_before_the_field_existed_still_loads(tmp_path):
    """Every reading on file predates `opt_out_emails`; none of them may become unreadable."""
    path = wg.outcomes_jsonl("acme", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kind": wg.RECORD_KIND, "wave": "old", "date": "2026-07-29", "sends": 24})
        + "\n",
        encoding="utf-8",
    )
    (report,) = wg.read_reports("acme", content_root=tmp_path)
    assert report.wave == "old"
    assert report.opt_out_emails == ()


@pytest.mark.parametrize(
    "raw",
    [
        ["Dana@Northwind.Example", "dana@northwind.example"],
        "Dana@Northwind.Example, dana@northwind.example",
        ("dana@northwind.example", "", None, "not-an-address"),
    ],
)
def test_emails_are_folded_deduped_and_filtered(raw):
    assert wg._emails(raw) == ("dana@northwind.example",)

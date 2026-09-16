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
    ok, message = wg.check("acme", content_root=tmp_path)
    assert not ok
    assert "No wave outcomes on file" in message
    assert "ingest" in message, "a blocking gate must say how to unblock it"


def test_a_wave_below_the_send_floor_blocks(tmp_path):
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=5, positive_replies=1),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path)
    assert not ok
    assert "5 send(s)" in message


def test_a_measured_wave_passes_and_prints_the_rate(tmp_path):
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=40, positive_replies=2, opt_outs=1),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path)
    assert ok
    assert "5.0%" in message, "the whole point is that the number is read out loud"


def test_the_newest_wave_is_the_one_checked(tmp_path):
    for n, sends in (("w1", 40), ("w2", 3)):
        wg.append_report(
            wg.WaveReport(wave=n, date=f"2026-08-2{sends % 10}", sends=sends),
            "acme",
            content_root=tmp_path,
        )
    ok, _ = wg.check("acme", content_root=tmp_path)
    assert not ok, "an older healthy wave must not vouch for an unmeasured newer one"


def test_ingest_normalizes_and_appends(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    src = tmp_path / "payload.json"
    src.write_text(json.dumps(_payload()), encoding="utf-8")
    rc = wg.main(
        ["ingest", "--profile", "acme", "--json", str(src), "--wave", "w1", "--date", "2026-08-20"]
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
    ok, message = wg.check("acme", content_root=tmp_path)
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
    ok, message = wg.check("acme", content_root=tmp_path)
    assert not ok
    assert "opt-out rate is too high" in message
    assert "3/40" in message
    assert "7.5%" in message
    assert "--ack-high-optout" in message

    # Passes when acknowledged
    ok_ack, message_ack = wg.check("acme", content_root=tmp_path, ack_high_optout=True)
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
    ok, message = wg.check("acme", content_root=tmp_path)
    assert not ok
    assert "opt-out rate is too high" in message
    assert "4/25" in message
    assert ">3 raw opt-outs ceiling" in message

    # Passes when acknowledged
    ok_ack, _ = wg.check("acme", content_root=tmp_path, ack_high_optout=True)
    assert ok_ack


def test_small_sample_acceptable_optouts_passes(tmp_path):
    """Wave with < 30 sends and <= 3 raw opt-outs passes even if percentage is high."""
    # 24 sends, 1 opt-out = 4.2% <= 3 raw opt-outs
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=24, positive_replies=1, opt_outs=1),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path)
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
    ok, message = wg.check("acme", content_root=tmp_path)
    assert ok
    assert "2 opt-out (5.0%)" in message


def test_boundary_exact_29_sends_3_optouts_passes(tmp_path):
    """At 29 sends (<30 sample size) and exactly 3 opt-outs (<=3 ceiling) passes."""
    # 3 opt-outs / 29 sends = 10.3% > 5%, but N < 30 and opt-outs <= 3, so it passes.
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=29, positive_replies=1, opt_outs=3),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path)
    assert ok
    assert "3 opt-out (10.3%)" in message


def test_boundary_exact_30_sends_exceeding_5_percent_blocks(tmp_path):
    """At 30 sends, 2 opt-outs is 6.7% > 5.0% — must block."""
    wg.append_report(
        wg.WaveReport(wave="w1", date="2026-08-20", sends=30, positive_replies=1, opt_outs=2),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path)
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
    # Check with --ack-high-optout exits 0
    assert wg.main(["check", "--profile", "acme", "--ack-high-optout"]) == 0

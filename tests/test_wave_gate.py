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
        wg.WaveReport(wave="w1", date="2026-08-20", sends=40, positive_replies=0),
        "acme",
        content_root=tmp_path,
    )
    ok, message = wg.check("acme", content_root=tmp_path)
    assert ok
    assert "0.0%" in message

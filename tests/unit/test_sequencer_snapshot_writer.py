"""``gtm_core.sequencer_snapshot`` — the one writer of ``sequence-stats.json``.

Real files in a temp root, a frozen clock, no mocking of the writer. Every id is fictional.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from gtm_core import prospects_consolidate as pc
from gtm_core import sequencer_snapshot as ss
from gtm_core import sequencer_snapshot_load as sl
from gtm_core.prospects_dashboard import _normalize_seq
from gtm_core.sequence_snapshot_format import body_digest, figures_of, file_meta

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
MON = "2026-09-28T08:00:00Z"
TUE = "2026-09-29T08:00:00Z"
PROFILE = "acme"


@pytest.fixture(autouse=True)
def _the_profile_exists(tmp_path):
    """The writer refuses a profile with no content folder (a typo must not create a tenant), so
    every test starts from a profile that exists."""
    (tmp_path / PROFILE).mkdir()


def _row(sid, sent=10, delivered=None):
    delivered = sent if delivered is None else delivered
    return {
        "sequenceId": sid,
        "sequenceName": f"Name {sid}",
        "status": "paused",
        "prospects": [{"total": sent, "contacted": sent}],
        "emails": {"status": {"delivered": delivered}},
    }


def _payload(tmp_path, name, *rows, mtime=NOW):
    p = tmp_path / "payloads" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({"sequences": list(rows)}), encoding="utf-8")
    os.utime(p, (mtime.timestamp(), mtime.timestamp()))
    return p


def _write(tmp_path, files, **kw):
    kw.setdefault("now", NOW)
    return ss.write(PROFILE, list(files), content_root=tmp_path, **kw)


def _path(tmp_path):
    return ss.stats_path(PROFILE, tmp_path)


def _doc(tmp_path):
    return json.loads(_path(tmp_path).read_text(encoding="utf-8"))


def _plant(tmp_path, content):
    p = _path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return p.read_bytes()


def _legacy(n=16, fetched=MON, sent=40):
    return {"fetched": fetched, "sequences": [_row(f"L{i:02d}", sent) for i in range(n)]}


def test_a_first_write_records_each_sequence_with_its_date_and_hashes(tmp_path):
    f = _payload(tmp_path, "a.json", _row("S1", 7), _row("S2", 9))
    ok, lines = _write(tmp_path, [f], fetched=MON)
    assert ok and "Wrote 2 sequence(s)" in lines[0] and MON in lines[0]
    doc = _doc(tmp_path)
    assert doc["format"] == 2 and doc["fetched"] == MON
    assert doc["stamps"] == {"S1": MON, "S2": MON}
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    assert doc["payload_sha256"] == {"S1": sha, "S2": sha}
    assert doc["body_sha256"] == body_digest(doc)
    assert file_meta(doc)["edited"] is False


def test_the_stamp_defaults_to_the_frozen_now(tmp_path):
    ok, _ = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))])
    assert ok and _doc(tmp_path)["stamps"] == {"S1": "2026-09-29T12:00:00Z"}


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("not json", id="not-json"),
        pytest.param({"sequences": []}, id="no-sequences"),
        pytest.param({"sequences": [{"sequenceId": "S9"}]}, id="no-figures"),
        pytest.param({"sequences": [_row("S9"), _row("S9")]}, id="duplicate-in-payload"),
    ],
)
def test_a_refused_payload_leaves_the_file_byte_identical(tmp_path, bad):
    ok, _ = _write(tmp_path, [_payload(tmp_path, "good.json", _row("S1"))], fetched=MON)
    assert ok
    before = _path(tmp_path).read_bytes()
    p = tmp_path / "payloads" / "bad.json"
    p.write_text(bad if isinstance(bad, str) else json.dumps(bad), encoding="utf-8")
    os.utime(p, (NOW.timestamp(), NOW.timestamp()))
    ok, lines = _write(tmp_path, [_payload(tmp_path, "ok2.json", _row("S2")), p], fetched=TUE)
    assert not ok and lines[0].startswith("REFUSED")
    assert _path(tmp_path).read_bytes() == before


def test_one_bad_file_among_good_ones_writes_nothing_from_the_good_ones(tmp_path):
    ok, _ = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=MON)
    before = _path(tmp_path).read_bytes()
    good = _payload(tmp_path, "b.json", _row("S2"))
    dup = _payload(tmp_path, "c.json", _row("S2"))
    ok, lines = _write(tmp_path, [good, dup], fetched=TUE)
    assert not ok and "appears twice" in lines[0]
    assert _path(tmp_path).read_bytes() == before


def test_a_payload_file_older_than_an_hour_is_refused(tmp_path):
    old = NOW - timedelta(seconds=sl.PAYLOAD_MAX_AGE_S + 60)
    ok, lines = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), mtime=old)])
    assert not ok and "fetch the figures again" in lines[0]
    assert not _path(tmp_path).exists()


def test_a_payload_just_inside_the_hour_is_accepted(tmp_path):
    fresh = NOW - timedelta(seconds=sl.PAYLOAD_MAX_AGE_S - 60)
    ok, _ = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), mtime=fresh)])
    assert ok


@pytest.mark.parametrize("stamp", ["not-a-date", "2026-10-30", ""])
def test_a_future_or_unusable_fetched_date_is_refused(tmp_path, stamp):
    ok, lines = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=stamp or "x")
    assert not ok and "not a usable date" in lines[0]
    assert not _path(tmp_path).exists()


def test_a_partial_refresh_stamps_only_what_it_wrote_and_the_top_date_stays_the_newest(tmp_path):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), _row("S2"))], fetched=MON)
    _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2", 12))], fetched=TUE)
    doc = _doc(tmp_path)
    assert doc["stamps"] == {"S1": MON, "S2": TUE}
    assert doc["fetched"] == TUE
    assert [r["sequenceId"] for r in doc["sequences"]] == ["S1", "S2"]
    assert doc["body_sha256"] == body_digest(doc)


def test_backfilling_an_older_date_does_not_move_the_top_date_back(tmp_path):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=TUE)
    _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2"))], fetched=MON)
    assert _doc(tmp_path)["fetched"] == TUE


def test_a_legacy_file_keeps_its_rows_and_marks_them_inherited(tmp_path):
    _plant(tmp_path, _legacy(16))
    meta = file_meta(_doc(tmp_path))
    assert len(meta["inherited"]) == 16 and set(meta["stamps"].values()) == {MON}
    ok, _ = _write(
        tmp_path, [_payload(tmp_path, "a.json", _row("L03", 41, delivered=41))], fetched=TUE
    )
    assert ok
    doc = _doc(tmp_path)
    assert len(doc["sequences"]) == 16 and doc["format"] == 2
    assert doc["stamps"]["L03"] == TUE and doc["stamps"]["L04"] == MON
    assert "L03" not in doc["inherited"] and len(doc["inherited"]) == 15
    assert doc["fetched"] == TUE


def test_a_partial_then_a_full_write_over_a_legacy_file_leaves_nothing_inherited(tmp_path):
    _plant(tmp_path, _legacy(16))
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("L00", 41))], fetched=TUE)
    full = _payload(tmp_path, "full.json", *[_row(f"L{i:02d}", 45) for i in range(16)])
    ok, _ = _write(tmp_path, [full], fetched="2026-09-29T11:00:00Z")
    assert ok
    doc = _doc(tmp_path)
    assert doc["inherited"] == [] and len(set(doc["stamps"].values())) == 1
    assert len(doc["payload_sha256"]) == 16


def test_a_bare_list_previous_file_is_merged_and_its_rows_stay_unstamped(tmp_path):
    _plant(tmp_path, [_row("S1", 3), _row("S2", 4)])
    ok, _ = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S2", 5))], fetched=TUE)
    assert ok
    doc = _doc(tmp_path)
    assert [r["sequenceId"] for r in doc["sequences"]] == ["S1", "S2"]
    assert doc["stamps"] == {"S2": TUE}


def test_a_dict_previous_file_with_a_row_without_an_id_is_refused(tmp_path):
    before = _plant(tmp_path, {"fetched": MON, "sequences": [_row("S1"), {"sent": 3}]})
    ok, lines = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S2"))], fetched=TUE)
    assert not ok and "unkeyable" in lines[0] and "--replace" in lines[0]
    assert _path(tmp_path).read_bytes() == before


def test_duplicate_ids_in_the_previous_file_are_refused_and_replace_starts_again(tmp_path):
    before = _plant(tmp_path, {"fetched": MON, "sequences": [_row("S1", 3), _row("S1", 4)]})
    f = _payload(tmp_path, "a.json", _row("S2"))
    ok, lines = _write(tmp_path, [f], fetched=TUE)
    assert not ok and "duplicate-ids: S1" in lines[0]
    assert _path(tmp_path).read_bytes() == before
    ok, _ = _write(tmp_path, [f], fetched=TUE, replace=True)
    assert ok and [r["sequenceId"] for r in _doc(tmp_path)["sequences"]] == ["S2"]


def test_an_unreadable_previous_file_is_refused_unless_replace(tmp_path):
    before = _plant(tmp_path, "{ this is not json")
    f = _payload(tmp_path, "a.json", _row("S1"))
    ok, lines = _write(tmp_path, [f], fetched=TUE)
    assert not ok and "unreadable" in lines[0]
    assert _path(tmp_path).read_bytes() == before
    ok, _ = _write(tmp_path, [f], fetched=TUE, replace=True)
    assert ok and _doc(tmp_path)["stamps"] == {"S1": TUE}


def test_a_previous_file_of_the_wrong_type_is_refused(tmp_path):
    before = _plant(tmp_path, json.dumps({"sequences": "nope"}))
    ok, lines = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=TUE)
    assert not ok and "unreadable" in lines[0]
    assert _path(tmp_path).read_bytes() == before


def test_a_hand_edit_inside_a_tool_written_file_is_seen_and_refused(tmp_path):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1", 7))], fetched=MON)
    doc = _doc(tmp_path)
    doc["sequences"][0]["prospects"][0]["contacted"] = 999
    _path(tmp_path).write_text(json.dumps(doc), encoding="utf-8")
    assert file_meta(_doc(tmp_path))["edited"] is True
    before = _path(tmp_path).read_bytes()
    ok, lines = _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2"))], fetched=TUE)
    assert not ok and "edited" in lines[0]
    assert _path(tmp_path).read_bytes() == before


def test_replace_over_an_edited_file_writes_a_clean_one(tmp_path):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1", 7))], fetched=MON)
    doc = _doc(tmp_path)
    doc["sequences"][0]["status"] = "active"
    _path(tmp_path).write_text(json.dumps(doc), encoding="utf-8")
    ok, _ = _write(tmp_path, [_payload(tmp_path, "b.json", _row("S1"))], fetched=TUE, replace=True)
    assert ok and file_meta(_doc(tmp_path))["edited"] is False


def test_prune_drops_what_the_payload_does_not_hold_and_says_so(tmp_path):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), _row("S2"))], fetched=MON)
    ok, lines = _write(
        tmp_path, [_payload(tmp_path, "b.json", _row("S2"))], fetched=TUE, prune=True
    )
    assert ok and "S1: dropped (--prune)" in lines
    doc = _doc(tmp_path)
    assert [r["sequenceId"] for r in doc["sequences"]] == ["S2"]
    assert set(doc["stamps"]) == {"S2"} and set(doc["payload_sha256"]) == {"S2"}


def test_without_prune_nothing_is_dropped(tmp_path):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), _row("S2"))], fetched=MON)
    _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2"))], fetched=TUE)
    assert len(_doc(tmp_path)["sequences"]) == 2


def test_forget_removes_one_sequence_and_its_records(tmp_path):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), _row("S2"))], fetched=MON)
    ok, lines = ss.forget(PROFILE, "S1", content_root=tmp_path)
    assert ok and "S1: removed" in lines[0]
    doc = _doc(tmp_path)
    assert [r["sequenceId"] for r in doc["sequences"]] == ["S2"]
    assert "S1" not in doc["stamps"] and doc["body_sha256"] == body_digest(doc)


def test_forget_refuses_an_unknown_id_and_a_missing_or_edited_file(tmp_path):
    ok, lines = ss.forget(PROFILE, "S1", content_root=tmp_path)
    assert not ok and "missing" in lines[0]
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=MON)
    before = _path(tmp_path).read_bytes()
    ok, lines = ss.forget(PROFILE, "S404", content_root=tmp_path)
    assert not ok and "not in" in lines[0] and _path(tmp_path).read_bytes() == before
    doc = _doc(tmp_path)
    doc["sequences"][0]["status"] = "active"
    _path(tmp_path).write_text(json.dumps(doc), encoding="utf-8")
    ok, lines = ss.forget(PROFILE, "S1", content_root=tmp_path)
    assert not ok and "edited" in lines[0]


def test_a_fallen_counter_is_written_noted_and_kept(tmp_path):
    _plant(tmp_path, _legacy(2, sent=40))
    f = _payload(tmp_path, "a.json", _row("L00", 31, delivered=40))
    ok, lines = _write(tmp_path, [f], fetched=TUE)
    assert ok
    note = "L00: sent is 31 now, was 40 at the last snapshot. Check the sending tool if that is unexpected."
    assert any(note in ln for ln in lines)
    doc = _doc(tmp_path)
    assert doc["sequences"][0]["prospects"][0]["contacted"] == 31
    assert doc["falls"]["L00"]["changes"][0] == {"field": "sent", "was": 40, "now": 31}
    assert "L01" not in doc["falls"]
    ok, _ = _write(
        tmp_path, [_payload(tmp_path, "b.json", _row("L00", 35, delivered=40))], fetched=TUE
    )
    assert ok and "L00" in _doc(tmp_path)["falls"]


def test_a_second_fall_keeps_the_highest_figure_it_fell_from(tmp_path):
    _plant(tmp_path, _legacy(1, sent=40))
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("L00", 31, delivered=40))], fetched=MON)
    _write(tmp_path, [_payload(tmp_path, "b.json", _row("L00", 20, delivered=40))], fetched=TUE)
    (change,) = _doc(tmp_path)["falls"]["L00"]["changes"]
    assert change == {"field": "sent", "was": 40, "now": 20}


def test_a_rise_or_an_unchanged_figure_records_no_fall(tmp_path):
    _plant(tmp_path, _legacy(1, sent=40))
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("L00", 40, delivered=40))], fetched=MON)
    _write(tmp_path, [_payload(tmp_path, "b.json", _row("L00", 45, delivered=45))], fetched=TUE)
    assert _doc(tmp_path)["falls"] == {}


def test_a_delivered_fall_is_recorded_on_its_own(tmp_path):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1", 10, delivered=10))], fetched=MON)
    _write(tmp_path, [_payload(tmp_path, "b.json", _row("S1", 10, delivered=8))], fetched=TUE)
    (change,) = _doc(tmp_path)["falls"]["S1"]["changes"]
    assert change == {"field": "delivered", "was": 10, "now": 8}


def test_ack_clears_one_fall_or_all_and_refuses_what_is_not_there(tmp_path):
    _plant(tmp_path, _legacy(2, sent=40))
    _write(
        tmp_path,
        [_payload(tmp_path, "a.json", _row("L00", 1, delivered=40), _row("L01", 2, delivered=40))],
        fetched=TUE,
    )
    assert set(_doc(tmp_path)["falls"]) == {"L00", "L01"}
    ok, _ = ss.ack(PROFILE, "L00", content_root=tmp_path)
    assert ok and set(_doc(tmp_path)["falls"]) == {"L01"}
    ok, lines = ss.ack(PROFILE, "L00", content_root=tmp_path)
    assert not ok and "no recorded fall" in lines[0]
    ok, _ = ss.ack(PROFILE, content_root=tmp_path)
    assert ok and _doc(tmp_path)["falls"] == {}


def test_a_fall_note_is_dropped_when_its_sequence_is_forgotten(tmp_path):
    _plant(tmp_path, _legacy(1, sent=40))
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("L00", 1, delivered=40))], fetched=TUE)
    ss.forget(PROFILE, "L00", content_root=tmp_path)
    assert _doc(tmp_path)["falls"] == {}


def test_status_lists_old_unstamped_and_edited_and_recorded_falls(tmp_path):
    from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS

    _seed_campaign(tmp_path, ["S1", "S2", "S3"])
    old = (NOW - timedelta(days=FIGURES_MAX_AGE_DAYS + 3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1", 9))], fetched=old)
    _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2", 9, delivered=9))], fetched=TUE)
    _write(tmp_path, [_payload(tmp_path, "c.json", _row("S2", 3, delivered=9))], fetched=TUE)
    ok, lines = ss.status(PROFILE, content_root=tmp_path, now=NOW)
    text = "\n".join(lines)
    assert not ok
    assert "S1: figures are" in text and "S3: no figures on file" in text
    assert "S2: sent is 3 now, was 9" in text


def test_status_is_clean_when_every_current_sequence_is_recent(tmp_path):
    _seed_campaign(tmp_path, ["S1"])
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=TUE)
    ok, lines = ss.status(PROFILE, content_root=tmp_path, now=NOW)
    assert ok and lines == ["Every current sequence has recent figures."]


def test_status_reports_a_hand_edit(tmp_path):
    _seed_campaign(tmp_path, ["S1"])
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=TUE)
    doc = _doc(tmp_path)
    doc["sequences"][0]["status"] = "active"
    _path(tmp_path).write_text(json.dumps(doc), encoding="utf-8")
    ok, lines = ss.status(PROFILE, content_root=tmp_path, now=NOW)
    assert not ok and any("changed by hand" in ln for ln in lines)


def test_status_says_so_when_there_is_no_file(tmp_path):
    ok, lines = ss.status(PROFILE, content_root=tmp_path, now=NOW)
    assert not ok and "No per-sequence figures to read" in lines[0]


def _seed_campaign(tmp_path, ids):
    camp = pc._prospects_dir(PROFILE, tmp_path).parent / "plans" / "campaigns"
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "c1.campaign.toml").write_text(
        f'slug = "c1"\ntitle = "Campaign One"\nsequences = {json.dumps(ids)}\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "row",
    [_row("S1", 12, delivered=11), {"id": "S3", "sent": 7, "delivered": 6}],
    ids=["raw-payload", "flat"],
)
def test_the_figures_the_writer_compares_are_the_ones_the_page_shows(row):
    seen = _normalize_seq(row)
    mine = figures_of(row)
    assert mine == {"sent": seen["sent"], "delivered": seen["delivered"]}
    assert mine["sent"] is not None and mine["delivered"] is not None


def test_figures_of_reads_a_missing_figure_as_none_never_zero():
    assert figures_of({"id": "S4"}) == {"sent": None, "delivered": None}
    assert figures_of({"sequenceId": "S2", "prospects": []}) == {"sent": None, "delivered": None}


def test_the_command_line_writes_reports_and_exits_nonzero_on_a_refusal(tmp_path, capsys):
    f = _payload(tmp_path, "a.json", _row("S1"), mtime=datetime.now(UTC))
    base = ["--profile", PROFILE, "--content-root", str(tmp_path)]
    assert ss.main([*base, "write", "--payload", str(f)]) == 0
    assert "Wrote 1 sequence(s)" in capsys.readouterr().out
    before = _path(tmp_path).read_bytes()
    assert ss.main([*base, "forget", "--id", "S404"]) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert _path(tmp_path).read_bytes() == before
    assert ss.main([*base, "forget", "--id", "S1"]) == 0


def test_a_payload_exactly_at_the_hour_is_accepted_and_one_second_past_is_refused(tmp_path):
    assert sl.PAYLOAD_MAX_AGE_S == 3600
    at = NOW - timedelta(seconds=sl.PAYLOAD_MAX_AGE_S)
    ok, _ = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), mtime=at)])
    assert ok
    past = NOW - timedelta(seconds=sl.PAYLOAD_MAX_AGE_S + 1)
    ok, lines = _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2"), mtime=past)])
    assert not ok and "fetch the figures again" in lines[0]


def test_a_later_fall_in_another_counter_keeps_the_earlier_one_and_lists_both_in_field_order(
    tmp_path,
):
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1", 10, delivered=10))], fetched=MON)
    _write(tmp_path, [_payload(tmp_path, "b.json", _row("S1", 7, delivered=10))], fetched=TUE)
    _write(tmp_path, [_payload(tmp_path, "c.json", _row("S1", 9, delivered=6))], fetched=TUE)
    changes = _doc(tmp_path)["falls"]["S1"]["changes"]
    assert changes == [
        {"field": "delivered", "was": 10, "now": 6},
        {"field": "sent", "was": 10, "now": 7},
    ]


def test_a_previous_row_without_a_delivered_figure_never_reads_as_a_fall(tmp_path):
    bare = {"sequenceId": "S1", "prospects": [{"contacted": 5}]}
    f = tmp_path / "bare.json"
    f.write_text(json.dumps({"sequences": [bare]}), encoding="utf-8")
    os.utime(f, (NOW.timestamp(), NOW.timestamp()))
    assert _write(tmp_path, [f], fetched=MON)[0]
    ok, lines = _write(
        tmp_path, [_payload(tmp_path, "b.json", _row("S1", 7, delivered=3))], fetched=TUE
    )
    assert ok and _doc(tmp_path)["falls"] == {}


def test_forgetting_or_pruning_an_inherited_sequence_drops_it_from_the_inherited_list(tmp_path):
    _plant(tmp_path, _legacy(3))
    ok, _ = ss.forget(PROFILE, "L00", content_root=tmp_path)
    assert ok and "L00" not in _doc(tmp_path)["inherited"]
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("L01"))], fetched=TUE, prune=True)
    assert _doc(tmp_path)["inherited"] == []


def test_status_does_not_call_figures_exactly_at_the_limit_old(tmp_path):
    from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS

    _seed_campaign(tmp_path, ["S1"])
    at = (NOW - timedelta(days=FIGURES_MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=at)
    ok, lines = ss.status(PROFILE, content_root=tmp_path, now=NOW)
    assert ok, lines


def test_a_new_row_without_a_delivered_figure_never_reads_as_a_fall(tmp_path):
    assert _write(
        tmp_path, [_payload(tmp_path, "a.json", _row("S1", 5, delivered=5))], fetched=MON
    )[0]
    bare = {"sequenceId": "S1", "prospects": [{"contacted": 6}]}
    f = tmp_path / "bare.json"
    f.write_text(json.dumps({"sequences": [bare]}), encoding="utf-8")
    os.utime(f, (NOW.timestamp(), NOW.timestamp()))
    assert _write(tmp_path, [f], fetched=TUE)[0]
    assert _doc(tmp_path)["falls"] == {}


def test_status_says_so_when_the_file_holds_no_sequences_and_no_campaign_lists_one(tmp_path):
    _plant(tmp_path, {"fetched": MON, "sequences": []})
    ok, lines = ss.status(PROFILE, content_root=tmp_path, now=NOW)
    assert not ok and "No per-sequence figures to read" in lines[0]


# --- what the writer refuses, and that every refusal leaves the file alone --------------------


def _p(**counters):
    """A payload row whose ``prospects[0]`` carries the given counters on top of a good row."""
    row = _row("S9", 5)
    row["prospects"][0].update(counters)
    return row


def _e(**counters):
    row = _row("S9", 5)
    row["emails"]["status"].update(counters)
    return row


def _seed_one(tmp_path):
    ok, lines = _write(tmp_path, [_payload(tmp_path, "good.json", _row("S1", 7))], fetched=MON)
    assert ok, lines
    return _path(tmp_path).read_bytes()


def _refused(tmp_path, payload_file, needle, **kw):
    before = _seed_one(tmp_path)
    ok, lines = _write(tmp_path, [payload_file], fetched=TUE, **kw)
    assert not ok and len(lines) == 1 and lines[0].startswith("REFUSED"), lines
    assert needle in lines[0], lines[0]
    assert _path(tmp_path).read_bytes() == before
    return lines[0]


_SHAPES = [
    pytest.param({"sequences": _row("D1")}, '"sequences" is not a list', id="sequences-is-a-row"),
    pytest.param({"sequences": "abc"}, '"sequences" is not a list', id="sequences-is-text"),
    pytest.param({"sequences": None}, '"sequences" is not a list', id="sequences-is-null"),
    pytest.param({"payload": _row("W1")}, "wrapped", id="wrapped-row"),
    pytest.param({"payload": [_row("W2")]}, "wrapped", id="wrapped-list"),
    pytest.param(
        {"message": "ok", "payload": _row("W3")}, "wrapped", id="the-tool-reply-as-it-comes"
    ),
    pytest.param({}, "no sequence with a sequenceId", id="empty-object"),
    pytest.param([], "no sequence with a sequenceId", id="empty-list"),
    pytest.param({"sequences": []}, "no sequence with a sequenceId", id="empty-sequences"),
    pytest.param({"sequences": [_row("V1"), 7]}, "sequences[1] is not an object", id="number"),
    pytest.param({"sequences": [_row("V1"), None]}, "sequences[1] is not an object", id="null"),
    pytest.param({"sequences": ["x", _row("V2")]}, "sequences[0] is not an object", id="text"),
    pytest.param([_row("V3"), [1]], "sequences[1] is not an object", id="bare-list-with-a-list"),
    pytest.param(
        {"sequences": [_row("OK1"), {"prospects": [{"contacted": 3}]}]},
        "sequences[1] has no usable sequenceId",
        id="row-without-an-id",
    ),
    pytest.param(
        {"sequences": [{"id": "S3", "sent": 3, "delivered": 3}]},
        "sequences[0] has no usable sequenceId",
        id="flat-row-keyed-id",
    ),
    pytest.param(
        {"sequences": [dict(_row("S9"), sequenceId="  ")]}, "no usable sequenceId", id="blank-id"
    ),
    pytest.param(
        {"sequences": [dict(_row("S9"), sequenceId=0)]}, "no usable sequenceId", id="zero-id"
    ),
    pytest.param(
        {"sequences": [dict(_row("S9"), sequenceId=True)]}, "no usable sequenceId", id="bool-id"
    ),
    pytest.param(
        {"sequences": [dict(_row("S9"), sequenceId=["S9"])]}, "no usable sequenceId", id="list-id"
    ),
    pytest.param(
        {"sequences": [dict(_row("S9"), sequenceId=" S9 ")]}, "no usable sequenceId", id="padded-id"
    ),
]


@pytest.mark.parametrize(("payload", "needle"), _SHAPES)
def test_a_payload_in_a_shape_the_writer_cannot_key_is_refused_by_name(tmp_path, payload, needle):
    _refused(tmp_path, _payload_of(tmp_path, "bad.json", payload), needle)


def _payload_of(tmp_path, name, content, *, mtime=NOW):
    """A payload file holding ``content`` as given (``_payload`` always wraps rows in a list)."""
    p = tmp_path / "payloads" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    os.utime(p, (mtime.timestamp(), mtime.timestamp()))
    return p


_COUNTERS = [
    pytest.param(_e(delivered=-5), "emails.status.delivered", id="negative-delivered"),
    pytest.param(_p(contacted="-3"), "prospects[0].contacted", id="negative-text-contacted"),
    pytest.param(_p(replied=-1), "prospects[0].replied", id="negative-replied"),
    pytest.param(_e(hardBounced=-2), "emails.status.hardBounced", id="negative-bounce-bucket"),
    pytest.param(_p(total="12.5"), "prospects[0].total", id="fractional-text"),
    pytest.param(_p(total=12.5), "prospects[0].total", id="fractional-number"),
    pytest.param(_p(total=3.0), "prospects[0].total", id="a-float-is-not-a-count"),
    pytest.param(_e(delivered="lots"), "emails.status.delivered", id="words"),
    pytest.param(_p(open=True), "prospects[0].open", id="a-boolean"),
    pytest.param(_p(unsubscribed=[1]), "prospects[0].unsubscribed", id="a-list"),
]


@pytest.mark.parametrize(("row", "where"), _COUNTERS)
def test_a_counter_that_is_not_a_whole_number_of_zero_or_more_is_refused(tmp_path, row, where):
    msg = _refused(tmp_path, _payload(tmp_path, "bad.json", row), where)
    assert "sequences[0]" in msg and "S9" in msg


def test_counters_as_the_provider_types_them_are_accepted(tmp_path):
    """Numbers as text ("10"), blanks and nulls are the real payload — refusing them would make
    the writer unusable, so this is the other half of the counter rule."""
    row = _row("S9", 5)
    row["prospects"][0].update({"total": "10", "contacted": "8", "replied": "", "open": None})
    row["emails"]["status"].update({"delivered": "8", "bounced": 0})
    ok, lines = _write(tmp_path, [_payload(tmp_path, "a.json", row)], fetched=MON)
    assert ok, lines


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        pytest.param(
            '{"sequences":[{"sequenceId":"S9","prospects":[{"contacted":1e999}],'
            '"emails":{"status":{"delivered":6}}}]}',
            "not a finite number",
            id="overflowing-counter",
        ),
        pytest.param(
            '{"sequences":[{"sequenceId":"S9","rating":NaN,"prospects":[{"contacted":3}],'
            '"emails":{"status":{"delivered":3}}}]}',
            "sequences[0].rating",
            id="nan-in-any-field",
        ),
        pytest.param(
            '{"sequences":[{"sequenceId":"S9","prospects":[{"contacted":3,"x":[-Infinity]}],'
            '"emails":{"status":{"delivered":3}}}]}',
            "not a finite number",
            id="nested-infinity",
        ),
        pytest.param(
            '{"sequences":[{"sequenceId":"S9","sequenceName":"Launch \\ud83d",'
            '"prospects":[{"contacted":3}],"emails":{"status":{"delivered":3}}}]}',
            "UTF-8",
            id="lone-surrogate-in-a-value",
        ),
        pytest.param(
            '{"sequences":[{"sequenceId":"S9","bad\\udc00key":1,"prospects":[{"contacted":3}],'
            '"emails":{"status":{"delivered":3}}}]}',
            "UTF-8",
            id="lone-surrogate-in-a-key",
        ),
    ],
)
def test_a_payload_the_file_cannot_hold_is_refused_not_crashed_on(tmp_path, text, needle):
    _refused(tmp_path, _payload_of(tmp_path, "bad.json", text), needle)


@pytest.mark.parametrize(
    ("depth", "needle"),
    [(5000, "levels deep"), (200000, "not readable as JSON")],
    ids=["deeper-than-a-reply-is", "deeper-than-the-parser-allows"],
)
def test_a_payload_nested_absurdly_deep_is_refused_not_crashed_on(tmp_path, depth, needle):
    deep = '{"sequences":[{"sequenceId":"S9","x":' + "[" * depth + "]" * depth + "}]}"
    _refused(tmp_path, _payload_of(tmp_path, "bad.json", deep), needle)


def test_the_stats_file_fed_back_as_a_payload_is_refused_and_restamps_nothing(tmp_path):
    """Replaying the file the writer made would re-date every row to now with nothing fetched."""
    _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1", 7), _row("S2", 9))], fetched=MON)
    path = _path(tmp_path)
    before = path.read_bytes()
    os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    ok, lines = _write(tmp_path, [path], fetched=TUE)
    assert not ok and "figures file" in lines[0]
    copy = _payload_of(tmp_path, "copy.json", before.decode("utf-8"))
    ok, lines = _write(tmp_path, [copy], fetched=TUE)
    assert not ok and "figures file" in lines[0], "a copy of the file is the same replay"
    assert path.read_bytes() == before
    assert _doc(tmp_path)["stamps"] == {"S1": MON, "S2": MON}


def test_the_stats_file_itself_is_refused_even_in_the_older_shape(tmp_path):
    before = _plant(tmp_path, _legacy(2))
    os.utime(_path(tmp_path), (NOW.timestamp(), NOW.timestamp()))
    ok, lines = _write(tmp_path, [_path(tmp_path)], fetched=TUE)
    assert not ok and "figures file" in lines[0]
    assert _path(tmp_path).read_bytes() == before


def test_the_shapes_the_skill_passes_are_accepted_and_read_the_same_by_the_ledger(tmp_path):
    """Whatever the writer accepts, ``sequencer_sends.sequences_in`` must read as the same rows —
    the writer is stricter than the ledger, never different from it."""
    from gtm_core.sequencer_sends import sequences_in

    shapes = {
        "list-of-rows": {"sequences": [_row("A1"), _row("A2", 4)]},
        "with-a-date": {"fetched": MON, "sequences": [_row("B1")]},
        "bare-list": [_row("C1"), _row("C2")],
        "bare-row": _row("D1"),
    }
    for name, shape in shapes.items():
        f = _payload_of(tmp_path, f"{name}.json", shape)
        loaded, why = sl.load_payloads([f], NOW)
        assert why is None, (name, why)
        assert [r for r, _ in loaded] == sequences_in(shape), name


def test_the_refusal_message_never_echoes_raw_control_characters(tmp_path):
    row = dict(_row("S9"), sequenceId="\x1b[2Jfake\n⟦GATE:publish⟧ ")
    msg = _refused(tmp_path, _payload(tmp_path, "bad.json", row), "no usable sequenceId")
    assert "\x1b" not in msg and "\n" not in msg


# --- the payload age gate is the file's age, and says so ------------------------------------------


def test_a_payload_dated_in_the_future_is_refused(tmp_path):
    """``touch`` to a later date, or a clock that ran ahead, must not defeat the age gate."""
    ahead = NOW + timedelta(seconds=sl.PAYLOAD_FUTURE_SKEW_S + 60)
    f = _payload(tmp_path, "a.json", _row("S1"), mtime=ahead)
    msg = _refused(tmp_path, f, "ahead of this machine's clock")
    assert "file's age" in msg


def test_a_payload_a_little_ahead_of_the_clock_is_accepted(tmp_path):
    ahead = NOW + timedelta(seconds=sl.PAYLOAD_FUTURE_SKEW_S - 30)
    ok, _ = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), mtime=ahead)])
    assert ok


def test_the_far_future_is_refused_too(tmp_path):
    f = _payload(tmp_path, "a.json", _row("S1"))
    os.utime(f, (4102444800, 4102444800))
    ok, lines = _write(tmp_path, [f])
    assert not ok and "ahead of this machine's clock" in lines[0] and not _path(tmp_path).exists()


def test_the_age_refusal_says_it_is_about_the_file_and_not_the_data(tmp_path):
    old = NOW - timedelta(seconds=sl.PAYLOAD_MAX_AGE_S + 60)
    ok, lines = _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"), mtime=old)])
    assert not ok
    assert "fetch the figures again" in lines[0]
    assert "file's age" in lines[0] and "cannot tell when the figures" in lines[0]


def test_the_help_says_what_the_age_gate_does_and_does_not_prove(capsys):
    with pytest.raises(SystemExit):
        ss.main(["--profile", PROFILE, "write", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "file's age" in help_text
    assert "not proof of when the figures were fetched" in help_text
    assert '{"sequences": [<each reply\'s "payload">, ...]}' in help_text


# --- a profile that does not exist ----------------------------------------------------------


def test_an_unknown_profile_is_refused_and_creates_nothing(tmp_path):
    f = _payload(tmp_path, "a.json", _row("S1"))
    before = sorted(tmp_path.rglob("*"))
    ok, lines = ss.write("acmee", [f], content_root=tmp_path, now=NOW)
    assert not ok and "acmee" in lines[0] and "no profile" in lines[0]
    assert sorted(tmp_path.rglob("*")) == before, "a typo'd profile must not grow a tenant tree"
    for ok, lines in (
        ss.forget("acmee", "S1", content_root=tmp_path),
        ss.ack("acmee", None, content_root=tmp_path),
    ):
        assert not ok and "no profile" in lines[0]
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("name", ["../evil", "a/b", ".."])
def test_a_profile_name_that_is_not_a_bare_name_is_refused_not_raised(tmp_path, name):
    f = _payload(tmp_path, "a.json", _row("S1"))
    ok, lines = ss.write(name, [f], content_root=tmp_path, now=NOW)
    assert not ok and lines[0].startswith("REFUSED")


def test_the_command_line_refuses_an_unknown_profile_with_a_nonzero_exit(tmp_path, capsys):
    f = _payload(tmp_path, "a.json", _row("S1"), mtime=datetime.now(UTC))
    rc = ss.main(
        ["--profile", "acmee", "--content-root", str(tmp_path), "write", "--payload", str(f)]
    )
    assert rc == 1 and "no profile" in capsys.readouterr().out
    assert not (tmp_path / "acmee").exists()


# --- a fall is seen from whatever counters the rows carry -------------------------------------------


def test_a_fall_is_seen_when_the_payload_has_no_prospects_list(tmp_path):
    first = {"sequenceId": "NP", "emails": {"status": {"delivered": 40}}}
    second = {"sequenceId": "NP", "emails": {"status": {"delivered": 31}}}
    assert _write(tmp_path, [_payload(tmp_path, "a.json", first)], fetched=MON)[0]
    ok, lines = _write(tmp_path, [_payload(tmp_path, "b.json", second)], fetched=TUE)
    assert ok and any("delivered is 31 now, was 40" in ln for ln in lines)
    (change,) = _doc(tmp_path)["falls"]["NP"]["changes"]
    assert change == {"field": "delivered", "was": 40, "now": 31}


# --- the write is atomic: a crash leaves the old file, a reader never sees half a new one -----------


def _no_temp_files(tmp_path):
    return [p.name for p in _path(tmp_path).parent.iterdir() if p.name.startswith(".tmp")]


def test_a_failed_replace_leaves_the_previous_file_and_no_temp_file(tmp_path, monkeypatch):
    before = _seed_one(tmp_path)

    def crash(src, dst):
        raise OSError("simulated crash before the rename")

    monkeypatch.setattr("gtm_core.fsio.os.replace", crash)
    with pytest.raises(OSError, match="simulated crash"):
        _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2"))], fetched=TUE)
    assert _path(tmp_path).read_bytes() == before
    assert _no_temp_files(tmp_path) == []


def test_forget_and_ack_replace_the_file_the_same_way(tmp_path, monkeypatch):
    before = _seed_one(tmp_path)
    calls = []
    real = os.replace

    def watch(src, dst):
        calls.append(os.fspath(dst))
        real(src, dst)

    monkeypatch.setattr("gtm_core.fsio.os.replace", watch)
    assert ss.forget(PROFILE, "S1", content_root=tmp_path)[0]
    assert calls == [os.fspath(_path(tmp_path))]
    assert _path(tmp_path).read_bytes() != before

    def crash(src, dst):
        raise OSError("simulated crash before the rename")

    _write(tmp_path, [_payload(tmp_path, "c.json", _row("S3"))], fetched=TUE)
    kept = _path(tmp_path).read_bytes()
    monkeypatch.setattr("gtm_core.fsio.os.replace", crash)
    with pytest.raises(OSError):
        ss.forget(PROFILE, "S3", content_root=tmp_path)
    assert _path(tmp_path).read_bytes() == kept and _no_temp_files(tmp_path) == []


def test_the_destination_is_whole_at_the_instant_it_is_replaced(tmp_path, monkeypatch):
    """The old file must still be a complete, valid one when the rename happens, and the write
    must reach the rename at all — a plain ``write_text`` truncates in place and never does."""
    _seed_one(tmp_path)
    seen = []
    real = os.replace

    def watch(src, dst):
        seen.append(json.loads(open(dst, encoding="utf-8").read())["stamps"])
        real(src, dst)

    monkeypatch.setattr("gtm_core.fsio.os.replace", watch)
    assert _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2"))], fetched=TUE)[0]
    assert seen == [{"S1": MON}]


# --- malformed ``falls`` in the file: the page and ``status`` say so, they do not crash ------------

_FALL = {"field": "sent", "was": 40, "now": 31}
_MALFORMED_FALLS = {
    "item-lacks-keys": {"S1": {"on": MON, "changes": [{"x": 1}]}},
    "changes-is-text": {"S1": {"on": MON, "changes": "abc"}},
    "changes-is-an-object": {"S1": {"on": MON, "changes": {"field": "sent"}}},
    "markup-in-the-field": {
        "S1": {"on": MON, "changes": [{"field": "<script>alert(1)</script>", "was": 1, "now": 0}]}
    },
    "falls-is-a-list": ["S1"],
    "figures-as-text": {"S1": {"on": MON, "changes": [{**_FALL, "now": "31"}]}},
}


def _plant_falls(tmp_path, falls, *, rehash):
    """A real writer-made file for a seeded dashboard profile, then ``falls`` swapped in."""
    from tests.test_email_campaign_dashboard import _seed

    profile = _seed(tmp_path, PROFILE)
    real = datetime.now(UTC)  # the dashboard reads the real clock, so the figures must be recent
    recent = (real - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    f = _payload(tmp_path, "a.json", _row("S1", 7), mtime=real)
    assert _write(tmp_path, [f], fetched=recent, now=real)[0]
    doc = _doc(tmp_path)
    doc["falls"] = falls
    if rehash:
        doc["body_sha256"] = body_digest(doc)
    _path(tmp_path).write_text(json.dumps(doc), encoding="utf-8")
    return profile


@pytest.mark.parametrize("rehash", [False, True], ids=["stale-hash", "recomputed-hash"])
@pytest.mark.parametrize("falls", _MALFORMED_FALLS.values(), ids=_MALFORMED_FALLS.keys())
def test_a_malformed_falls_entry_ages_the_page_as_unknown_and_says_why(tmp_path, falls, rehash):
    from gtm_core import email_campaign_dashboard as gd

    profile = _plant_falls(tmp_path, falls, rehash=rehash)
    model = gd.build_model(profile, tmp_path)
    page = gd.render_html(model)
    assert model["figures"]["state"] == "undated" and model["figures"]["cause"] == "edited"
    assert "changed by hand" in page
    assert "<script>alert(1)" not in page and "email-fall-" not in page
    ok, lines = ss.status(profile, content_root=tmp_path, now=datetime.now(UTC))
    assert not ok and any("changed by hand" in ln for ln in lines)


@pytest.mark.parametrize("falls", _MALFORMED_FALLS.values(), ids=_MALFORMED_FALLS.keys())
def test_a_malformed_falls_entry_blocks_a_merge_and_names_why(tmp_path, falls):
    profile = _plant_falls(tmp_path, falls, rehash=True)
    before = _path(tmp_path).read_bytes()
    ok, lines = _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2"))], fetched=TUE)
    assert not ok and "recorded falls" in lines[0] and "--replace" in lines[0]
    ok, lines = ss.ack(profile, None, content_root=tmp_path)
    assert not ok and "edited" in lines[0]
    assert _path(tmp_path).read_bytes() == before


def test_a_well_formed_fall_still_shows_on_the_page(tmp_path):
    from gtm_core import email_campaign_dashboard as gd

    profile = _plant_falls(tmp_path, {"S1": {"on": MON, "changes": [_FALL]}}, rehash=True)
    model = gd.build_model(profile, tmp_path)
    assert model["figures"]["cause"] != "edited"
    assert "sent is 31 now, was 40" in gd.render_html(model)


# --- the wording: recorded by the refresh command, never verified -----------------------------


def test_the_digest_is_worded_as_recorded_by_the_refresh_command_never_as_verified(
    tmp_path, capsys
):
    """At every surface an operator or agent reads: the help, the refusal, ``status``, the page."""
    import re

    from gtm_core import email_campaign_dashboard as gd

    with pytest.raises(SystemExit):
        ss.main(["--profile", PROFILE, "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "recorded by the refresh command" in help_text

    profile = _plant_falls(tmp_path, {}, rehash=True)
    doc = _doc(tmp_path)
    doc["sequences"][0]["status"] = "active"
    _path(tmp_path).write_text(json.dumps(doc), encoding="utf-8")  # an edit the hash records
    ok, refusal = _write(tmp_path, [_payload(tmp_path, "b.json", _row("S2"))], fetched=TUE)
    assert not ok and "recorded" in refusal[0]
    _, status_lines = ss.status(profile, content_root=tmp_path, now=datetime.now(UTC))
    assert any("since the refresh command wrote it" in ln for ln in status_lines)
    page = gd.render_html(gd.build_model(profile, tmp_path))
    # The page is long and says "verification" about email addresses elsewhere, so hold only the
    # sentence that speaks about the digest: the one carrying the refresh command's name.
    (sentence,) = set(re.findall(r"[^<>]*since the refresh command wrote it[^<>]*", page))

    for surface in (help_text, *refusal, *status_lines, sentence):
        assert not re.search(r"verif|authentic|genuine|trusted", surface, re.I), surface[:200]


# --- ids across two fetches: what the writer does when an id comes, goes or changes ----------------
#
# Replaces ``test_sequence_ids_are_unique_and_stable_across_two_fetches`` in
# ``tests/contracts/test_dashboard_figure_ages.py``, which built both "fetches" from the same ids and
# so could not fail. Whether a real provider keeps an id stable across two real fetches needs the
# network and is NOT proved here; what is proved is what the writer and ``status`` do when it does
# not.


def _stamp(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_sequence_missing_from_a_later_fetch_stays_old_on_file_and_forget_clears_it(tmp_path):
    from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS as LIMIT

    _seed_campaign(tmp_path, ["S1", "S2"])
    first = _payload(tmp_path, "a.json", _row("S1"), _row("S2"), _row("S3"))
    assert _write(tmp_path, [first], fetched=_stamp(LIMIT + 3))[0]
    second = _payload(tmp_path, "b.json", _row("S1", 11), _row("S2", 12))
    assert _write(tmp_path, [second], fetched=TUE)[0]
    doc = _doc(tmp_path)
    assert doc["stamps"] == {"S1": TUE, "S2": TUE, "S3": _stamp(LIMIT + 3)}, "S3 keeps its own date"
    ok, lines = ss.status(PROFILE, content_root=tmp_path, now=NOW)
    assert not ok and len(lines) == 1 and lines[0].startswith("S3: figures are")
    assert "no current campaign lists it" in lines[0] and "forget --id S3" in lines[0]
    assert ss.forget(PROFILE, "S3", content_root=tmp_path)[0]
    assert ss.status(PROFILE, content_root=tmp_path, now=NOW) == (
        True,
        ["Every current sequence has recent figures."],
    )


def test_prune_is_the_other_way_to_let_a_deleted_sequence_go(tmp_path):
    first = _payload(tmp_path, "a.json", _row("S1"), _row("S3"))
    assert _write(tmp_path, [first], fetched=MON)[0]
    ok, lines = _write(
        tmp_path, [_payload(tmp_path, "b.json", _row("S1"))], fetched=TUE, prune=True
    )
    assert ok and "S3: dropped (--prune)" in lines
    assert set(_doc(tmp_path)["stamps"]) == {"S1"}


def test_a_sequence_that_comes_back_under_a_new_id_is_not_read_as_refreshed(tmp_path):
    """The campaign still lists the old id; a fetch that returns the same sequence under another id
    must leave the old one old (and the page aged), not quietly count as today's figures."""
    from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS as LIMIT

    _seed_campaign(tmp_path, ["A1"])
    assert _write(tmp_path, [_payload(tmp_path, "a.json", _row("A1"))], fetched=_stamp(LIMIT + 3))[
        0
    ]
    assert _write(tmp_path, [_payload(tmp_path, "b.json", _row("A1b"))], fetched=TUE)[0]
    doc = _doc(tmp_path)
    assert [r["sequenceId"] for r in doc["sequences"]] == ["A1", "A1b"]
    assert doc["stamps"]["A1"] == _stamp(LIMIT + 3) and doc["stamps"]["A1b"] == TUE
    ok, lines = ss.status(PROFILE, content_root=tmp_path, now=NOW)
    assert not ok and [ln.split(":")[0] for ln in lines] == ["A1"]


def test_an_id_that_appears_twice_across_two_payload_files_in_one_pass_is_refused(tmp_path):
    first = _payload(tmp_path, "a.json", _row("U1"), _row("U2"))
    again = _payload(tmp_path, "b.json", _row("U2", 9))
    ok, lines = _write(tmp_path, [first, again], fetched=TUE)
    assert not ok and "sequence U2 appears twice" in lines[0] and not _path(tmp_path).exists()


def test_a_payload_an_hour_ahead_of_the_clock_is_refused(tmp_path):
    """Pinned in plain seconds, not through the constant, so a skew allowance that grows past
    "a little clock drift" cannot slip through by redefining what the test compares against."""
    assert sl.PAYLOAD_FUTURE_SKEW_S < 600
    f = _payload(tmp_path, "a.json", _row("S1"), mtime=NOW + timedelta(hours=1))
    ok, lines = _write(tmp_path, [f])
    assert not ok and "ahead of this machine's clock" in lines[0]


def _bare(sid, sent=10):
    """A ``get_sequence_stats`` row as the tool returns it: no status at all."""
    row = _row(sid, sent)
    del row["status"]
    return row


def _list_reply(tmp_path, *rows):
    p = tmp_path / "payloads" / "list.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({"message": "ok", "payload": list(rows)}), encoding="utf-8")
    return p


def test_the_list_reply_stamps_each_sequence_active_or_paused_with_its_step_count(tmp_path):
    """The figures fetch carries no active/paused flag, so every campaign read "started". The
    sending tool's own sequence list carries it; the writer joins it on the id."""
    f = _payload(tmp_path, "a.json", _bare("S1"), _bare("S2"), _bare("S3"))
    lst = _list_reply(
        tmp_path,
        {"id": "S1", "title": "one", "active": True, "steps": [{}, {}]},
        {"id": "S2", "title": "two", "active": False, "steps": [{}]},
    )
    ok, lines = _write(tmp_path, [f], fetched=MON, sequences=[lst])
    assert ok, lines
    rows = {r["sequenceId"]: r for r in _doc(tmp_path)["sequences"]}
    assert (rows["S1"]["status"], rows["S1"]["steps"]) == ("active", 2)
    assert (rows["S2"]["status"], rows["S2"]["steps"]) == ("paused", 1)
    # Absent from the list: no status is guessed.
    assert "status" not in rows["S3"] and "steps" not in rows["S3"]
    norm = _normalize_seq(rows["S1"])
    assert (norm["status"], norm["steps"]) == ("active", 2)
    assert _normalize_seq(rows["S3"])["steps"] == 0  # not known: no emails goal is built on it


def test_a_list_row_without_a_true_false_active_refuses_the_write(tmp_path):
    f = _payload(tmp_path, "a.json", _bare("S1"))
    lst = _list_reply(tmp_path, {"id": "S1", "title": "one", "active": "yes"})
    ok, lines = _write(tmp_path, [f], fetched=MON, sequences=[lst])
    assert not ok and "REFUSED" in lines[0] and "active" in lines[0]
    assert not _path(tmp_path).exists()

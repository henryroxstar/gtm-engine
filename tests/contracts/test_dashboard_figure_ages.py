"""One date per sequence, rolled up once: which sequences set the page's age, what the page says
it used, and what a recorded fall or a hand edit does to the page.

The figures file is written by the real writer (``gtm_core.sequencer_snapshot``), never by a
hand-built fixture, so a change to the file's shape is seen here. Every id is fictional.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core import sequencer_snapshot as ss
from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS
from gtm_core.sequencer_sends import _fetched_date, plan_sends
from tests.contracts.test_dashboard_figures_age import _header
from tests.contracts.test_dashboard_ps20_scoped_strip import _campaign, _scoped
from tests.contracts.test_dashboard_ps20_trust import _stats
from tests.test_email_campaign_dashboard import _seed

LIMIT = FIGURES_MAX_AGE_DAYS


def _at(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(sid, sent=10, delivered=None):
    delivered = sent if delivered is None else delivered
    return {
        "sequenceId": sid,
        "sequenceName": f"Name {sid}",
        "status": "paused",
        "prospects": [{"total": sent, "contacted": sent}],
        "emails": {"status": {"delivered": delivered}},
    }


def _refresh(tmp_path, profile, rows, days_ago, **kw):
    f = (
        tmp_path
        / "payloads"
        / f"p{abs(hash((tuple(r['sequenceId'] for r in rows), days_ago)))}.json"
    )
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps({"sequences": rows}), encoding="utf-8")
    ok, lines = ss.write(profile, [f], content_root=tmp_path, fetched=_at(days_ago), **kw)
    assert ok, lines
    return lines


def _page(tmp_path, profile):
    m = gd.build_model(profile, tmp_path)
    return m, gd.render_html(m)


def test_a_sequence_in_the_figures_that_no_campaign_lists_still_sets_the_age(tmp_path):
    profile = _seed(tmp_path)  # c1 lists S1
    _refresh(tmp_path, profile, [_row("S1")], 0.1)
    _refresh(tmp_path, profile, [_row("X9")], LIMIT + 2)
    m, page = _page(tmp_path, profile)
    assert m["figures"]["over_limit"] and "figures-old" in m["warnings"]
    assert "the oldest of 2 sequences in use" in _header(page)
    assert "1 of them in no campaign" in _header(page)


def test_a_sequence_a_campaign_lists_but_the_figures_lack_makes_the_age_unknown(tmp_path):
    profile = _seed(tmp_path)
    _campaign(tmp_path, profile, "c2", "Campaign Two", ["S2"])
    _refresh(tmp_path, profile, [_row("S1")], 0.1)
    m, page = _page(tmp_path, profile)
    assert m["figures"]["state"] == "undated" and m["figures"]["over_limit"]
    assert m["figures"]["cause_ids"] == ["S2"] and "S2: no figures on file" in page
    assert "records-disagree" in m["warnings"] and "figures-old" in m["warnings"]


def test_a_missing_sequence_ages_only_the_pages_that_list_it(tmp_path):
    profile = _seed(tmp_path)
    _campaign(tmp_path, profile, "c2", "Campaign Two", ["S2"])
    _refresh(tmp_path, profile, [_row("S1")], 0.1)
    c1, c2 = _scoped(tmp_path, profile, "c1"), _scoped(tmp_path, profile, "c2")
    assert c1["figures"]["state"] == "dated" and "figures-old" not in c1["warnings"]
    assert c2["figures"]["state"] == "undated" and c2["figures"]["cause_ids"] == ["S2"]
    assert "figures-old" in c2["warnings"]


def test_an_archived_sequence_stops_setting_the_age_but_still_listed_ones_do(tmp_path):
    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    _refresh(tmp_path, profile, [_row("S1")], 0.1)
    _refresh(tmp_path, profile, [_row("S2")], LIMIT + 3)

    def body(extra):
        return (
            'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1", "S2"]\n'
            f"{extra}\n[targets]\nemails = 9\n"
        )

    camp.write_text(body(""), encoding="utf-8")
    assert gd.build_model(profile, tmp_path)["figures"]["over_limit"]
    camp.write_text(
        body('archived_sequences = ["S2"]').replace('["S1", "S2"]', '["S1"]'), encoding="utf-8"
    )
    m = gd.build_model(profile, tmp_path)
    assert m["figures"]["state"] == "dated" and not m["figures"]["over_limit"]
    assert "figures-old" not in m["warnings"]


def test_a_campaign_page_is_dated_by_its_own_sequences_and_says_so(tmp_path):
    profile = _seed(tmp_path)
    _campaign(tmp_path, profile, "c2", "Campaign Two", ["S2"])
    _refresh(tmp_path, profile, [_row("S1")], 0.1)
    _refresh(tmp_path, profile, [_row("S2")], LIMIT + 2)
    rollup = gd.build_model(profile, tmp_path)
    assert rollup["figures"]["over_limit"] and "figures-old" in rollup["warnings"]
    c1 = _scoped(tmp_path, profile, "c1")
    assert not c1["figures"]["over_limit"] and "figures-old" not in c1["warnings"]
    both = _scoped(tmp_path, profile, "c1,c2")
    assert both["figures"]["over_limit"] and "figures-old" in both["warnings"]
    page = gd.render_html(both)
    assert "the oldest of the 2 sequences on this page" in _header(page)
    assert "the 1 sequence on this page" in _header(gd.render_html(c1))


def test_a_file_that_only_has_one_date_for_everything_says_the_date_is_the_files(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": _at(1), "sequences": [{"id": "S1", "sent": 0}]})
    _, page = _page(tmp_path, profile)
    assert "date from the file" in _header(page)
    _refresh(tmp_path, profile, [_row("S1")], 1, replace=True)
    _, page = _page(tmp_path, profile)
    assert "date from the file" not in _header(page)


def test_a_hand_edited_file_makes_the_age_unknown_and_says_why(tmp_path):
    profile = _seed(tmp_path)
    _refresh(tmp_path, profile, [_row("S1")], 0.1)
    path = ss.stats_path(profile, tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["sequences"][0]["prospects"][0]["contacted"] = 999
    path.write_text(json.dumps(doc), encoding="utf-8")
    m, page = _page(tmp_path, profile)
    assert m["figures"]["state"] == "undated" and m["figures"]["cause"] == "edited"
    assert "changed by hand" in page


def test_a_fallen_counter_shows_on_the_sequence_row_and_stays_until_acknowledged(tmp_path):
    profile = _seed(tmp_path)
    _refresh(tmp_path, profile, [_row("S1", 40, delivered=40)], 2)
    _refresh(tmp_path, profile, [_row("S1", 31, delivered=40)], 1)
    note = (
        "sent is 31 now, was 40 at the last snapshot. Check the sending tool if that is unexpected."
    )
    for _ in range(2):
        _, page = _page(tmp_path, profile)
        assert re.search(rf'data-figure="email-fall-S1">{re.escape(note)}</div>', page)
        _refresh(tmp_path, profile, [_row("S1", 33, delivered=40)], 0.5)
    ok, _ = ss.ack(profile, "S1", content_root=tmp_path)
    assert ok
    _, page = _page(tmp_path, profile)
    assert "email-fall-S1" not in page


def test_a_fall_that_never_happened_leaves_no_note(tmp_path):
    profile = _seed(tmp_path)
    _refresh(tmp_path, profile, [_row("S1", 10)], 2)
    _refresh(tmp_path, profile, [_row("S1", 12)], 1)
    _, page = _page(tmp_path, profile)
    assert "email-fall-" not in page


def _plan(doc, existing):
    fetched = _fetched_date([doc], "")
    return plan_sends(
        [doc],
        existing,
        lane_by_seq={},
        cell_by_seq={},
        email_to_cell={},
        email_to_lane={},
        fetched=fetched,
    )


def _file(profile, tmp_path):
    return json.loads(ss.stats_path(profile, tmp_path).read_text(encoding="utf-8"))


def test_a_partial_refresh_does_not_make_the_sends_ledger_skip_new_sends(tmp_path):
    profile = _seed(tmp_path)
    _refresh(tmp_path, profile, [_row("S1", 10), _row("S2", 10)], 1)
    monday, _ = _plan(_file(profile, tmp_path), [])
    assert {r["ref"] for r in monday} == {"S1", "S2"}
    _refresh(tmp_path, profile, [_row("S2", 15)], 0)
    doc = _file(profile, tmp_path)
    assert doc["fetched"] == doc["stamps"]["S2"] != doc["stamps"]["S1"]
    tuesday, notes = _plan(doc, monday)
    assert [(r["ref"], r["value"]) for r in tuesday] == [("S2", 5)], notes


def test_a_fallen_counter_writes_no_sends_row(tmp_path):
    profile = _seed(tmp_path)
    _refresh(tmp_path, profile, [_row("S1", 10)], 1)
    monday, _ = _plan(_file(profile, tmp_path), [])
    _refresh(tmp_path, profile, [_row("S1", 8)], 0)
    rows, notes = _plan(_file(profile, tmp_path), monday)
    assert rows == [] and any("no new sends" in n for n in notes)


def test_a_file_with_no_dates_at_all_names_the_sequences_and_says_they_carry_no_date(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, [{"id": "S1", "sent": 3}])
    m, page = _page(tmp_path, profile)
    assert m["figures"]["state"] == "undated"
    assert "S1: figures carry no date" in page


def test_a_date_that_cannot_be_read_is_worded_as_that_and_not_as_missing(tmp_path):
    profile = _seed(tmp_path)
    _refresh(tmp_path, profile, [_row("S1")], 0.1)
    path = ss.stats_path(profile, tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["stamps"]["S1"] = "not-a-date"
    from gtm_core.sequence_snapshot_format import body_digest

    doc["body_sha256"] = body_digest(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    m, page = _page(tmp_path, profile)
    assert "S1: date cannot be read" in page


@pytest.mark.parametrize("top", ["garbage", "future"])
def test_a_whole_file_with_a_bad_date_does_not_list_every_sequence_as_the_problem(tmp_path, top):
    profile = _seed(tmp_path)
    fetched = "garbage" if top == "garbage" else _at(-3)
    _stats(tmp_path, profile, {"fetched": fetched, "sequences": [{"id": "S1", "sent": 3}]})
    m, page = _page(tmp_path, profile)
    assert m["figures"]["state"] == ("undated" if top == "garbage" else "future")
    assert m["figures"]["cause_ids"] == [] and "S1:" not in _header(page)


def test_a_scoped_page_with_no_figures_file_says_nothing_about_old_figures(tmp_path):
    profile = _seed(tmp_path)
    ss.stats_path(profile, tmp_path).unlink()
    assert gd.build_model(profile, tmp_path)["figures"]["state"] == "none"
    c1 = _scoped(tmp_path, profile, "c1")
    assert c1["figures"]["state"] == "none" and "figures-old" not in c1["warnings"]


# --- ids and absent pieces, round 2 (2026-10-02) -----------------------------------------------


def test_fall_note_reads_a_model_with_no_snapshot_as_no_note():
    """`status.snapshot` present but `None` (a hand-built model, a fixture) used to raise
    AttributeError; every other reader of the snapshot is defensive and so is this one."""
    from gtm_core.email_campaign_dashboard import figure_ages

    for m in (
        {},
        {"status": None},
        {"status": {}},
        {"status": {"snapshot": None}},
        {"status": {"snapshot": {}}},
        {"status": {"snapshot": {"file_meta": None}}},
        {"status": {"snapshot": {"file_meta": {"falls": None}}}},
    ):
        assert figure_ages.fall_note(m, "S1") == "", m


def test_fall_note_still_finds_a_recorded_fall():
    from gtm_core.email_campaign_dashboard import figure_ages

    fall = {"on": "2026-09-30", "changes": [{"field": "sent", "was": 40, "now": 31}]}
    m = {"status": {"snapshot": {"file_meta": {"falls": {"S1": fall}}}}}
    assert "sent is 31 now, was 40" in figure_ages.fall_note(m, "S1")
    assert figure_ages.fall_note(m, "S2") == ""


def test_a_stamped_integer_id_is_found_under_the_string_a_campaign_lists(tmp_path):
    """The loader reads a row's id as the writer's own `row_id` string, so `7` on disk is `"7"`
    everywhere: the stamp is found, the campaign that lists `"7"` is covered, and the age is the
    row's own — not "no figures on file" for a sequence that is plainly there."""
    profile = _seed(tmp_path)
    _campaign(tmp_path, profile, "c7", "Campaign Seven", ["7"])
    now = _at(0.1)
    rows = [{"id": "S1", "sent": 1}, {"sequenceId": 7, "sent": 2}]
    _stats(tmp_path, profile, {"fetched": now, "sequences": rows}, stamped=True)
    m, _page_html = _page(tmp_path, profile)
    assert m["figures"]["state"] == "dated", m["figures"]
    assert m["figures"]["cause"] is None and m["figures"]["cause_ids"] == []
    assert not m["status"]["snapshot"]["file_meta"]["edited"]


def test_per_id_sorts_ids_of_mixed_types_without_raising():
    """A campaign manifest can carry a numeric id beside a text one; the age's id lists are
    compared as text, so neither the member table nor the cause list raises."""
    from gtm_core.email_campaign_dashboard import figure_ages

    snap = {
        "source": "stats",
        "unreadable": False,
        "file_meta": {
            "ids": ["S1"],
            "stamps": {"S1": _at(0.1)},
            "inherited": [],
            "edited": False,
        },
    }
    campaigns = {
        "campaigns": [
            {
                "sequences": [{"sequence_id": 7}, {"sequence_id": "S1"}, {"sequence_id": 3.5}],
                "archived": [],
            }
        ]
    }
    got = figure_ages.with_effective_age(snap, campaigns, datetime.now(UTC))
    assert got["age_cause"] == "missing"
    assert got["age_ids"] == sorted(got["age_ids"]) == ["3.5", "7"]


def test_an_archived_numeric_id_still_meets_its_snapshot_row_and_does_not_set_the_age():
    """A manifest that spells an archived sequence's id as a number must still be recognised as
    archived when the snapshot holds it as text. Otherwise the old run's stale stamp is read as a
    sequence in use that no campaign lists, and drags the whole page's age."""
    from gtm_core.email_campaign_dashboard import figure_ages

    fresh, stale = _at(0.1), _at(30)
    snap = {
        "source": "stats",
        "unreadable": False,
        "file_meta": {
            "ids": ["S1", "7"],
            "stamps": {"S1": fresh, "7": stale},
            "inherited": [],
            "edited": False,
        },
    }
    campaigns = {
        "campaigns": [{"sequences": [{"sequence_id": "S1"}], "archived": [{"sequence_id": 7}]}]
    }
    got = figure_ages.with_effective_age(snap, campaigns, datetime.now(UTC))
    assert got["fetched"] == fresh and got["age_cause"] is None
    assert got["age_basis"]["count"] == 1

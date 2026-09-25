"""PS20 Phase 2 — one forecast arithmetic for Operator notes and Results, and prose that claims
only what that arithmetic shows (PRD out-of-scope Rule-B list: forecast.py)."""

import re

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import forecast
from tests.contracts.dashboard_page import visible_text
from tests.test_email_campaign_dashboard import _seed_operational


def _text(m):
    """The forecast as a reader sees it. The f-strings wrap sentences across source lines
    ("…not\n        even they are"), so a raw-HTML substring check fails on today's text."""
    return visible_text(forecast._forecast_block(m))


def _cap(tmp_path, profile, cap):
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    text = camp.read_text(encoding="utf-8")
    camp.write_text(text.replace("daily_cap = 90", f"daily_cap = {cap}"), encoding="utf-8")


def _hand_built(sequences, packs=3):
    """Sequence lanes plus a lane of 1:1 packs sent by hand (the shape of test_email_campaign_
    dashboard.py:1404-1427), with a sending window so the forecast renders."""
    seqs = [{"sequence_id": s, "enrolled": 4} for s in sequences]
    window = {"daily_cap": 90, "mailboxes": 9, "touches": 2}
    return {
        "messages": [
            {"sequence_id": s, "spec": f"{s}.md", "copy": [{"day": 0}, {"day": 5}], "lint": {}}
            for s in sequences
        ],
        "campaigns": {
            "campaigns": [{"sequences": seqs, "targets": {"prospects": 4}, "window": window}]
        },
        "samples": {
            "rendered": [],
            "touches": [],
            "packs": [{"to": f"p{i}@x.example", "mail_days": [2, 7]} for i in range(packs)],
        },
    }


def _clocked(m, stamp="2026-09-25 09:00 UTC"):
    """Pin the page clock the way the model sets it (`generated_at`, UTC). 25 Sep 2026 is a
    Friday, so the first sending day is Monday 28 Sep."""
    m["generated_at"] = stamp
    return m


def test_when_done_is_the_forecasts_own_arithmetic(tmp_path):
    m = _clocked(gd.build_model(_seed_operational(tmp_path), tmp_path))  # 3 x 3 emails, 90/day
    sentence, why = forecast.when_done(m)
    assert why is None and "7 working days" in sentence  # the table's "1 + 6"
    assert "7 working days" in _text(m)
    done_by = re.findall(
        r"<td class='muted'>(?:<strong>)?(\d{1,2} [A-Z][a-z]{2})", forecast._forecast_block(m)
    )
    assert done_by[-1] == "6 Oct" and f"by {done_by[-1]} " in sentence
    assert "Done by" not in sentence


def test_a_weekend_clock_counts_from_the_next_working_day(tmp_path):
    """Sat and Sun read "starts on the next working day" as Monday = day 1, exactly as Friday
    does. Moving the start to Monday before counting made Monday day 0: a day late."""
    for stamp in ("2026-09-25 23:00 UTC", "2026-09-26 09:00 UTC", "2026-09-27 09:00 UTC"):
        m = _clocked(gd.build_model(_seed_operational(tmp_path), tmp_path), stamp)
        assert forecast._schedule(m)["finish"] == "6 Oct", stamp
        assert "by 6 Oct " in forecast.when_done(m)[0], stamp
    # A clock far from the day the suite runs, so reading `date.today()` cannot pass by luck.
    far = _clocked(gd.build_model(_seed_operational(tmp_path), tmp_path), "2020-02-29 09:00 UTC")
    assert forecast._schedule(far)["finish"] == "10 Mar"  # Sat -> Mon 2 Mar is day 1


def test_one_working_day_is_singular():
    """A one-touch lane has no follow-up arc: 4 emails at 90 a day is one day, not "1 days"."""
    m = _hand_built(["S1"], packs=0)
    m["messages"][0]["copy"] = [{"day": 0}]
    sentence, _ = forecast.when_done(m)
    text = _text(m)
    assert sentence.endswith("— 1 working day.")
    assert "takes 1 working day," in text and "take 1 working day to send" in text
    assert "1 working day" in visible_text(forecast._schedule(m)["rows"])
    assert "1 working days" not in text + sentence


def test_when_done_refuses_with_the_forecasts_own_reason(tmp_path):
    from tests.contracts.test_dashboard_aggregation_refusal import BASE
    from tests.contracts.test_dashboard_aggregation_refusal import _seed as _two

    _two(tmp_path, second=BASE.replace("touches = 2", "touches = 3"))
    sentence, why = forecast.when_done(gd.build_model("acme", tmp_path))
    assert sentence is None and "cadences" in why


def test_the_arc_sets_the_date_only_when_sending_is_shorter_than_the_arc(tmp_path):
    profile = _seed_operational(tmp_path)
    loose = _text(gd.build_model(profile, tmp_path))
    assert "The follow-up arc sets the finish date" in loose and "fit in 1 sending day " in loose
    assert "not even they are" in loose
    _cap(tmp_path, profile, 1)  # 9 emails at 1 a day: 9 sending days, longer than the 6-day tail
    tight = _text(gd.build_model(profile, tmp_path))
    assert "The follow-up arc sets the finish date" not in tight
    assert "The mailboxes set the finish date" in tight and "9 working days" in tight
    assert "not even they are" not in tight


def test_it_never_says_the_run_cannot_start_yet(tmp_path):
    assert "which it cannot yet" not in _text(gd.build_model(_seed_operational(tmp_path), tmp_path))


def test_the_hand_send_note_counts_the_rows_that_pace_themselves():
    one = _text(_hand_built(["S1"]))
    assert "The first row paces itself." in one and "The other 1 row is a hand send" in one
    assert "The first 2 rows pace themselves." in _text(_hand_built(["S1", "S2"]))
    assert "hand send" not in _text(_hand_built(["S1"], packs=0))
    # No sequence at all: every row is a hand send, so there is nothing to contrast — and
    # never "The first 0 rows pace themselves". Two hand lanes, so `len(lanes) > 1` is wrong.
    packs_only = _hand_built([])
    assert "hand send" not in _text(packs_only) and "pace" not in _text(packs_only)
    two_hand = _hand_built([])
    two_hand["samples"]["rendered"] = [{"spec": "r", "to": "r0@x.example"}]
    two_hand["samples"]["touches"] = [{"spec": "r", "day": 0}, {"spec": "r", "day": 3}]
    assert len(forecast._schedule(two_hand)["lanes"]) == 2
    assert "hand send" not in _text(two_hand) and "The first 0 rows" not in _text(two_hand)


def test_the_arc_sets_the_date_when_sending_exactly_matches_the_tail():
    """6 emails at 1 a day is 6 sending days; an 8-day ladder is a 6-working-day tail
    (ceil(8 x 5 / 7)). A tie is the arc's: the last enrollee's ladder still has to run."""
    m = _hand_built(["S1"], packs=0)
    m["messages"][0]["copy"] = [{"day": 0}, {"day": 8}]
    m["campaigns"]["campaigns"][0]["targets"]["prospects"] = 3
    m["campaigns"]["campaigns"][0]["window"]["daily_cap"] = 1
    s = forecast._schedule(m)
    assert (s["send_days"], s["tail"]) == (6, 6)
    assert "The follow-up arc sets the finish date" in _text(m)


def test_when_done_names_which_input_is_missing():
    """`_schedule` returns the REASON it has nothing to date, so `when_done` can say which.
    Both branches were one `{}` and so one sentence."""
    nocap = _hand_built(["S1"])
    del nocap["campaigns"]["campaigns"][0]["window"]["daily_cap"]
    nolanes = _hand_built([], packs=0)
    assert forecast.when_done(nocap) == (None, "no sending ceiling is declared")
    assert forecast.when_done(nolanes) == (None, "nothing is loaded to send yet")
    assert forecast._forecast_block(nocap) == "" == forecast._forecast_block(nolanes)
    neither = _hand_built([], packs=0)
    del neither["campaigns"]["campaigns"][0]["window"]["daily_cap"]
    assert forecast.when_done(neither) == (None, "no sending ceiling is declared")


def test_one_unloaded_person_is_singular():
    m = _hand_built(["S1"])
    m["campaigns"]["campaigns"][0]["targets"]["prospects"] = 1
    m["samples"]["rendered"] = [{"spec": "other", "to": f"r{i}@x.example"} for i in range(2)]
    assert "1 more person has a drafted email that nothing will send." in _text(m)

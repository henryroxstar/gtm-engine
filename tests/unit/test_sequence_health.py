"""The stop-line watcher: it recommends a pause, and only recommends.

Fictional data only (§R9). Payload SHAPES are copied from live replies: ``get_sequence_stats`` (the
full ``prospects[0]`` / ``emails.status`` key set) and ``get_consolidated_stats`` (the 25-key row,
``Unsubscribed`` / ``Bounced`` / ``Replied`` as the words Yes/No, ``Email Sent At`` as
``Sun Sep 27 2026 22:10:37 GMT-4 (America/New_York)``). The incident test reproduces the shape the
2026-10-02 post-mortem measured: step 1 sent to 232 people with 4 unsubscribing (1.7%, under the
5% line) and step 2 sent to 156 with 45 unsubscribing (29%).
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gtm_core import sequence_health as sh
from gtm_core.sequencer_stats_read import parse_sent_at

PROFILE = "democo"
SEQ, OTHER = "Sa1Bb2Cc3D", "Dd4Ee5Ff6G"  # fictional, provider-shaped ids
ET = timezone(timedelta(hours=-4))
_PROSPECT_KEYS = (
    "total open replied clicked unsubscribed contacted notContacted completed active upcoming "
    "inActive waiting bounced paused finished uncategorized interested notInterested meetingBooked "
    "outOfOffice closed notNow doNotContact interestedDealValue meetingBookedDealValue "
    "closedDealValue sentNoOpen openedNoReply openedNoClicked clickedNoReply"
).split()
_STATUS_KEYS = (
    "delivered opened clicked replied failed blockBounced hardBounced softBounced bounced scheduled"
).split()


def stats_reply(
    sid, *, name="Combined list", contacted, unsub, delivered, bounced=0, block=0, hard=0, replied=0
):
    """A real-shaped ``get_sequence_stats`` reply. ``bounced`` is the aggregate, as in live replies."""
    prospects = dict.fromkeys(_PROSPECT_KEYS, "0")
    prospects.update(total=str(contacted), contacted=str(contacted), unsubscribed=str(unsub))
    status = dict.fromkeys(_STATUS_KEYS, 0)
    status.update(
        delivered=delivered, bounced=bounced, blockBounced=block, hardBounced=hard, replied=replied
    )
    return {
        "message": "Sequence stats fetched successfully",
        "payload": {
            "prospects": [prospects],
            "emails": {"total": delivered + bounced, "status": status},
            "sequenceName": name,
            "sequenceId": sid,
            "client": {},
        },
    }


def stamp(when: datetime) -> str:
    return when.strftime("%a %b %d %Y %H:%M:%S") + " GMT-4 (America/New_York)"


def email_row(
    sid, step, n, when, *, unsub=False, bounced=False, replied=False, sender="box1@sender.example"
):
    """A real-shaped ``get_consolidated_stats`` row (25 keys)."""
    yn = lambda flag: "Yes" if flag else "No"  # noqa: E731
    return {
        "Sequence Title": "Combined list",
        "Sequence Owner": "Owner Name",
        "Step Id": f"step-{step}",
        "Step Number": step,
        "Sender Email": sender,
        "Recipient Email": f"person{n}@contact{n % 7}.example",
        "Recipient name": f"Person {n}",
        "Email Sent At": stamp(when),
        "Open Count": 0,
        "Last Opened At": "",
        "Replied": yn(replied),
        "Replied At": "",
        "Conversation Id": "",
        "Current Sentiment": "",
        "Click Count": 0,
        "Last Clicked At": "",
        "Last Link Clicked": "",
        "Bounced": yn(bounced),
        "Bounced At": "",
        "Unsubscribed": yn(unsub),
        "Deleted": "No",
        "Sequence Id": sid,
        "Variant": "A",
        "Variant Id": "v1",
        "Client": {},
    }


def step_rows(
    sid, step, count, unsub_at=(), *, start, offset=0, bounced_at=(), sender="box1@sender.example"
):
    """``count`` sends one minute apart from ``start``; the unsub/bounce positions are 0-based send order."""
    return [
        email_row(
            sid, step, offset + i, start + timedelta(minutes=i),
            unsub=i in unsub_at, bounced=i in bounced_at, sender=sender,
        )
        for i in range(count)
    ]  # fmt: skip


def page(rows, *, more=False):
    return {
        "message": "ok",
        "payload": {"data": rows, "hasMore": more, "nextPageNumber": 2 if more else None},
    }


D1 = datetime(2026, 9, 28, 10, 0, tzinfo=ET)
D2 = datetime(2026, 10, 1, 10, 0, tzinfo=ET)


class Home:
    def __init__(self, root: Path):
        self.root = root
        (root / PROFILE).mkdir()
        self.stats_dir = root / "stats"
        self.stats_dir.mkdir()
        self.settings = root / PROFILE / "settings.json"
        self.n = 0

    def stats(self, reply):
        self.n += 1
        (self.stats_dir / f"s{self.n}.json").write_text(json.dumps(reply))

    def consolidated(self, *pages) -> list[str]:
        paths = []
        for k, body in enumerate(pages):
            path = self.root / f"consolidated-{k}.json"
            path.write_text(json.dumps(body))
            paths.append(str(path))
        return paths

    def sequences(self, *rows) -> list[str]:
        path = self.root / "list.json"
        path.write_text(json.dumps({"message": "ok", "payload": [
            {"id": i, "title": t, "active": a, "steps": []} for i, t, a in rows]}))  # fmt: skip
        return ["--sequences", str(path)]

    def run(self, *flags):
        return sh.main(["--profile", PROFILE, "--stats-dir", str(self.stats_dir), *flags])


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    return Home(tmp_path)


def out_json(home, capsys, *flags):
    code = home.run("--json", *flags)
    return code, json.loads(capsys.readouterr().out)


def incident(home, *, step2_unsub_at=range(30, 75)):
    """Step 1: 232 sent / 4 unsubscribed. Step 2: 156 sent / 45 unsubscribed (29%)."""
    home.stats(stats_reply(SEQ, contacted=232, unsub=49, delivered=388, bounced=0))
    rows = step_rows(SEQ, 1, 232, unsub_at={50, 90, 140, 200}, start=D1)
    rows += step_rows(SEQ, 2, 156, unsub_at=set(step2_unsub_at), start=D2)
    return home.consolidated(page(rows))


# ── the incident ──────────────────────────────────────────────────────────────────────────────


def test_step_1_at_1_7_percent_does_not_breach_and_step_2_at_29_percent_does(home, capsys):
    paths = incident(home)
    code, out = out_json(home, capsys, "--consolidated", *paths)
    assert code == 2
    texts = out["lines"]
    assert (
        "Pause Combined list: 45 of 156 people unsubscribed after step 2 (29%); the line is 5%"
        in texts
    )
    assert not any("after step 1" in t for t in texts)  # 4 of 232 = 1.7%: under the line
    step1 = next(s for s in out["sequences"][0]["steps"] if s["step"] == 1)
    assert (step1["unsubscribed"], step1["sent"]) == (4, 232)
    # The whole-sequence rate is over the line too, and says so in its own words.
    assert any("49 of 232 people unsubscribed so far (21%)" in t for t in texts)


def test_text_and_json_say_the_same_thing(home, capsys):
    paths = incident(home)
    home.run("--consolidated", *paths)
    text = capsys.readouterr().out
    _, out = out_json(home, capsys, "--consolidated", *paths)
    for line in out["lines"]:
        assert line in text
    assert f"{out['breach_count']} breach(es) needing action" in text


# ── the line itself: strictly over, with a floor ──────────────────────────────────────────────


def test_exactly_at_the_line_is_not_over_it_and_one_more_is(home, capsys):
    home.stats(stats_reply(SEQ, contacted=20, unsub=1, delivered=20))
    assert home.run() == 0  # 1 of 20 is exactly 5%
    capsys.readouterr()
    (home.stats_dir / "s1.json").unlink()
    home.stats(stats_reply(SEQ, contacted=20, unsub=2, delivered=20))
    assert home.run() == 2  # 2 of 20 is 10%
    assert "2 of 20 people unsubscribed so far (10%); the line is 5%" in capsys.readouterr().out


def test_below_the_sample_floor_a_rate_line_does_not_apply(home, capsys):
    home.stats(stats_reply(SEQ, contacted=19, unsub=2, delivered=15, bounced=3, block=3))  # 18 sent
    assert home.run() == 0
    assert "OK: no sequence is over a stop line." in capsys.readouterr().out


def test_documented_defaults():
    assert sh.DEFAULT_LINES == {
        "unsubscribe_stop_line": 0.05,
        "bounce_stop_line": 0.05,
        "block_bounce_stop_line": 0.02,
    }
    assert (sh.MIN_SAMPLE, sh.EARLY_COUNT, sh.EARLY_WINDOW) == (20, 3, 20)


# ── the early warning: three in the first twenty, whatever has gone so far ────────────────────


def test_three_unsubscribes_in_the_first_twenty_sent_is_an_early_warning(home, capsys):
    home.stats(stats_reply(SEQ, contacted=156, unsub=3, delivered=156))
    rows = step_rows(SEQ, 2, 156, unsub_at={4, 9, 15}, start=D2)
    code, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(rows)))
    assert code == 2
    assert (
        "Pause Combined list: 3 of the first 20 people emailed at step 2 unsubscribed; 3 or more is the early warning"
        in out["lines"]
    )
    assert [b["code"] for b in out["breaches"]] == [
        "step-early-warning"
    ]  # 3 of 156 is under the rate line


def test_the_early_warning_has_no_sample_floor(home, capsys):
    home.stats(stats_reply(SEQ, contacted=8, unsub=3, delivered=8))
    rows = step_rows(SEQ, 2, 8, unsub_at={0, 1, 2}, start=D2)
    code, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(rows)))
    assert code == 2 and "3 of the first 8 people emailed at step 2" in out["lines"][0]


def test_the_window_is_the_first_twenty_SENT_not_the_first_twenty_listed(home, capsys):
    """Three unsubscribes among the last-sent rows listed first must not trip the warning."""
    home.stats(stats_reply(SEQ, contacted=60, unsub=3, delivered=60))
    rows = step_rows(SEQ, 2, 60, unsub_at={57, 58, 59}, start=D2)
    rows.reverse()  # the page lists newest first
    code, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(rows)))
    assert code == 0 and out["breaches"] == []  # 3 of 60 is exactly 5%: not over, and not early


def test_the_window_edge_is_exactly_the_twentieth_send(home, capsys):
    home.stats(stats_reply(SEQ, contacted=60, unsub=3, delivered=60))
    inside = step_rows(SEQ, 2, 60, unsub_at={17, 18, 19}, start=D2)  # sends 18, 19 and 20
    code, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(inside)))
    assert code == 2 and [b["code"] for b in out["breaches"]] == ["step-early-warning"]
    just_out = step_rows(SEQ, 2, 60, unsub_at={17, 18, 20}, start=D2)  # the third is send 21
    code, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(just_out)))
    assert code == 0 and out["breaches"] == []


def test_a_second_send_to_the_same_person_at_the_same_step_is_kept(home, capsys):
    """Only an exact repeat (page overlap) collapses; a re-send at another time is another email."""
    home.stats(stats_reply(SEQ, contacted=20, unsub=0, delivered=21))
    rows = step_rows(SEQ, 1, 20, start=D1)
    resend = dict(rows[0], **{"Email Sent At": stamp(D1 + timedelta(days=1))})
    _, out = out_json(home, capsys, "--consolidated", *home.consolidated(page([*rows, resend])))
    assert out["sequences"][0]["steps"][0]["sent"] == 21


def test_step_figures_takes_the_first_twenty_sent_whatever_order_it_is_handed():
    """Not only the CLI path: the function itself must not trust the order of its input."""
    from gtm_core.sequencer_stats_read import SentEmail

    emails = [
        SentEmail(
            SEQ,
            2,
            f"p{i}@c.example",
            "",
            D2 + timedelta(minutes=i),
            i in (57, 58, 59),
            False,
            False,
        )
        for i in range(60)
    ]
    (late,) = sh.step_figures(list(reversed(emails)))[SEQ]
    assert late.early_sent == 20 and late.early_unsubscribed == 0 and late.unsubscribed == 3


def test_unsubscribes_just_past_the_window_are_not_early(home, capsys):
    home.stats(stats_reply(SEQ, contacted=60, unsub=3, delivered=60))
    rows = step_rows(SEQ, 2, 60, unsub_at={20, 21, 22}, start=D2)  # sends 21-23
    code, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(rows)))
    assert code == 0 and out["breaches"] == []


# ── bounces ───────────────────────────────────────────────────────────────────────────────────


def test_bounce_and_block_bounce_lines_use_the_aggregate_not_the_sum_of_all_four(home, capsys):
    """Live: bounced 9 = blockBounced 8 + hardBounced 1. Adding all four would read 18."""
    home.stats(stats_reply(SEQ, contacted=58, unsub=0, delivered=98, bounced=9, block=8, hard=1))
    code, out = out_json(home, capsys)
    assert code == 2
    assert "Pause Combined list: 9 of 107 emails bounced (8.4%); the line is 5%" in out["lines"]
    assert any(
        "8 of 107 emails were refused outright by the receiving server (7.5%); the line is 2%" in t
        for t in out["lines"]
    )
    assert out["sequences"][0]["sent"] == 107 and out["sequences"][0]["bounced"] == 9


def test_block_bounce_has_its_own_tighter_line(home, capsys):
    home.stats(stats_reply(SEQ, contacted=50, unsub=0, delivered=95, bounced=5, block=3, hard=2))
    _, out = out_json(home, capsys)  # 5 of 100 = 5%: not over; 3 of 100 = 3% block: over 2%
    assert [b["code"] for b in out["breaches"]] == ["block-bounce-rate"]


def test_per_step_bounces_from_consolidated_rows(home, capsys):
    home.stats(stats_reply(SEQ, contacted=100, unsub=0, delivered=94, bounced=6, block=1, hard=5))
    rows = step_rows(SEQ, 1, 100, bounced_at=set(range(0, 100, 17)), start=D1)  # 6 bounced of 100
    _, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(rows)))
    assert any(
        "6 of 100 emails bounced at step 1 (6.0%); the line is 5%" in t for t in out["lines"]
    )


def test_replies_are_computed_per_sequence_and_step(home, capsys):
    home.stats(stats_reply(SEQ, contacted=30, unsub=0, delivered=30, replied=2))
    rows = step_rows(SEQ, 1, 30, start=D1)
    rows[3]["Replied"] = rows[4]["Replied"] = "Yes"
    _, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(rows)))
    assert out["sequences"][0]["replied"] == 2 and out["sequences"][0]["steps"][0]["replied"] == 2


# ── settings ──────────────────────────────────────────────────────────────────────────────────


def test_settings_override_a_default(home, capsys):
    home.settings.write_text(json.dumps({"unsubscribe_stop_line": 0.5}))
    paths = incident(home)
    code, out = out_json(home, capsys, "--consolidated", *paths)
    assert out["stop_lines"]["unsubscribe_stop_line"] == 0.5
    assert not any("people unsubscribed" in t for t in out["lines"] if "early" not in t)
    assert out["stop_lines"]["bounce_stop_line"] == 0.05  # untouched keys keep their default


@pytest.mark.parametrize("bad", [5, 0, -0.1, "0.05", True, None, 1.5])
def test_a_stop_line_that_is_not_a_fraction_stops_the_run(home, capsys, bad):
    home.settings.write_text(json.dumps({"bounce_stop_line": bad}))
    home.stats(stats_reply(SEQ, contacted=30, unsub=0, delivered=30))
    assert home.run() == 3 and "bounce_stop_line" in capsys.readouterr().err


def test_unreadable_settings_stop_the_run(home, capsys):
    home.settings.write_text("{not json")
    home.stats(stats_reply(SEQ, contacted=30, unsub=0, delivered=30))
    assert home.run() == 3 and "settings.json is not readable JSON" in capsys.readouterr().err


# ── mailbox volume ────────────────────────────────────────────────────────────────────────────


def test_a_mailbox_over_its_daily_cap_is_a_breach_counted_in_its_own_day(home, capsys):
    home.settings.write_text(json.dumps({"mailbox_daily_cap": 10}))
    home.stats(stats_reply(SEQ, contacted=19, unsub=0, delivered=19))
    rows = step_rows(SEQ, 1, 19, start=D1)
    rows += step_rows(
        SEQ, 1, 8, start=D1 + timedelta(days=1), offset=100, sender="box2@sender.example"
    )
    code, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(rows)))
    assert code == 2
    assert (
        "Slow down: mailbox box1@sender.example sent 19 emails on 2026-09-28; its daily cap is 10"
        in out["lines"]
    )


def test_at_or_under_the_cap_is_quiet_and_a_cap_with_no_volume_is_a_note_not_a_pass(home, capsys):
    home.stats(stats_reply(SEQ, contacted=10, unsub=0, delivered=10))
    rows = step_rows(SEQ, 1, 10, start=D1)
    assert (
        home.run("--mailbox-daily-cap", "10", "--consolidated", *home.consolidated(page(rows))) == 0
    )
    capsys.readouterr()
    code, out = out_json(
        home, capsys, "--mailbox-daily-cap", "10"
    )  # no consolidated rows, no volume given
    assert code == 0 and any("no mailbox volume was available" in n for n in out["notes"])


def test_a_volume_flag_stands_in_for_the_rows_and_a_flag_beats_settings(home, capsys):
    home.settings.write_text(json.dumps({"mailbox_daily_cap": 100}))
    home.stats(stats_reply(SEQ, contacted=10, unsub=0, delivered=10))
    code, out = out_json(home, capsys, "--mailbox-daily-cap", "10", "--mailbox-daily-volume", "19")
    assert (
        code == 2
        and "Slow down: a mailbox sent 19 emails in a day; its daily cap is 10" in out["lines"]
    )


# ── already paused ────────────────────────────────────────────────────────────────────────────


def test_a_sequence_already_off_reads_already_paused_and_does_not_fail_the_run(home, capsys):
    paths = incident(home)
    code, out = out_json(
        home, capsys, "--consolidated", *paths, *home.sequences((SEQ, "Combined list", False))
    )
    assert code == 0 and out["breach_count"] == 0
    assert out["lines"] and all(t.startswith("Already paused Combined list:") for t in out["lines"])
    code, out = out_json(
        home, capsys, "--consolidated", *paths, *home.sequences((SEQ, "Combined list", True))
    )
    assert code == 2 and all(t.startswith("Pause ") for t in out["lines"])


# ── fail loudly, never a smaller answer ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("break_it", "message"),
    [
        (
            lambda r: r["payload"]["emails"]["status"].pop("delivered"),
            "emails.status.delivered is missing",
        ),
        (
            lambda r: r["payload"]["prospects"][0].pop("contacted"),
            "prospects[0].contacted is missing",
        ),
        (
            lambda r: r["payload"]["prospects"][0].pop("unsubscribed"),
            "prospects[0].unsubscribed is missing",
        ),
        (
            lambda r: [
                r["payload"]["emails"]["status"].pop(k)
                for k in ("bounced", "blockBounced", "hardBounced", "softBounced")
            ],
            "no bounce counts",
        ),
        (lambda r: r["payload"].pop("emails"), "`emails` is missing"),
    ],
)
def test_a_stats_reply_missing_a_number_a_rate_divides_by_is_refused(
    home, capsys, break_it, message
):
    reply = stats_reply(SEQ, contacted=30, unsub=0, delivered=30)
    break_it(reply)
    home.stats(reply)
    assert home.run() == 3 and message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("break_it", "message"),
    [
        (lambda r: r.pop("Step Number"), "Step Number"),
        (lambda r: r.update({"Step Number": 0}), "Step Number"),
        (lambda r: r.pop("Recipient Email"), "Recipient Email"),
        (lambda r: r.update({"Unsubscribed": "Maybe"}), "expected Yes or No"),
        (lambda r: r.pop("Bounced"), "expected Yes or No"),
        (lambda r: r.update({"Email Sent At": "yesterday"}), "not a date"),
        (lambda r: r.pop("Sequence Id"), "Sequence Id"),
    ],
)
def test_a_consolidated_row_missing_what_a_rate_needs_is_refused(home, capsys, break_it, message):
    home.stats(stats_reply(SEQ, contacted=30, unsub=0, delivered=30))
    rows = step_rows(SEQ, 1, 3, start=D1)
    break_it(rows[1])
    assert home.run("--consolidated", *home.consolidated(page(rows))) == 3
    assert message in capsys.readouterr().err


def test_a_page_set_where_every_page_says_more_is_coming_is_refused(home, capsys):
    home.stats(stats_reply(SEQ, contacted=30, unsub=0, delivered=30))
    rows = step_rows(SEQ, 1, 30, start=D1)
    assert home.run("--consolidated", *home.consolidated(page(rows, more=True))) == 3
    assert "every page says hasMore" in capsys.readouterr().err
    # Given the last page too, the set is accepted.
    assert home.run("--consolidated", *home.consolidated(page(rows, more=True), page([]))) == 0


def test_an_empty_consolidated_read_is_not_a_clean_read(home, capsys):
    home.stats(stats_reply(SEQ, contacted=30, unsub=0, delivered=30))
    assert home.run("--consolidated", *home.consolidated(page([]))) == 3
    assert "empty read is not a clean read" in capsys.readouterr().err


def test_page_overlap_repeats_a_row_exactly_and_is_not_double_counted(home, capsys):
    home.stats(stats_reply(SEQ, contacted=40, unsub=0, delivered=40))
    rows = step_rows(SEQ, 1, 40, start=D1)
    paths = home.consolidated(
        page(rows[:30], more=True), page(rows[10:])
    )  # rows 10-29 appear twice
    _, out = out_json(home, capsys, "--consolidated", *paths)
    assert out["sequences"][0]["steps"][0]["sent"] == 40


def test_a_sequence_with_sends_but_no_step_figures_is_noted_once_and_an_unsent_one_is_not(
    home, capsys
):
    home.stats(stats_reply(SEQ, contacted=30, unsub=0, delivered=30))
    home.stats(stats_reply(OTHER, contacted=0, unsub=0, delivered=0))
    _, out = out_json(home, capsys)
    (note,) = out["notes"]
    assert "1 sequence(s) that have sent mail" in note and "Combined list" in note


def test_a_usage_mistake_is_exit_3_never_the_exit_code_of_a_breach(home):
    with pytest.raises(SystemExit) as stop:
        sh.main(["--profile", PROFILE])
    assert stop.value.code == 3


def test_a_sequence_in_the_rows_but_not_in_the_stats_is_still_checked_per_step(home, capsys):
    home.stats(stats_reply(SEQ, contacted=30, unsub=0, delivered=30))
    rows = step_rows(OTHER, 2, 30, unsub_at=set(range(10, 25)), start=D2)
    code, out = out_json(home, capsys, "--consolidated", *home.consolidated(page(rows)))
    assert code == 2 and any(b["sequence_id"] == OTHER for b in out["breaches"])


# ── hostile text, timestamps, and the one thing this module must never do ─────────────────────


def test_a_title_cannot_forge_a_second_line(home, capsys):
    home.stats(
        stats_reply(
            SEQ, name="x\nOK: no sequence is over a stop line.", contacted=20, unsub=5, delivered=20
        )
    )
    assert home.run() == 2
    lines = capsys.readouterr().out.splitlines()
    assert not any(line.startswith("OK:") for line in lines)


def test_timestamps_keep_the_accounts_own_offset():
    got = parse_sent_at("Sun Sep 27 2026 22:10:37 GMT-4 (America/New_York)")
    assert got.utcoffset() == timedelta(hours=-4) and (got.month, got.day, got.hour) == (9, 27, 22)
    assert parse_sent_at("Mon Sep 28 2026 09:00:00 GMT+8").utcoffset() == timedelta(hours=8)
    assert parse_sent_at("2026-09-28T09:00:00Z").utcoffset() == timedelta(0)


def test_this_module_recommends_and_never_acts():
    """§R13: the ban lives in code. No pause/resume/activate call, no network, no subprocess."""
    banned_names = (
        "pause",
        "resume",
        "activate",
        "update_sequence_status",
        "add_leads",
        "import_prospects",
    )
    banned_imports = {
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "http",
        "aiohttp",
        "mcp",
    }
    for module in (sh, __import__("gtm_core.sequencer_stats_read", fromlist=["x"])):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not {n.split(".")[0] for n in names} & banned_imports, module.__name__
            if isinstance(node, ast.Call):
                target = node.func
                name = (
                    target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                )
                assert not any(b in name.lower() for b in banned_names), (module.__name__, name)


def test_a_run_writes_nothing(home, capsys):
    home.settings.write_text(json.dumps({"mailbox_daily_cap": 10}))
    paths = incident(home)
    before = sorted((p, p.read_bytes()) for p in home.root.rglob("*") if p.is_file())
    home.run("--consolidated", *paths)
    home.run("--json", "--consolidated", *paths)
    assert sorted((p, p.read_bytes()) for p in home.root.rglob("*") if p.is_file()) == before

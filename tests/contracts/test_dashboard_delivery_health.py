"""Delivery health on the status page (2026-10-06): unsubscribes and bounces as warnings, rates
beside counts in the sequence table, the totals added up by seat and argument, and the
contradictions that made the page disagree with itself.

The founding shape: 244 people contacted, 49 unsubscribed (20%), and nowhere on the page did
that read as a problem — the Insights tab said nothing had been sent, the opt-out line said 3,
and the per-sequence counts sat unsorted with no rates beside them.
"""

from __future__ import annotations

from gtm_core import email_campaign_dashboard as gd
from gtm_core.email_campaign_dashboard import delivery
from gtm_core.email_campaign_dashboard import views_overview as vo
from gtm_core.email_campaign_dashboard import views_results as vr
from gtm_core.email_campaign_dashboard.config import BOUNCE_RISK_PCT, UNSUB_RISK_PCT
from gtm_core.email_campaign_dashboard.insights_lenses import strategic_lenses_html
from tests.contracts.dashboard_page import block, visible_text
from tests.contracts.test_dashboard_ps20_trust import (
    _ago,
    _fixture_10_24_1,
    _stats,
    _strip,
    _tile_labelled,
)

#: The delivery strip's open tag; the page's other risk marks are pills, never a card.
STRIP = 'class="card risk"'


def _seq(sid, name, contacted, *, unsub=0, replied=0, delivered=None, bounced=0):
    """One sequence in the sending tool's own per-sequence payload shape."""
    p = {
        "sequenceId": sid,
        "sequenceName": name,
        "status": "paused",
        "prospects": [
            {"contacted": contacted, "replied": replied, "unsubscribed": unsub, "total": contacted}
        ],
    }
    if delivered is not None:
        p["emails"] = {"status": {"delivered": delivered, "bounced": bounced}}
    return p


def _model(tmp_path, *seqs):
    profile = _fixture_10_24_1(tmp_path)
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": list(seqs)})
    return gd.build_model(profile, tmp_path)


def _card(html, slug="c1"):
    return block(html, f'data-campaign="{slug}"')


# ── 1. the page-wide warning ─────────────────────────────────────────────────────────────


def test_high_unsubscribes_raise_a_strip_naming_where_they_came_from(tmp_path):
    m = _model(
        tmp_path,
        _seq("S1", "Alpha angle", 20, unsub=6),
        _seq("S2", "Beta angle", 30, unsub=1),
    )
    head = _strip(gd.render_html(m))
    assert '<div class="card risk" data-risk="unsub-rate">' in head
    text = visible_text(block(head, STRIP))
    assert "7 of 50 people contacted (14.0%) unsubscribed" in text
    assert f"under {UNSUB_RISK_PCT}%" in text
    assert "Alpha angle: 6 of 20 (30.0%)" in text


def test_healthy_rates_raise_no_delivery_strip(tmp_path):
    m = _model(
        tmp_path,
        _seq("S1", "Alpha angle", 50, unsub=1, delivered=100, bounced=1),
        _seq("S2", "Beta angle", 0),
    )
    # 1 of 50 is exactly the threshold: the strip fires strictly ABOVE it, like the bounce pill.
    assert STRIP not in _strip(gd.render_html(m))


def test_high_bounces_raise_the_strip_in_emails_not_people(tmp_path):
    m = _model(tmp_path, _seq("S1", "Alpha angle", 40, delivered=90, bounced=10))
    strip = block(_strip(gd.render_html(m)), STRIP)
    assert 'data-risk="bounce-rate"' in strip
    text = visible_text(strip)
    assert "10 of 100 emails (10.0%) bounced" in text
    assert f"{BOUNCE_RISK_PCT}%" in text


def test_an_unreadable_snapshot_raises_no_delivery_strip(tmp_path):
    profile = _fixture_10_24_1(tmp_path)
    _stats(tmp_path, profile, "{broken")
    assert STRIP not in _strip(gd.render_html(gd.build_model(profile, tmp_path)))


# ── 3. the per-sequence table: rates, worst first, unsent collapsed ──────────────────────


def test_the_table_shows_rates_and_flags_a_high_unsubscribe_rate(tmp_path):
    m = _model(
        tmp_path,
        _seq("S1", "Alpha angle", 20, unsub=6, replied=1),
        _seq("S2", "Beta angle", 30),
    )
    card = _card(vr._results_view(m))
    assert "<th class='num-cell'>Reply rate</th>" in card
    assert "<th class='num-cell'>Unsubscribe rate</th>" in card
    assert "data-risk='unsub-rate'>30.0%</span>" in card
    assert "5.0%" in card  # Alpha's reply rate, 1 of 20
    assert "data-risk='unsub-rate'>0.0%" not in card


def test_the_table_lists_the_worst_unsubscribe_rate_first(tmp_path):
    m = _model(
        tmp_path,
        _seq("S1", "Alpha angle", 50, unsub=1),
        _seq("S2", "Beta angle", 10, unsub=4),
    )
    card = _card(vr._results_view(m))
    assert card.index("Beta angle") < card.index("Alpha angle")


def test_sequences_that_have_contacted_nobody_collapse_into_one_line(tmp_path):
    m = _model(tmp_path, _seq("S1", "Alpha angle", 20), _seq("S2", "Beta angle", 0))
    card = _card(vr._results_view(m))
    start = card.index("<table><thead><tr><th>Sequence</th>")
    table = card[start : card.index("</table>", start)]
    assert "Alpha angle" in table and "Beta angle" not in table
    unsent = block(card, "data-unsent")
    assert "not sent yet" in visible_text(unsent) and "Beta angle" in unsent


# ── 4. added up by seat and by argument ──────────────────────────────────────────────────


def _hand_model(seqs, messages):
    return {"campaigns": {"campaigns": [{"slug": "c1", "sequences": seqs}]}, "messages": messages}


def _live(sid, title, sent, *, unsub=0, replied=0, bounced=None, delivered=None):
    live = {"sent": sent, "replied": replied, "unsubscribed": unsub}
    if bounced is not None:
        live |= {"bounced": bounced, "delivered": delivered, "bounce_source": "emails"}
    return {"sequence_id": sid, "title": title, "live": live}


def _msg(sid, seats, premise=""):
    return {
        "sequence_id": sid,
        "audience": [{"seat": s} for s in seats],
        "portfolio": {"premise": premise},
    }


def test_rollup_adds_sequences_up_by_seat_and_names_mixed_and_missing():
    m = _hand_model(
        [
            _live("A", "Lane · First angle · CEO", 100, unsub=10, replied=1),
            _live("B", "Lane · Second angle · CEO", 50, unsub=5),
            _live("C", "Lane · Third angle · Mixed", 20, unsub=1),
            _live("D", "Lane · Fourth angle · ?", 10),
            _live("E", "Lane · Fifth angle · CTO", 0),  # contacted nobody: not added up
        ],
        [
            _msg("A", ["ceo"], "seat-remit"),
            _msg("B", ["ceo", "ceo"], "seat-remit"),
            _msg("C", ["ceo", "cto"], "regulated-entity"),
            _msg("E", ["cto"], "seat-remit"),
        ],
    )
    by_seat = {g["label"]: g for g in delivery.rollup(delivery.sequence_rows(m), "seat")}
    assert set(by_seat) == {"Ceo", "Mixed seats", "Not recorded"}
    assert (by_seat["Ceo"]["sequences"], by_seat["Ceo"]["contacted"]) == (2, 150)
    assert (by_seat["Ceo"]["unsubscribed"], by_seat["Ceo"]["replied"]) == (15, 1)
    by_arg = {
        g["label"]: g["contacted"] for g in delivery.rollup(delivery.sequence_rows(m), "argument")
    }
    assert by_arg == {"Seat remit": 150, "Regulated entity": 20, "Not recorded": 10}


def test_a_sequence_two_campaigns_list_is_added_up_once():
    seq = _live("A", "Lane · First angle · CEO", 100, unsub=10)
    m = {
        "campaigns": {
            "campaigns": [{"slug": "c1", "sequences": [seq]}, {"slug": "c2", "sequences": [seq]}]
        },
        "messages": [_msg("A", ["ceo"])],
    }
    (only,) = delivery.rollup(delivery.sequence_rows(m), "seat")
    assert only["contacted"] == 100


def test_rollup_card_says_why_segment_is_not_added_up_and_renders_nothing_before_sending():
    m = _hand_model([_live("A", "Lane · First angle · CEO", 40, unsub=4)], [_msg("A", ["ceo"])])
    html = delivery.rollup_html(m)
    assert "data-rollup" in html and "Segment is not added up" in visible_text(html)
    assert "data-risk='unsub-rate'>10.0%" in html
    assert delivery.rollup_html(_hand_model([_live("A", "x", 0)], [])) == ""


def test_a_group_bounce_rate_needs_every_sequence_to_report_bounces():
    m = _hand_model(
        [
            _live("A", "Lane · First · CEO", 40, bounced=1, delivered=99),
            _live("B", "Lane · Second · CEO", 40),  # no per-email status block
        ],
        [_msg("A", ["ceo"]), _msg("B", ["ceo"])],
    )
    html = delivery.rollup_html(m)
    assert "not available" in html and "1.0%" not in html


def test_the_rollup_renders_on_the_results_tab_once_anyone_is_contacted(tmp_path):
    m = _model(tmp_path, _seq("S1", "Alpha angle", 20, unsub=1), _seq("S2", "Beta angle", 0))
    assert "data-rollup" in vr._results_view(m)


# ── 2. the contradictions ────────────────────────────────────────────────────────────────


def test_replies_by_seat_reads_the_sending_figures_not_an_empty_ledger():
    m = _hand_model([_live("A", "Lane · First angle · CEO", 139, replied=1)], [_msg("A", ["ceo"])])
    m["cells"] = {"cells": [], "baseline": 0.05}  # the outcomes ledger has recorded nothing
    html = strategic_lenses_html(m)
    seat = html[html.index("Replies by Seat") : html.index("Why-Now Coverage")]
    assert "nothing in this run has been sent" not in seat
    assert "<td>Ceo</td><td class='num-cell'>139</td>" in seat
    hook = html[html.index("Replies by Hook") :]
    assert "<td>First angle</td><td class='num-cell'>139</td>" in hook


def _with_optouts(tmp_path, events, *, unsub=6):
    from gtm_core.prospects_import import _ledgers

    profile = _fixture_10_24_1(tmp_path)
    _stats(
        tmp_path,
        profile,
        {"fetched": _ago(0), "sequences": [_seq("S1", "Alpha angle", 20, unsub=unsub)]},
    )
    for ev in events:
        _ledgers(profile, tmp_path).append_history({"ts": "2026-09-25", **ev})
    return visible_text(_card(vr._results_view(gd.build_model(profile, tmp_path))))


def _reply(email):
    return {"event": "optout_detected", "email": email}


def _link(email, seq="S1"):
    return {
        "event": "optout_detected",
        "email": email,
        "skill": "sequencer-unsubscribe",
        "source": "sequencer_unsubscribe",
        "sequence_id": seq,
    }


def test_reply_optouts_alone_keep_the_original_sentence(tmp_path):
    text = _with_optouts(tmp_path, [_reply("ada@acme.example")], unsub=0)
    assert "1 person asked not to be contacted; 0 of 1 on the do-not-contact list" in text
    assert "unsubscribe link" not in text


def test_the_line_says_how_many_came_by_reply_and_how_many_by_the_unsubscribe_link(tmp_path):
    events = [_reply("ada@acme.example"), _link("b@acme.example"), _link("c@acme.example")]
    text = _with_optouts(tmp_path, events, unsub=2)
    assert "3 people asked not to be contacted" in text
    assert "1 by reply, 2 by unsubscribe link" in text


def test_unsubscribes_the_sending_tool_counts_but_the_ledger_does_not_are_flagged_as_unrecorded(
    tmp_path,
):
    text = _with_optouts(tmp_path, [_link("b@acme.example")], unsub=6)
    assert "The sending tool counts 6 people as unsubscribed from this campaign" in text
    assert "5 are not recorded as opt-outs yet" in text


def test_nothing_is_flagged_once_every_unsubscribe_is_recorded(tmp_path):
    events = [_link(f"p{i}@acme.example") for i in range(6)]
    assert "not recorded as opt-outs" not in _with_optouts(tmp_path, events, unsub=6)


def test_unsubscribes_with_no_opt_out_rows_at_all_are_flagged_with_nothing_recorded(tmp_path):
    text = _with_optouts(tmp_path, [], unsub=6)
    assert "asked not to be contacted" not in text
    assert "The sending tool counts 6 people as unsubscribed from this campaign" in text
    assert "6 are not recorded as opt-outs yet" in text


def test_only_this_campaigns_sequences_count_as_recorded(tmp_path):
    """A link opt-out recorded against some other campaign's sequence does not settle this one."""
    text = _with_optouts(tmp_path, [_link("b@acme.example", seq="ELSEWHERE")], unsub=6)
    assert "6 are not recorded as opt-outs yet" in text


# ── 5. the Overview campaign card ────────────────────────────────────────────────────────


def test_the_overview_card_shows_the_unsubscribe_rate_beside_the_reply_rate(tmp_path):
    m = _model(tmp_path, _seq("S1", "Alpha angle", 40, unsub=8), _seq("S2", "Beta angle", 10))
    tile = _tile_labelled(vo._overview_view(m), "unsubscribed")
    assert ">8<" in tile
    assert "data-risk='unsub-rate'>16.0%</span>" in tile and ">50<" in tile


def test_an_unreadable_snapshot_shows_the_unsubscribe_tile_as_a_dash(tmp_path):
    profile = _fixture_10_24_1(tmp_path)
    _stats(tmp_path, profile, "{broken")
    tile = _tile_labelled(vo._overview_view(gd.build_model(profile, tmp_path)), "unsubscribed")
    assert "—" in tile and "unsub-rate" not in tile


def test_people_in_the_sending_tool_are_split_by_whether_their_sequence_is_sending():
    from gtm_core.email_campaign_dashboard.delivery import sending_split

    enrolled = {"a@example.com": "S1", "b@example.com": "S2", "c@example.com": "S3"}
    statuses = {"S1": "active", "S2": "paused", "S3": ""}
    emails = ["a@example.com", "b@example.com", "c@example.com", "d@example.com"]
    assert sending_split(emails, enrolled, statuses) == {"sending": 1, "paused": 1, "unknown": 2}


def test_the_lede_says_how_many_are_sending_and_how_many_are_loaded_but_paused():
    from gtm_core.prospect_lede import sending_line

    assert sending_line(4, {"sending": 0, "paused": 4}) == (
        "In the sending tool: 4 — 0 sending now, 4 loaded but paused "
        "(nothing goes out until you start them)."
    )
    mixed = sending_line(3, {"sending": 0, "paused": 1, "unknown": 2})
    assert "2 status unknown" in mixed and "until you start them" not in mixed
    assert sending_line(4, None) is None


def test_the_results_card_shows_state_words_auto_goals_and_the_operator_note():
    from gtm_core.email_campaign_dashboard.views_results import _campaign_card

    c = {
        "slug": "c1",
        "title": "Campaign",
        "state": "paused",
        "targets_auto": True,
        "targets_derivation": {"emails": "ten people x two"},
        "promised_vs_actual": {"emails": {"target": 20, "actual": 0, "pct": 0}},
        "results_note": "Week one was a <trial>.",
        "sequences": [],
        "experiment": {},
    }
    html = _campaign_card(c, None)
    assert "Paused" in html and 'data-state="paused"' in html
    assert "data-goal-auto" in html and "ten people x two" in html
    assert "Week one was a &lt;trial&gt;." in html  # the operator's text, escaped
    c.update(targets_auto=False, results_note="", state="completed")
    plain = _campaign_card(c, None)
    assert "data-goal-auto" not in plain and "data-results-note" not in plain
    assert "Finished" in plain

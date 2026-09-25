"""PS20 Phase 2 foundation — the tab list, declared sections, the filter's reach."""

import pytest

from gtm_core.email_campaign_dashboard import aggregate, config
from gtm_core.email_campaign_dashboard import format as fmt


def test_the_five_tabs_in_reading_order():
    assert config.TABS == (
        ("overview", "Overview"),
        ("accounts", "Accounts"),
        ("emails", "Emails"),
        ("results", "Results"),
        ("ops", "Operator notes"),
    )
    assert config.TAB_LABELS == dict(config.TABS)


def test_every_tab_declares_its_sections_and_every_ops_block_has_one_group():
    assert set(config.SECTIONS) == {t for t, _ in config.TABS}
    groups = [g for g, _s, _b in config.OPS_GROUPS]
    blocks = [b for _g, _s, bs in config.OPS_GROUPS for b in bs]
    assert len(set(groups + blocks)) == len(groups + blocks), "an id is declared twice"
    assert config.SECTIONS["ops"] == frozenset(groups + blocks)
    assert (groups[0], groups[-1]) == ("numbers", "maintenance")
    # One literal pin until Task 2.7a's structure test enforces the declared blocks.
    assert config.SECTIONS["overview"] == frozenset(
        {"lede", "campaign-lines", "accounts-funnel", "contacts-by-status"}
    )


def test_a_section_wraps_its_body_and_an_empty_one_is_absent():
    assert fmt.section("lede", "<p>x</p>") == '<div data-section="lede"><p>x</p></div>'
    assert fmt.section("lede", "  \n ") == ""


def test_a_tile_outside_the_filters_reach_carries_no_filter_hook():
    fmt._tiles_reset()
    inside = fmt._stat(3, "accounts researched", src="rows:all", reach=True)
    outside = fmt._stat(3, "accounts researched", src="rows:all", reach=False)
    frozen = fmt._stat(9, "people contacted", src="sum:campaigns.actuals.sent", reach=False)
    assert 'data-count-pred="all"' in inside and 'data-filter="on"' in inside
    for html in (outside, frozen):
        for hook in ("data-count-pred", "data-filter", "stat-why", "not filtered"):
            assert hook not in html, (hook, html)
    assert [t["reach"] for t in fmt._tiles_recorded()] == [True, False, False]


@pytest.mark.parametrize(
    "row,on_record,readable,word",
    [
        ({"status": "paused", "sent": 3}, True, True, "paused"),
        ({"sent": 3}, True, True, "started"),
        ({"sent": 0}, True, True, "staged"),
        (None, True, True, "staged"),  # no snapshot row: no status, no count
        (None, False, True, "none"),
        ({"status": "live"}, False, True, "active"),
        ({"sent": 3}, True, False, "unknown"),
    ],
)
def test_one_sequences_go_live_word(row, on_record, readable, word):
    assert aggregate.sequence_word(row, on_record, readable) == word


def test_the_tally_counts_the_same_words(monkeypatch):
    rows = [{"sent": 2}, {"sent": 0}, {"status": "paused", "sent": 0}]
    assert aggregate.seq_tally(rows, True) == "1 staged · 1 paused · 1 started"
    # …and it counts them by CALLING the one per-sequence rule, not a copy of it.
    calls = []
    monkeypatch.setattr(
        aggregate,
        "sequence_word",
        lambda row, on_record, readable: calls.append((on_record, readable)) or "unknown",
    )
    assert aggregate.seq_tally(rows, False) == "3 unknown"
    assert calls == [(True, False)] * 3, "readable must be passed through, not assumed"


def test_a_campaigns_own_figures_carry_its_replies_and_meetings():
    fig = {"contacted": ({"current": 3, "earlier": 2, "not_linked": 0}, None)}
    c = {"actuals": {"sent": 3, "replied": 2, "meetings": 1}, "archived_actuals": {"sent": 2}}
    assert aggregate.campaign_contacted(fig, c) == {
        "current": 3,
        "earlier": 2,
        "not_linked": 0,
        "replied": 2,
        "meetings": 1,
    }
    bare = aggregate.campaign_contacted(fig, {"actuals": {"sent": 3}})
    assert (bare["replied"], bare["meetings"]) == (0, 0), "an absent figure reads 0, not None"
    assert aggregate.campaign_contacted({"contacted": (None, "unreadable")}, c) is None

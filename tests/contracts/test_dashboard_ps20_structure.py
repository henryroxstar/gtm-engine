"""PS20 Phase 2 — five tabs, declared sections, one strip above them, and the filter and
`.tech` on Accounts only (TP T2.1-T2.7, T2.12, T2.14)."""

import html as _html
import re

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core.email_campaign_dashboard.config import OPS_GROUPS, SECTIONS, TABS
from tests.contracts.dashboard_page import classes, elements, head, panel, section, visible_text
from tests.contracts.test_dashboard_colour_reasons import SCOPES, _render, _seed_every_site
from tests.contracts.test_dashboard_ps20_trust import _fig, _fixture_10_24_1, _seed_with_roster
from tests.contracts.test_dashboard_tenant_prose import SLUG as FULL
from tests.contracts.test_dashboard_tenant_prose import _full_fixture
from tests.lint import operator_vocabulary as ov
from tests.test_email_campaign_dashboard import _page, _seed

FILTER_HOOKS = (
    "data-count-pred",
    "data-row",
    "data-group",
    "data-group-count",
    "data-filter",
    "data-stale-when-filtered",
    "data-no-filter",
)
FILTER_IDS = ("filterbar", "tech-toggle", "filter-note", "filter-tripwire")


def section_violations(page: str) -> list[str]:
    out = []
    for tab, _tag, a, anc in elements(page):
        if tab is None:
            continue
        if "data-section" in a and a["data-section"] not in SECTIONS[tab]:
            out.append(f"{tab}:{a['data-section']}")
        if "card" in classes(a) and not any("data-section" in x for x in [*anc, a]):
            out.append(f"{tab}: a card in no section")
        if "card" in classes(a) and any("card" in classes(x) for x in anc):
            out.append(f"{tab}: a card inside a card")
    return out


def filter_leaks(page: str) -> list[str]:
    out = []
    for tab, tag, a, _anc in elements(page):
        if tab == "accounts":
            continue
        if "tech" in classes(a):
            out.append(f"{tab}: .tech on <{tag}>")
        if (
            any(h in a for h in FILTER_HOOKS)
            or a.get("id") in FILTER_IDS
            or classes(a) & {"stat-why", "why", "techtoggle", "filterbar"}
        ):
            out.append(f"{tab}: a filter hook on <{tag}>")
    return out


def test_five_tabs_overview_first(tmp_path):
    page = _page(tmp_path, _seed(tmp_path))
    nav = re.findall(r'<button class="tab( on)?" data-t="(\w+)">([^<]+)</button>', page)
    assert [_html.unescape(label) for _on, _k, label in nav] == [
        "Overview",
        "Accounts",
        "Emails",
        "Results",
        "Operator notes",
    ]
    assert [k for _on, k, _l in nav] == [t for t, _ in TABS]
    assert [on for on, _k, _l in nav] == [" on", "", "", "", ""]
    assert re.findall(r'<section id="p-(\w+)" class="panel( on)?">', page) == [
        ("overview", " on"),
        ("accounts", ""),
        ("emails", ""),
        ("results", ""),
        ("ops", ""),
    ]


def test_the_lede_is_the_first_card_on_overview(tmp_path):
    page = _page(tmp_path, _seed(tmp_path))
    first = next(
        a for tab, _t, a, _anc in elements(page) if tab == "overview" and "card" in classes(a)
    )
    assert "lede" in classes(first)


@pytest.mark.parametrize("mode", SCOPES)
def test_every_rendered_section_is_declared_for_its_tab(tmp_path, mode):
    page = _render(tmp_path, _seed_every_site(tmp_path), mode)
    assert section_violations(page) == []
    rendered = {
        a["data-section"] for tab, _t, a, _anc in elements(page) if tab and "data-section" in a
    }
    assert {"lede", "account-table", "email-table", "campaign-results", "numbers"} <= rendered


def test_the_section_check_catches_an_undeclared_a_loose_and_a_nested_card(tmp_path):
    page = _page(tmp_path, _seed(tmp_path))
    top = '<section id="p-overview" class="panel on">'
    bogus = page.replace(top, top + '<div data-section="bogus"><div class="card">x</div></div>')
    assert section_violations(bogus) == ["overview:bogus"]
    res = '<section id="p-results" class="panel">'
    assert section_violations(page.replace(res, res + '<div class="card">x</div>')) == [
        "results: a card in no section"
    ]
    nested = page.replace(
        res,
        res
        + '<div data-section="learnings"><div class="card"><div class="card">x</div></div></div>',
    )
    assert "results: a card inside a card" in section_violations(nested)


@pytest.mark.parametrize("mode", SCOPES)
def test_only_the_strip_sits_above_the_tabs(tmp_path, mode):
    page = _render(tmp_path, _seed_every_site(tmp_path), mode)  # undated figures: the strip shows
    top = head(page)
    # Elements, not substrings: the <style> above the tabs names .filterbar and .techtoggle.
    els = [a for _p, _t, a, _anc in elements(top)]
    cards = [a for a in els if "card" in classes(a)]
    assert len(cards) == 1 and cards[0].get("data-warn")
    assert top.index('class="card warn"') < top.index('class="tabs"')
    assert not [
        a
        for a in els
        if a.get("id") in FILTER_IDS
        or "data-section" in a
        or classes(a) & {"techtoggle", "filterbar", "stat", "stats"}
    ]


def test_panels_are_hidden_by_class_only(tmp_path):
    page = _page(tmp_path, _seed(tmp_path))
    panels = [a for _p, t, a, _anc in elements(page) if t == "section"]
    assert len(panels) == 5 and not any("hidden" in a for a in panels)


@pytest.mark.parametrize("mode", SCOPES)
def test_the_filter_and_the_technical_detail_live_on_accounts_only(tmp_path, mode):
    page = _render(tmp_path, _seed_every_site(tmp_path), mode)
    assert filter_leaks(page) == []
    accounts = panel(page, "accounts")
    assert 'id="filterbar"' in accounts and 'id="tech-toggle"' in accounts
    assert re.search(
        r'id="filter-tripwire"[^>]*\bhidden\b', accounts
    )  # hidden on a consistent page
    assert "tech" in {c for _p, _t, a, _anc in elements(accounts) for c in classes(a)}
    # No "not filtered" text check: `.stat-why` and `.why` are always `hidden`, so visible text
    # never carries it, and `filter_leaks` already fails either class outside Accounts.


def test_the_leak_check_catches_a_hook_outside_accounts(tmp_path):
    page = _page(tmp_path, _seed_with_roster(tmp_path))
    ops = '<section id="p-ops" class="panel">'
    assert filter_leaks(page.replace(ops, ops + '<span class="tech">x</span>')) == [
        "ops: .tech on <span>"
    ]
    assert filter_leaks(page.replace(ops, ops + '<span data-count-pred="all">1</span>'))


def test_the_capability_summary_is_shown_plainly_in_operator_notes(tmp_path):
    from gtm_core.sequencers import render_summary

    m = gd.build_model(_seed_with_roster(tmp_path), tmp_path)
    compliance = section(panel(gd.render_html(m), "ops"), "compliance")
    summary = render_summary(m["inbound"]["capability_rows"], fmt="html")
    assert (
        summary
        and summary in compliance
        and "tech" not in {c for _p, _t, a, _anc in elements(compliance) for c in classes(a)}
    )


def test_every_operator_notes_group_renders_in_order(tmp_path):
    ops = panel(_page(tmp_path, _seed(tmp_path)), "ops")
    got = re.findall(r'<details class="ops-group" data-section="([\w-]+)"', ops)
    assert got == [g for g, _s, _b in OPS_GROUPS]


def test_a_scoped_page_has_five_tabs_one_campaign_line_and_the_pool_notes(tmp_path):
    page = _render(tmp_path, _seed_every_site(tmp_path), "campaign")
    assert re.findall(r'data-t="(\w+)"', page) == [t for t, _ in TABS]
    lines = section(panel(page, "overview"), "campaign-lines")
    assert len(re.findall(r'data-figure="campaign-contacted-', lines)) == 1
    assert "Profile-wide, not this campaign" in visible_text(panel(page, "overview"))


def test_a_partial_roster_on_a_scoped_page_shows_its_rows_and_names_the_gap(tmp_path):
    from tests.contracts.test_dashboard_aggregation_refusal import BASE
    from tests.contracts.test_dashboard_aggregation_refusal import _seed as _two

    _two(tmp_path, second=BASE)
    m = gd.build_model("acme", tmp_path)
    page = gd.render_html(gd.scope_to_campaign(m, "mine-20260904,other-20260718"))
    accounts = panel(page, "accounts")
    assert "Analytical Engine" in section(accounts, "account-table")
    assert "Covers 1 of 2 campaigns" in visible_text(accounts)


@pytest.mark.parametrize("scoped", [False, True])
def test_overview_results_and_operator_notes_agree_on_people_contacted(tmp_path, scoped):
    m = gd.build_model(_fixture_10_24_1(tmp_path), tmp_path)
    page = gd.render_html(gd.scope_to_campaign(m, "c1") if scoped else m)
    overview, results, ops = (panel(page, t) for t in ("overview", "results", "ops"))
    assert (
        _fig(overview, "campaign-contacted-c1"),
        _fig(results, "contacted-current"),
        _fig(results, "results-contacted-c1"),
        _fig(ops, "ops-contacted"),
    ) == ("10", "10", "10", "10")


RETIRED_TABS = (
    "Where things stand",
    "The worklist",
    "Who we're emailing",
    "What we're saying",
    "What we'll learn",
)
NON_OPS = ("overview", "accounts", "emails", "results")


def _pages(tmp_path):
    """Every page shape the retired names could hide on: three scopes of the every-site fixture,
    the full fixture's rollup and campaign page, and a scoped page with NO roster (the pool
    branch, where `_pool_scope_note` renders)."""
    every = _seed_every_site(tmp_path / "every")
    full = _full_fixture(tmp_path / "full")
    bare = _seed(tmp_path / "bare")
    yield from (_render(tmp_path / "every", every, mode) for mode in SCOPES)
    m = gd.build_model(full, tmp_path / "full")
    yield gd.render_html(m)
    yield gd.render_html(gd.scope_to_campaign(gd.build_model(full, tmp_path / "full"), FULL))
    yield gd.render_html(gd.scope_to_campaign(gd.build_model(bare, tmp_path / "bare"), "c1"))


def test_no_sentence_names_a_retired_tab(tmp_path):
    for page in _pages(tmp_path):
        text = visible_text(page)
        for name in RETIRED_TABS:
            assert name not in text, name


def test_visible_text_outside_operator_notes_uses_the_operators_words(tmp_path):
    for page in _pages(tmp_path):
        for tab in NON_OPS:
            body = panel(page, tab)
            if tab == "overview":  # the terminal's own lede, verbatim (PS15) — Q5, TP §6
                body = body.replace(section(body, "lede"), "")
            shown = visible_text(body, skip=frozenset({"tech"}))
            assert [h for h in ov.findings(text=shown) if h[0] == "<text>"] == [], tab


def test_the_vocabulary_check_reads_only_what_the_toggle_leaves_visible(tmp_path):
    accounts = panel(_page(tmp_path, _seed_with_roster(tmp_path)), "accounts")
    assert ">Research verdict<" in accounts  # the toggled column says it…
    shown = visible_text(accounts, skip=frozenset({"tech"}))
    assert not [h for h in ov.findings(text=shown) if h[0] == "<text>"]
    toggled = visible_text(accounts)  # …and with the toggle on, the lint finds it
    assert [h for h in ov.findings(text=toggled) if h[0] == "<text>"]

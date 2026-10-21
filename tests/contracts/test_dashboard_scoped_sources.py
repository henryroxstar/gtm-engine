"""A scoped page says ONE thing about its sending figures, on every surface that says anything.

WHY THIS EXISTS (verification audit F-07, repro R21). ``scope_to_campaign`` narrows the page to a
campaign's own sequences and ``figure_ages.rescope`` re-judges the figures for that narrower set.
It re-judged ``figures`` and ``warnings`` and left ``sources`` as built for the whole profile, so
a campaign page whose own sequences were fresh printed a fresh header and no strip above a sources
table that said the same figures were "old", dated by another campaign's sequence. The PRD's
surface-agreement rule (§4B) names the header, the strip, the per-campaign line, the sources
table and the ``--check-fresh`` record; they all have to read the page's own figures.

Each case here renders the page and compares what every surface printed, so a surface that stops
reading the rescoped value fails by name. Every id and campaign is fictional.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from gtm_core import email_campaign_dashboard as gd
from gtm_core.email_campaign_dashboard import figure_ages, freshness, provenance
from tests.contracts.test_dashboard_figure_ages import (
    LIMIT,
    _at,
    _campaign,
    _header,
    _refresh,
    _row,
    _scoped,
)
from tests.contracts.test_dashboard_ps20_trust import _stats, _strip
from tests.test_email_campaign_dashboard import _model, _seed

_HEADER_FIGURES = re.compile(r"Sending figures from (\d{4}-\d{2}-\d{2})")
_FIGURES_FROM = re.compile(r"figures from (\d{4}-\d{2}-\d{2})")


def _two_campaigns(tmp_path, *, c1_days: float, c2_days: float):
    """c1 lists S1 and c2 lists S2; each sequence was fetched on its own day."""
    profile = _seed(tmp_path)
    _campaign(tmp_path, profile, "c2", "Campaign Two", ["S2"])
    _refresh(tmp_path, profile, [_row("S1")], c1_days)
    _refresh(tmp_path, profile, [_row("S2")], c2_days)
    return profile


def _sources_row(page: str) -> tuple[str, str, str]:
    """(label, as-of, state words) of the figures row, read off the RENDERED sources table."""
    group = page.split('data-section="sources"', 1)[1].split("</details>", 1)[0]
    row = [r for r in group.split("<tr>") if provenance.SOURCES["figures"]["label"] in r][0]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    return cells[0], cells[1], cells[2]


def _surfaces(m: dict) -> dict:
    """Everything the page says about the figures' date and verdict, each read from its own
    surface: the header, the strip, the per-campaign line, the sources row, the model's row and
    the date the inventory would record for ``--check-fresh``."""
    page = gd.render_html(m)
    _label, as_of, words = _sources_row(page)
    header_date = _HEADER_FIGURES.search(_header(page))
    meta = freshness.page_meta(m)["figures_fetched"]
    return {
        "header_date": header_date.group(1) if header_date else None,
        "strip": 'data-warn="figures-old"' in _strip(page),
        "campaign_line_dates": set(_FIGURES_FROM.findall(page)),
        "table_as_of": as_of,
        "table_words": words,
        "model_state": m["sources"]["figures"]["state"],
        "meta_date": meta[:10] if meta else None,
    }


def test_a_fresh_campaign_page_is_fresh_on_every_surface_though_another_campaign_is_old(tmp_path):
    """The R21 repro. Mutation caught: delete the ``m["sources"]`` re-derivation from
    ``figure_ages.rescope`` and the table says old over a fresh header."""
    profile = _two_campaigns(tmp_path, c1_days=0.1, c2_days=LIMIT + 3)
    s = _surfaces(_scoped(tmp_path, profile, "c1"))
    today = _at(0.1)[:10]
    assert s["header_date"] == today
    assert s["strip"] is False
    assert s["campaign_line_dates"] == {today}
    assert s["table_as_of"] == today
    assert s["model_state"] == "current"
    assert s["table_words"] == "current"
    assert s["meta_date"] == today


def test_the_in_page_banner_on_a_scoped_page_measures_from_its_own_sequences(tmp_path):
    """The banner reads the same ``m["figures"]`` as the header, so a fresh campaign's page
    carries ITS instant — not the profile's oldest — and the old campaign's page, already over
    the limit at build, carries no banner (its strip says so statically)."""
    profile = _two_campaigns(tmp_path, c1_days=0.1, c2_days=LIMIT + 3)
    fresh = gd.render_html(_scoped(tmp_path, profile, "c1"))
    old = gd.render_html(_scoped(tmp_path, profile, "c2"))
    attr = re.search(r'id="stale-banner" data-figures="([^"]+)"', fresh)
    assert attr and attr.group(1).startswith(_at(0.1)[:10])
    assert "stale-banner" not in old and 'data-warn="figures-old"' in old


def test_the_old_campaigns_page_is_old_on_every_surface(tmp_path):
    profile = _two_campaigns(tmp_path, c1_days=0.1, c2_days=LIMIT + 3)
    s = _surfaces(_scoped(tmp_path, profile, "c2"))
    old = _at(LIMIT + 3)[:10]
    assert s["header_date"] == old
    assert s["strip"] is True
    assert s["campaign_line_dates"] == {old}
    assert s["table_as_of"] == old
    assert s["model_state"] == "old" and s["table_words"].startswith("old")
    assert s["meta_date"] == old


def test_the_rollup_is_old_because_its_oldest_sequence_is(tmp_path):
    """The unscoped page is dated by the oldest sequence ANY campaign lists, so one old
    campaign makes the rollup old — the other half of the same disagreement."""
    profile = _two_campaigns(tmp_path, c1_days=0.1, c2_days=LIMIT + 3)
    s = _surfaces(gd.build_model(profile, tmp_path))
    old = _at(LIMIT + 3)[:10]
    assert s["header_date"] == old and s["strip"] is True
    assert s["table_as_of"] == old and s["model_state"] == "old"


def test_a_page_covering_both_campaigns_is_old_when_either_is(tmp_path):
    profile = _two_campaigns(tmp_path, c1_days=0.1, c2_days=LIMIT + 3)
    s = _surfaces(_scoped(tmp_path, profile, "c1,c2"))
    assert s["strip"] is True and s["model_state"] == "old"
    assert s["table_as_of"] == s["header_date"] == _at(LIMIT + 3)[:10]


def test_every_campaign_old_makes_every_page_old(tmp_path):
    profile = _two_campaigns(tmp_path, c1_days=LIMIT + 1, c2_days=LIMIT + 3)
    for slugs in ("c1", "c2", "c1,c2"):
        s = _surfaces(_scoped(tmp_path, profile, slugs))
        assert s["strip"] is True and s["model_state"] == "old", slugs
        assert s["table_as_of"] == s["header_date"], slugs


def test_a_scoped_page_re_derives_the_row_from_figures_not_from_the_rollups_row(tmp_path):
    """The re-derivation reads the re-judged ``figures``: poison the row the model was built
    with and the scoped page must still print what its own figures say. Mutation caught:
    ``rescope`` leaving ``m["sources"]`` alone."""
    profile = _two_campaigns(tmp_path, c1_days=0.1, c2_days=LIMIT + 3)
    m = gd.build_model(profile, tmp_path)
    m["sources"]["figures"] = {"label": "x", "state": "missing", "as_of": "1999-01-01"}
    scoped = gd.scope_to_campaign(m, "c1")
    row = scoped["sources"]["figures"]
    assert row["state"] == "current" and row["as_of"] == _at(0.1)[:10]
    assert row["label"] == provenance.SOURCES["figures"]["label"]


def test_rescope_leaves_the_other_source_rows_exactly_as_built(tmp_path):
    """Only the figures row depends on which campaigns are in scope; every other row is a file
    the whole profile shares, so re-deriving it per campaign would be a second implementation."""
    profile = _two_campaigns(tmp_path, c1_days=0.1, c2_days=LIMIT + 3)
    m = gd.build_model(profile, tmp_path)
    before = {k: v for k, v in m["sources"].items() if k != "figures"}
    scoped = gd.scope_to_campaign(m, "c1")
    assert {k: v for k, v in scoped["sources"].items() if k != "figures"} == before


def test_scope_to_campaign_on_a_model_with_no_snapshot_is_a_no_op_for_the_figures(tmp_path):
    """The fixture ``_model()`` carries no ``status.snapshot``; ``rescope`` used to index it
    directly and raised ``KeyError``. Mutation caught: restore ``m["status"]["snapshot"]``."""
    m = _model()
    assert "snapshot" not in (m.get("status") or {})
    figures_before = m.get("figures")
    sources_before = m.get("sources")
    out = gd.scope_to_campaign(m, "mine-20260904")
    assert out.get("figures") == figures_before
    assert out.get("sources") == sources_before


def test_rescope_called_directly_with_no_status_at_all_changes_nothing():
    m = {"campaigns": {"campaigns": []}}
    figure_ages.rescope(m, m["campaigns"], datetime(2026, 10, 1, tzinfo=UTC))
    assert m == {"campaigns": {"campaigns": []}}


def test_a_future_dated_per_campaign_line_carries_the_same_age_unknown_caveat(tmp_path):
    """Surface agreement for the one state the per-campaign line gets wrong: figures dated more
    than a day ahead. The header and the strip say "in the future, so their age is unknown"; the
    line under each campaign must not read as a clean date."""
    stamp = _at(-3)
    day = stamp[:10]
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": stamp, "sequences": [{"id": "S1", "sent": 1}]})
    m = gd.build_model(profile, tmp_path)
    assert m["figures"]["state"] == "future"
    page = gd.render_html(m)
    assert "which is in the future" in _header(page)
    body = page.replace(_header(page), "")
    assert re.search(rf"{day}[^<]{{0,80}}(future|age unknown)", body), (
        "the line reads as a clean date"
    )

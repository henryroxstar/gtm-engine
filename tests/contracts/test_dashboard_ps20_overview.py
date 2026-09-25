"""PS20 Phase 2 — the Overview tab: the lede, one line per campaign, and the terminal's two
blocks (TP T2.2, T2.14)."""

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import views_overview as vo
from gtm_core.email_campaign_dashboard.aggregate import _scope_figures
from gtm_core.email_campaign_dashboard.config import SECTIONS
from gtm_core.email_campaign_dashboard.format import scope_label
from gtm_core.email_campaign_dashboard.health import figures_date
from tests.contracts.dashboard_page import classes, elements, section, section_ids, visible_text
from tests.contracts.test_dashboard_ps20_trust import _fig, _fixture_10_24_1, _manifest, _stats
from tests.test_email_campaign_dashboard import _seed


def _with_c2(tmp_path, profile):
    """A second campaign that claims sequence X, which no campaign listed before."""
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c2.campaign.toml").write_text(
        'slug = "c2"\ntitle = "Campaign Two"\nsequences = ["X"]\n', encoding="utf-8"
    )
    return profile


def test_the_lede_then_the_campaigns_then_the_terminals_two_blocks(tmp_path):
    html = vo._overview_view(gd.build_model(_fixture_10_24_1(tmp_path), tmp_path))
    assert section_ids(html) == ["lede", "campaign-lines", "accounts-funnel", "contacts-by-status"]
    assert set(section_ids(html)) <= SECTIONS["overview"]
    first_card = next(a for _p, _t, a, _anc in elements(html) if "card" in classes(a))
    assert "lede" in classes(first_card)


def test_one_line_per_campaign_read_off_the_figures(tmp_path):
    m = gd.build_model(_fixture_10_24_1(tmp_path), tmp_path)
    lines = section(vo._overview_view(m), "campaign-lines")
    assert _fig(lines, "campaign-word-c1") == "started"
    assert (
        _fig(lines, "campaign-contacted-c1") == "10"
    )  # current only: the retired run's 24 is not here
    assert _fig(lines, "campaign-replied-c1") == "0"
    assert _fig(lines, "campaign-contacted-unlinked") == "1"  # sequence X, in no campaign
    assert f"figures from {figures_date(m['status']['snapshot']['fetched'])}" in visible_text(lines)
    pill = next(
        a for _p, _t, a, _anc in elements(lines) if a.get("data-figure") == "campaign-word-c1"
    )
    assert classes(pill) == {"pill"}  # a state, not a warning (PRD P1.5)


def test_the_campaign_lines_add_up_to_the_scope(tmp_path):
    m = gd.build_model(_with_c2(tmp_path, _fixture_10_24_1(tmp_path)), tmp_path)
    lines = section(vo._overview_view(m), "campaign-lines")
    got = [int(_fig(lines, f"campaign-contacted-{s}")) for s in ("c1", "c2")]
    assert got == [10, 1] and sum(got) == _scope_figures(m)["contacted"][0]["current"]
    assert _fig(lines, "campaign-contacted-unlinked") is None  # X belongs to c2 now


def test_an_unreadable_snapshot_reads_unknown_never_zero(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, "{broken")
    lines = section(vo._overview_view(gd.build_model(profile, tmp_path)), "campaign-lines")
    assert (_fig(lines, "campaign-word-c1"), _fig(lines, "campaign-contacted-c1")) == (
        "unknown",
        "—",
    )
    assert _fig(lines, "campaign-replied-c1") == "—"  # refused replies never read 0
    assert "couldn't be read" in visible_text(lines)


def _c1(m):
    return next(c for c in m["campaigns"]["campaigns"] if c.get("slug") == "c1")


def test_a_blank_title_falls_back_to_the_slug(tmp_path):
    m = gd.build_model(_fixture_10_24_1(tmp_path), tmp_path)
    for title in (None, ""):
        _c1(m)["title"] = title
        text = visible_text(section(vo._overview_view(m), "campaign-lines"))
        assert "c1 started" in text and "None" not in text, title


def test_a_title_is_escaped_and_one_reads_singular(tmp_path):
    m = gd.build_model(_fixture_10_24_1(tmp_path), tmp_path)
    _c1(m).update(title="<script>x</script>", actuals={"sent": 1, "replied": 1, "meetings": 2})
    lines = section(vo._overview_view(m), "campaign-lines")
    assert "<script>" not in lines
    assert "1 person contacted" in visible_text(lines) and "1 reply" in visible_text(lines)
    assert _fig(lines, "campaign-replied-c1") == "1"  # its replies, not its 2 meetings


def test_an_undated_snapshot_says_so(tmp_path):
    profile = _seed(tmp_path)
    _manifest(tmp_path, profile, ["S1"], [])
    _stats(tmp_path, profile, {"sequences": [{"id": "S1", "sent": 2}]})  # no "fetched"
    lines = section(vo._overview_view(gd.build_model(profile, tmp_path)), "campaign-lines")
    assert _fig(lines, "campaign-date-c1") == "figures carry no date"


def test_the_campaign_lines_name_their_scope(tmp_path):
    profile = _with_c2(tmp_path, _fixture_10_24_1(tmp_path))
    rollup = gd.build_model(profile, tmp_path)
    scoped = gd.scope_to_campaign(gd.build_model(profile, tmp_path), "c1")
    assert scope_label(rollup) != scope_label(scoped)
    for m in (rollup, scoped):
        text = visible_text(section(vo._overview_view(m), "campaign-lines"))
        assert f"One line per campaign in {scope_label(m)}:" in text


def test_no_campaigns_and_nothing_unlinked_renders_no_campaign_card(tmp_path):
    m = gd.build_model(_seed(tmp_path), tmp_path)
    m["campaigns"]["campaigns"] = []
    assert vo._campaign_lines(m) == ""
    assert "campaign-lines" not in section_ids(vo._overview_view(m))


def test_a_scoped_overview_has_one_line_and_labels_the_profile_wide_blocks(tmp_path):
    profile = _with_c2(tmp_path, _fixture_10_24_1(tmp_path))
    html = vo._overview_view(gd.scope_to_campaign(gd.build_model(profile, tmp_path), "c1"))
    lines = section(html, "campaign-lines")
    assert _fig(lines, "campaign-contacted-c1") == "10"
    assert _fig(lines, "campaign-contacted-c2") is None
    for sid in ("lede", "accounts-funnel", "contacts-by-status"):
        assert "Profile-wide, not this campaign." in visible_text(section(html, sid)), sid


def test_needs_an_address_is_split_out_for_operator_notes(tmp_path, monkeypatch):
    from tests.test_dashboard_operator_truth import (
        MIXED_ACCOUNTS,
        MIXED_STATE,
        PROFILE,
        _tenant,
        page_numbers,
    )

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE)
    m = gd.build_model(PROFILE, content_root=tmp_path)
    html = vo._overview_view(m)
    assert page_numbers(html)["Waiting on you"] == 2 and page_numbers(html)["All accounts"] == 7
    assert "Still finding the right person" not in section(html, "contacts-by-status")
    assert page_numbers(vo._needs_address_block(m))["Still finding the right person"] == 1

"""PS20 Task 2.0 decision — a scoped page's strip: its own sum, its own sequences' gaps,
the one snapshot's age."""

import json

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from tests.contracts.test_dashboard_ps20_trust import _ago, _stats, _strip
from tests.test_email_campaign_dashboard import _seed


def _campaign(tmp_path, profile, slug, title, sequences):
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / f"{slug}.campaign.toml").write_text(
        f'slug = "{slug}"\ntitle = "{title}"\nsequences = {json.dumps(sequences)}\n\n'
        "[targets]\nemails = 9\n",
        encoding="utf-8",
    )


def _scoped(tmp_path, profile, slugs):
    return gd.scope_to_campaign(gd.build_model(profile, tmp_path), slugs)


def _two_claim_s1(tmp_path):
    """c1 and c2 both list S1; c3 lists S3. Fresh, readable figures."""
    profile = _seed(tmp_path)
    _campaign(tmp_path, profile, "c2", "Campaign Two", ["S1"])
    _campaign(tmp_path, profile, "c3", "Campaign Three", ["S3"])
    rows = [{"id": "S1", "sent": 5}, {"id": "S3", "sent": 2}]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    return profile


def test_the_rollup_strip_names_the_campaigns_that_share_a_sequence(tmp_path):
    m = gd.build_model(_two_claim_s1(tmp_path), tmp_path)
    assert m["warnings"] == ["records-disagree"]
    strip = _strip(gd.render_html(m))
    assert "counted twice" in strip and "Campaign One" in strip and "Campaign Two" in strip
    assert "Campaign Three" not in strip


def test_a_campaign_page_checks_its_own_sum(tmp_path):
    profile = _two_claim_s1(tmp_path)
    for slug in ("c1", "c3"):
        scoped = _scoped(tmp_path, profile, slug)
        assert scoped["warnings"] == [], slug
        # The attribute, not the bare word: the stylesheet above the tabs names data-warn in
        # a comment, so a bare substring check could never pass.
        assert 'data-warn="' not in _strip(gd.render_html(scoped)), slug
    # Both claimants on one page: S1 IS counted twice here.
    assert _scoped(tmp_path, profile, "c1,c2")["warnings"] == ["records-disagree"]


def test_a_campaign_page_shows_a_records_gap_only_for_its_own_sequences(tmp_path):
    profile = _seed(tmp_path)  # c1 lists S1
    _campaign(tmp_path, profile, "c2", "Campaign Two", ["S9"])  # in our records, not the figures
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [{"id": "S1", "sent": 1}]})
    m = gd.build_model(profile, tmp_path)
    assert (m["reconciliation"]["in_ledger_only"], m["warnings"]) == (["S9"], ["records-disagree"])
    assert _scoped(tmp_path, profile, "c1")["warnings"] == []
    c2 = _scoped(tmp_path, profile, "c2")
    assert c2["warnings"] == ["records-disagree"]
    assert c2["reconciliation"]["in_ledger_only"] == ["S9"]
    strip = _strip(gd.render_html(c2))
    assert "These numbers may be out of date" in strip and "Campaign Two" in strip


def test_an_old_or_unreadable_snapshot_is_every_pages(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": _ago(3), "sequences": [{"id": "S1", "sent": 0}]})
    assert _scoped(tmp_path, profile, "c1")["warnings"] == ["figures-old"]
    _stats(tmp_path, profile, "{broken")
    assert _scoped(tmp_path, profile, "c1")["warnings"] == ["unreadable"]


def test_a_gap_no_campaign_lists_says_so(tmp_path):
    """A snapshot-only id belongs to no campaign, so the strip names none — and says that,
    rather than ending on an empty "It affects ."."""
    profile = _seed(tmp_path)  # c1 lists S1
    rows = [{"id": "S1", "sent": 0}, {"id": "X", "sent": 0}]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    m = gd.build_model(profile, tmp_path)
    assert m["reconciliation"]["in_snapshot_only"] == ["X"]
    assert "It affects sequences no campaign lists." in _strip(gd.render_html(m))

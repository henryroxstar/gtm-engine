"""Tests for gtm_core.campaigns_dashboard — the CRO-facing campaigns portfolio page."""

from __future__ import annotations

import json

from gtm_core import campaigns_dashboard as cd
from gtm_core import prospects_consolidate as pc


def _write_manifest(tmp_path, profile, slug, body):
    path = (
        pc._prospects_dir(profile, tmp_path).parent
        / "plans"
        / "campaigns"
        / f"{slug}.campaign.toml"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _write_history(tmp_path, profile, lines):
    path = tmp_path / profile / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def _write_stats(tmp_path, profile, sequences):
    path = pc._pool_dir(profile, tmp_path) / "sequence-stats.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sequences": sequences}), encoding="utf-8")


def test_load_manifests_reads_toml_with_and_without_targets(tmp_path):
    profile = "acme"
    _write_manifest(
        tmp_path,
        profile,
        "c1",
        """
slug = "c1"
title = "Campaign One"

[targets]
sqls = 35
""",
    )
    _write_manifest(tmp_path, profile, "c2", 'slug = "c2"\ntitle = "No targets"\n')

    manifests = cd._load_manifests(profile, content_root=tmp_path)
    assert len(manifests) == 2
    by_slug = {m["slug"]: m for m in manifests}
    assert by_slug["c1"]["targets"]["sqls"] == 35
    assert by_slug["c2"]["targets"] == {}
    assert by_slug["c2"]["sequences"] == []


def test_staged_sequences_excludes_cleanup_and_tolerates_both_key_shapes(tmp_path):
    profile = "acme"
    _write_history(
        tmp_path,
        profile,
        [
            {
                "event": "sequence_staged",
                "sequence_id": "s1",
                "campaign": "c1",
                "steps": 4,
                "prospects_enrolled": 30,
            },
            {
                "event": "sequence_staged",
                "sequence_id": "s2",
                "touches": 3,
                "leads_enrolled": 10,
            },
            {
                "event": "sequence_cleanup",
                "sequences_deleted": [{"id": "s2"}],
            },
        ],
    )
    staged = cd._staged_sequences(profile, content_root=tmp_path)
    assert [s["sequence_id"] for s in staged] == ["s1"]
    assert staged[0]["enrolled"] == 30
    assert staged[0]["steps"] == 4


def test_build_campaigns_joins_manifest_stats_and_finds_unlinked(tmp_path):
    profile = "acme"
    _write_manifest(
        tmp_path,
        profile,
        "c1",
        """
slug = "c1"
title = "Campaign One"
status = "active"
sequences = ["s1"]

[targets]
sqls = 10
emails = 100
""",
    )
    _write_history(
        tmp_path,
        profile,
        [
            {"event": "sequence_staged", "sequence_id": "s1", "campaign": "c1"},
            {"event": "sequence_staged", "sequence_id": "s2"},
        ],
    )
    _write_stats(
        tmp_path,
        profile,
        [
            {
                "sequenceId": "s1",
                "sequenceName": "Live One",
                "status": "active",
                "prospects": [
                    {"total": "100", "contacted": "100", "replied": "6", "meetingBooked": "2"}
                ],
                "emails": {"status": {"delivered": "100", "replied": "6"}},
            }
        ],
    )

    model = cd.build_campaigns(profile, content_root=tmp_path)
    campaigns = model["campaigns"]
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c["slug"] == "c1"
    assert c["actuals"]["meetings"] == 2
    pva = c["promised_vs_actual"]
    assert pva["sqls"]["target"] == 10
    assert pva["sqls"]["actual"] == 2
    assert pva["emails"]["target"] == 100
    assert pva["emails"]["actual"] == 100

    unlinked_ids = [s["sequence_id"] for s in model["unlinked_sequences"]]
    assert unlinked_ids == ["s2"]


def test_manifest_links_live_sequence_that_was_never_staged(tmp_path):
    """The real backfill case: a hand-loaded sequence has live stats but no
    sequence_staged ledger event. The manifest's sequences list must still pull
    it into the campaign, synthesized from live stats."""
    profile = "acme"
    _write_manifest(
        tmp_path,
        profile,
        "c1",
        """
slug = "c1"
title = "Campaign One"
sequences = ["live1"]

[targets]
emails = 100
""",
    )
    # No history at all — the sequence was never staged.
    _write_stats(
        tmp_path,
        profile,
        [
            {
                "sequenceId": "live1",
                "sequenceName": "Hand Loaded",
                "status": "paused",
                "prospects": [
                    {"total": "30", "contacted": "5", "replied": "1", "meetingBooked": "0"}
                ],
                "emails": {"status": {"delivered": "5", "replied": "1"}},
            }
        ],
    )
    model = cd.build_campaigns(profile, content_root=tmp_path)
    c = model["campaigns"][0]
    assert [s["sequence_id"] for s in c["sequences"]] == ["live1"]
    assert c["actuals"]["sent"] == 5
    assert model["unlinked_sequences"] == []

    html_out = cd.render_html(model)
    assert "Hand Loaded" in html_out
    assert "paused" in html_out


def test_render_html_shows_campaign_and_unlinked_and_sql_proxy_note(tmp_path):
    profile = "acme"
    _write_manifest(
        tmp_path,
        profile,
        "c1",
        """
slug = "c1"
title = "Campaign One"
status = "active"
sequences = ["s1"]

[targets]
sqls = 10
""",
    )
    _write_history(
        tmp_path,
        profile,
        [{"event": "sequence_staged", "sequence_id": "s2"}],
    )
    model = cd.build_campaigns(profile, content_root=tmp_path)
    html_out = cd.render_html(model)
    assert "Campaign One" in html_out
    assert "Promised vs actual" in html_out
    assert "SQL" in html_out
    assert "s2" in html_out


def test_render_dashboard_writes_campaigns_html(tmp_path):
    profile = "acme"
    _write_manifest(tmp_path, profile, "c1", 'slug = "c1"\ntitle = "Campaign One"\n')
    out = cd.render_dashboard(profile, content_root=tmp_path)
    assert out.exists()
    assert out.name == "campaigns.html"
    assert "Campaign One" in out.read_text(encoding="utf-8")


def test_consolidate_auto_refreshes_campaigns_html(tmp_path):
    profile = "acme"
    _write_manifest(tmp_path, profile, "c1", 'slug = "c1"\ntitle = "Campaign One"\n')
    pc.consolidate(profile, content_root=tmp_path)
    out = cd.dashboard_path(profile, content_root=tmp_path)
    assert out.exists()
    assert "Campaign One" in out.read_text(encoding="utf-8")

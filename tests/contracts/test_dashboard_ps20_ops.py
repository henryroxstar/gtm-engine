"""PS20 Phase 2 — Operator notes: collapsed groups, each holding its declared blocks (TP T2.5)."""

import json
import re

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import views_ops as vops
from gtm_core.email_campaign_dashboard import views_ready
from gtm_core.email_campaign_dashboard.config import OPS_GROUPS
from tests.contracts.dashboard_page import section, section_ids, visible_text
from tests.contracts.test_dashboard_colour_reasons import _seed_every_site
from tests.contracts.test_dashboard_ps20_trust import _ago, _eval_round, _stats, _strip
from tests.contracts.test_dashboard_tenant_prose import SLUG as FULL
from tests.contracts.test_dashboard_tenant_prose import _full_fixture
from tests.test_email_campaign_dashboard import _seed, _seed_operational


def _groups(html):
    return re.findall(
        r'<details class="ops-group" data-section="([\w-]+)"( open)?><summary>([^<]+)</summary>',
        html,
    )


def test_eight_groups_in_order_each_holding_only_its_own_blocks(tmp_path):
    html = vops._ops_view(gd.build_model(_seed_every_site(tmp_path), tmp_path))
    assert [(g, s) for g, _o, s in _groups(html)] == [(g, s) for g, s, _b in OPS_GROUPS]
    for gid, _summary, blocks in OPS_GROUPS:
        inner = section_ids(section(html, gid))[1:]
        assert set(inner) <= set(blocks), (gid, inner)


def test_numbers_that_need_a_look_opens_exactly_when_it_has_content(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [{"id": "S1", "sent": 0}]})
    clean = {g: o for g, o, _s in _groups(vops._ops_view(gd.build_model(profile, tmp_path)))}
    assert clean["numbers"] == ""
    rows = [{"id": "S1", "sent": 0}, {"id": "GHOST", "sent": 0}]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    html = vops._ops_view(gd.build_model(profile, tmp_path))
    assert {g: o for g, o, _s in _groups(html)}["numbers"] == " open"
    assert "GHOST" in section(html, "reconciliation")


def test_the_numbers_name_what_disagrees(tmp_path):
    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c2.campaign.toml").write_text(
        'slug = "c2"\ntitle = "Campaign Two"\nsequences = ["S1"]\n', encoding="utf-8"
    )
    history = pc._prospects_dir(profile, tmp_path).parent / "history.jsonl"
    history.write_text(
        json.dumps(
            {
                "event": "sequence_staged",
                "sequence_id": "ORPH",
                "campaign": "",
                "ts": "2026-09-20T00:00:00Z",
                "enrolled": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    html = vops._ops_view(gd.build_model(profile, tmp_path))
    shared = visible_text(section(html, "shared-sequences"))
    assert "S1" in shared and "Campaign One" in shared and "Campaign Two" in shared
    assert "ORPH" in section(html, "unlinked")
    every = vops._ops_view(gd.build_model(_seed_every_site(tmp_path / "every"), tmp_path / "every"))
    assert "enrolled at the provider" in section(every, "list-vs-provider")


def test_before_sending_starts_holds_the_sent_card_the_re_push_and_the_files_to_load(tmp_path):
    html = vops._ops_view(gd.build_model(_seed_operational(tmp_path), tmp_path))
    before = section(html, "before-sending")
    for sid in ("sent", "re-push", "load-files", "ceiling-tile", "compliance"):
        assert section(before, sid), sid
    assert 'data-figure="ops-heading"' in section(before, "sent")


def test_the_readiness_blocks_render_from_a_hand_built_model():
    m = {
        "campaigns": {
            "campaigns": [{"state": "staged", "targets": {"emails": 10}, "sequences": []}]
        },
        "status": {"sequences": []},
        "messages": [
            {"sequence_id": "seq1", "lint": {"drift": ["body changed"]}},
            {"sequence_id": "seq1", "lint": {"drift": ["body changed"]}},
            {"sequence_id": "seq2", "lint": {"drift": []}},
        ],
    }
    blocks = views_ready._readiness_blocks(m)  # 2.1b's carve; `views_ops` imports it
    assert "<strong>1 of 2 sequences</strong> were revised" in blocks["re-push"]
    assert vops._readiness_blocks is views_ready._readiness_blocks  # one copy, not two


def test_list_quality_keeps_the_pool_on_the_rollup_and_the_roster_on_a_campaign_page(tmp_path):
    profile = _full_fixture(tmp_path)
    rollup = vops._ops_view(gd.build_model(profile, tmp_path))
    assert "people we will actually email" in section(rollup, "pool")
    scoped = vops._ops_view(gd.scope_to_campaign(gd.build_model(profile, tmp_path), FULL))
    assert section(scoped, "pool") == ""
    assert "Where the 2 judged rows go" in visible_text(section(scoped, "roster-notes"))
    assert section(scoped, "needs-address") and section(scoped, "finding-new-people")


def test_the_email_quality_detail_carries_no_second_state_badge(tmp_path):
    html = vops._ops_view(gd.build_model(_seed_every_site(tmp_path), tmp_path))
    detail = section(html, "checks-detail")
    assert "What is checked" in detail
    assert 'data-risk="re-push"' not in detail and 'data-risk="blocking-check"' not in detail


def test_maintenance_is_its_own_group(tmp_path):
    profile = _seed(tmp_path)
    maint = section(vops._ops_view(gd.build_model(profile, tmp_path)), "maintenance")
    assert "Nothing to maintain." in maint
    _eval_round(tmp_path, profile)
    lines = section(vops._ops_view(gd.build_model(profile, tmp_path)), "maintenance-lines")
    assert "labeler-2026-09-22" in lines and "1 pre-filled for you to correct and 1 blank" in lines


def test_shared_repeated_and_unlinked_sequences_agree_with_strip(tmp_path):
    """The strip and Operator notes must never disagree about shared, repeated, or unlinked sequences."""
    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c2.campaign.toml").write_text(
        'slug = "c2"\ntitle = "Campaign Two"\nsequences = ["S1"]\n', encoding="utf-8"
    )
    (camp / "c3.campaign.toml").write_text(
        'slug = "c3"\ntitle = "Campaign Three"\nsequences = ["S3"]\n', encoding="utf-8"
    )
    history = pc._prospects_dir(profile, tmp_path).parent / "history.jsonl"
    history.write_text(
        json.dumps(
            {
                "event": "sequence_staged",
                "sequence_id": "ORPH",
                "campaign": "",
                "ts": "2026-09-20T00:00:00Z",
                "enrolled": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = [
        {"id": "S1", "sent": 5},
        {"id": "S3", "sent": 2},
        {"id": "S3", "sent": 2},
        {"id": "ORPH", "name": "Orphan Sequence", "sent": 0},
    ]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    m = gd.build_model(profile, tmp_path)
    html = gd.render_html(m)
    ops_html = vops._ops_view(m)

    strip_text = visible_text(_strip(html))
    shared_text = visible_text(section(ops_html, "shared-sequences"))
    unlinked_sec = section(ops_html, "unlinked")

    assert "S1" in shared_text and "Campaign One" in shared_text and "Campaign Two" in shared_text
    assert "Campaign One" in strip_text and "Campaign Two" in strip_text

    assert "S3" in shared_text and "summed once per row" in shared_text
    assert "Campaign Three" in strip_text

    assert "ORPH" in unlinked_sec and "Orphan Sequence" in unlinked_sec


def test_sequence_names_and_ids_are_escaped(tmp_path):
    """Check escaping of sequence names and ids across Operator notes."""
    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c2.campaign.toml").write_text(
        'slug = "c2"\ntitle = "Campaign <Two>"\nsequences = ["seq<1>"]\n', encoding="utf-8"
    )
    history = pc._prospects_dir(profile, tmp_path).parent / "history.jsonl"
    history.write_text(
        json.dumps(
            {
                "event": "sequence_staged",
                "sequence_id": "orph<seq>",
                "campaign": "",
                "ts": "2026-09-20T00:00:00Z",
                "enrolled": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = [
        {"id": "seq<1>", "name": "<Snap & Name>", "sent": 0, "loaded": 2},
        {"id": "orph<seq>", "name": "<Orphan & Name>", "sent": 0, "loaded": 0},
    ]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    m = gd.build_model(profile, tmp_path)
    html = vops._ops_view(m)

    unlinked_sec = section(html, "unlinked")
    assert "orph&lt;seq&gt;" in unlinked_sec
    assert "&lt;Orphan &amp; Name&gt;" in unlinked_sec
    assert "<Orphan" not in unlinked_sec

    seq_table = section(html, "sequence-table")
    assert "&lt;Snap &amp; Name&gt;" in seq_table
    assert "<Snap" not in seq_table
    assert 'data-figure="progress-seq&lt;1&gt;"' in seq_table

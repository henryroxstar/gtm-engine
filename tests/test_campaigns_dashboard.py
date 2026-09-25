"""Tests for gtm_core.campaigns_dashboard — the campaigns model behind the
CRO-facing portfolio page (rendered by gtm_core.email_campaign_dashboard)."""

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


def test_consolidate_auto_refreshes_the_merged_page(tmp_path):
    """Consolidation refreshes ONE page now. The two former pages became redirects when
    they merged, so refreshing them separately would re-create the drift the merge fixed."""
    from gtm_core import email_campaign_dashboard as gd

    profile = "acme"
    _write_manifest(tmp_path, profile, "c1", 'slug = "c1"\ntitle = "Campaign One"\n')
    pc.consolidate(profile, content_root=tmp_path)

    merged = gd.dashboard_path(profile, content_root=tmp_path)
    assert merged.exists()
    assert "Campaign One" in merged.read_text(encoding="utf-8")

    retired = pc._prospects_dir(profile, tmp_path).parent / "campaigns.html"
    assert "url=email_campaign_status.html" in retired.read_text(encoding="utf-8")


def test_staged_sequences_reads_batch_staging_and_single_deletion_verbs(tmp_path):
    """The lifecycle vocabulary grew past ``sequence_staged``/``sequence_cleanup``.

    A reader that knows only the original pair does not fail loudly when a sequence
    is staged under a newer verb — it renders a confident page describing sequences
    that no longer exist while omitting every one that does. That is what
    ``campaigns.html`` did between 17 and 18 Aug 2026, so each verb is pinned here.
    """
    profile = "acme"
    _write_history(
        tmp_path,
        profile,
        [
            # Original single-sequence shape, later cleaned up.
            {
                "event": "sequence_staged",
                "sequence_id": "old1",
                "prospects_enrolled": 10,
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "event": "sequence_cleanup",
                "sequences_deleted": [{"id": "old1"}],
                "ts": "2026-01-02T00:00:00Z",
            },
            # Batch staging: both verbs carry a "new_sequences" list, not a sequence_id.
            {
                "event": "sequence_rebuilt",
                "new_sequences": [{"id": "reb1", "enrolled": 292}, {"id": "reb2", "enrolled": 161}],
                "ts": "2026-01-03T00:00:00Z",
            },
            {
                "event": "sequence_seat_split",
                "new_sequences": [{"id": "seat1", "enrolled": 99, "step_ids": ["a", "b", "c"]}],
                "ts": "2026-01-04T00:00:00Z",
            },
            # Single-id deletion, the counterpart to the batch verbs.
            {"event": "sequence_deleted", "sequence_id": "reb1", "ts": "2026-01-05T00:00:00Z"},
        ],
    )

    staged = cd._staged_sequences(profile, content_root=tmp_path)
    by_id = {s["sequence_id"]: s for s in staged}

    assert set(by_id) == {"reb2", "seat1"}, (
        "batch-staged sequences must survive, deleted ones must not"
    )
    assert by_id["reb2"]["enrolled"] == 161
    # step_ids is the seat-split shape's only record of touch count.
    assert by_id["seat1"]["steps"] == 3


def test_staged_sequences_applies_events_in_ledger_order(tmp_path):
    """A sequence re-staged after a deletion is live again.

    Collecting deletions into a set and subtracting them at the end would drop it,
    because the ledger is append-only and the re-staging comes after.
    """
    profile = "acme"
    _write_history(
        tmp_path,
        profile,
        [
            {
                "event": "sequence_staged",
                "sequence_id": "s1",
                "leads_enrolled": 5,
                "ts": "2026-01-01T00:00:00Z",
            },
            {"event": "sequence_deleted", "sequence_id": "s1", "ts": "2026-01-02T00:00:00Z"},
            {
                "event": "sequence_staged",
                "sequence_id": "s1",
                "leads_enrolled": 40,
                "ts": "2026-01-03T00:00:00Z",
            },
        ],
    )

    staged = cd._staged_sequences(profile, content_root=tmp_path)
    assert [s["sequence_id"] for s in staged] == ["s1"]
    assert staged[0]["enrolled"] == 40


def test_archived_sequences_are_excluded_from_headline_numbers(tmp_path):
    """A retired run's sends measure a campaign that no longer exists.

    Folding them into "this campaign" is what made the live page report 24 emails sent
    for a campaign whose every sequence had sent none.
    """
    profile = "acme"
    _write_manifest(
        tmp_path,
        profile,
        "c1",
        """
slug = "c1"
title = "Campaign One"
sequences = ["NEW1"]
archived_sequences = ["OLD1"]

[targets]
emails = 900
""",
    )
    _write_stats(
        tmp_path,
        profile,
        [
            {"id": "NEW1", "name": "current", "status": "paused", "sent": 0, "replied": 0},
            {
                "id": "OLD1",
                "name": "retired",
                "status": "paused",
                "sent": 24,
                "replied": 1,
                "loaded": 30,
            },
        ],
    )

    c = cd.build_campaigns(profile, content_root=tmp_path)["campaigns"][0]
    assert [s["sequence_id"] for s in c["sequences"]] == ["NEW1"]
    assert [s["sequence_id"] for s in c["archived"]] == ["OLD1"]
    assert c["actuals"]["sent"] == 0
    assert c["archived_actuals"]["sent"] == 24
    assert c["archived_actuals"]["loaded"] == 30
    # An archived id must not also surface as "unlinked" — it is linked, just retired.
    assert cd.build_campaigns(profile, content_root=tmp_path)["unlinked_sequences"] == []


def test_build_campaigns_totals_carry_reply_labels_current_only(tmp_path):
    """PS20 T3.1 — the reply labels the snapshot already carries land in a campaign's
    ``actuals``, summed only over its CURRENT rows; an archived row's labels land in
    ``archived_totals`` instead, exactly like ``sent``."""
    profile = "acme"
    _write_manifest(
        tmp_path,
        profile,
        "c1",
        'slug = "c1"\nsequences = ["NEW1"]\narchived_sequences = ["OLD1"]\n',
    )
    _write_stats(
        tmp_path,
        profile,
        [
            {
                "id": "NEW1",
                "sent": 10,
                "interested": 2,
                "not_interested": 1,
                "not_now": 1,
                "out_of_office": 0,
                "unsubscribed": 1,
                "do_not_contact": 0,
            },
            {
                "id": "OLD1",
                "sent": 24,
                "interested": 5,
                "not_interested": 2,
                "not_now": 0,
                "out_of_office": 1,
                "unsubscribed": 0,
                "do_not_contact": 1,
            },
        ],
    )
    c = cd.build_campaigns(profile, content_root=tmp_path)["campaigns"][0]
    assert c["actuals"]["interested"] == 2
    assert c["actuals"]["not_interested"] == 1
    assert c["actuals"]["not_now"] == 1
    assert c["actuals"]["out_of_office"] == 0
    assert c["actuals"]["unsubscribed"] == 1
    assert c["actuals"]["do_not_contact"] == 0
    assert c["archived_actuals"]["interested"] == 5
    assert c["archived_actuals"]["not_interested"] == 2
    assert c["archived_actuals"]["out_of_office"] == 1
    assert c["archived_actuals"]["do_not_contact"] == 1


def test_build_campaigns_totals_zero_an_absent_label_field(tmp_path):
    """A row that carries none of the label fields contributes 0, never a KeyError — a
    row with no live entry at all (``live_by_id.get(sid, {})``) is the same shape as the
    ledger-synthesised rows ``model.py`` builds for a campaign-scoped page."""
    profile = "acme"
    _write_manifest(tmp_path, profile, "c1", 'slug = "c1"\nsequences = ["S1"]\n')
    _write_stats(tmp_path, profile, [{"id": "S1", "sent": 5}])
    c = cd.build_campaigns(profile, content_root=tmp_path)["campaigns"][0]
    for k in (
        "interested",
        "not_interested",
        "not_now",
        "out_of_office",
        "unsubscribed",
        "do_not_contact",
    ):
        assert c["actuals"][k] == 0
    assert c["actuals"]["bounced"] == 0
    assert c["actuals"]["delivered"] == 0
    assert c["actuals"]["bounce_unavailable"] == 1  # no live entry -> not the "emails" source


def test_build_campaigns_bounces_sum_only_over_the_emails_source(tmp_path):
    """PS20 T3.1/T3.6 — ``bounced``/``delivered`` are an EMAIL count, a different unit
    from ``sent`` (people), and only the per-email status block gives one that unit. A
    row without it (``bounce_source`` "prospects", the prospect-level fallback) is counted
    in ``bounce_unavailable`` instead of mixed into the sum — for either side."""
    profile = "acme"
    _write_manifest(
        tmp_path,
        profile,
        "c1",
        'slug = "c1"\nsequences = ["S1", "S2"]\narchived_sequences = ["S3"]\n',
    )
    _write_stats(
        tmp_path,
        profile,
        [
            {
                "sequenceId": "S1",
                "prospects": [{"total": "10", "contacted": "10", "bounced": "9"}],
                "emails": {"status": {"delivered": "95", "bounced": "5"}},
            },
            {
                "sequenceId": "S2",
                # no "emails" block -> bounce_source "prospects", not summed as bounced/delivered
                "prospects": [{"total": "10", "contacted": "10", "bounced": "3"}],
            },
            {
                "sequenceId": "S3",
                "prospects": [{"total": "10", "contacted": "10"}],
                "emails": {"status": {"delivered": "40", "bounced": "2"}},
            },
        ],
    )
    c = cd.build_campaigns(profile, content_root=tmp_path)["campaigns"][0]
    assert c["actuals"]["bounced"] == 5
    assert c["actuals"]["delivered"] == 95
    assert c["actuals"]["bounce_unavailable"] == 1
    assert c["archived_actuals"]["bounced"] == 2
    assert c["archived_actuals"]["delivered"] == 40
    assert c["archived_actuals"]["bounce_unavailable"] == 0


def test_state_follows_the_snapshot_status(tmp_path):
    profile = "acme"
    _write_manifest(
        tmp_path, profile, "c1", 'slug = "c1"\nsequences = ["S1"]\n\n[targets]\nemails = 900\n'
    )
    _write_stats(tmp_path, profile, [{"id": "S1", "name": "s", "status": "paused", "sent": 0}])
    c = cd.build_campaigns(profile, content_root=tmp_path)["campaigns"][0]
    assert c["state"] == "paused"

    _write_stats(tmp_path, profile, [{"id": "S1", "name": "s", "status": "active", "sent": 10}])
    c = cd.build_campaigns(profile, content_root=tmp_path)["campaigns"][0]
    assert c["state"] == "active"


MANIFEST = 'slug = "c1"\ntitle = "C1"\nsequences = ["S1"]\n'


def test_state_is_the_go_live_word(tmp_path):
    _write_manifest(tmp_path, "acme", "c1", MANIFEST)
    _write_stats(tmp_path, "acme", [{"id": "S1", "sent": 0, "loaded": 4}])
    c = cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]
    assert (c["state"], c["actuals"]["loaded"]) == ("staged", 4)
    _write_stats(tmp_path, "acme", [{"id": "S1", "sent": 3, "loaded": 4}])
    assert cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]["state"] == "started"
    _write_stats(tmp_path, "acme", [{"id": "S1", "status": "active", "sent": 3}])
    assert cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]["state"] == "active"


def test_state_is_unknown_under_an_unreadable_snapshot(tmp_path):
    _write_manifest(tmp_path, "acme", "c1", MANIFEST)
    _write_stats(tmp_path, "acme", [])
    (pc._pool_dir("acme", tmp_path) / "sequence-stats.json").write_text("{broken")
    assert cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]["state"] == "unknown"


def test_ledger_paused_status_is_ignored(tmp_path):
    """The ledger's staged event says "paused" by construction; only the snapshot counts."""
    _write_manifest(tmp_path, "acme", "c1", MANIFEST)
    _write_history(
        tmp_path,
        "acme",
        [{"event": "sequence_staged", "sequence_id": "S1", "campaign": "c1", "status": "paused"}],
    )
    _write_stats(tmp_path, "acme", [{"id": "S1", "sent": 2}])  # the snapshot carries no status
    assert cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]["state"] == "started"


def test_archived_rows_are_kept_out_of_the_current_go_live(tmp_path):
    """An archived sequence's status/sends must never leak into the CURRENT state — an
    archived "paused" must not mask a current start, and an archived send must not fake
    a current contact."""
    _write_manifest(
        tmp_path,
        "acme",
        "c1",
        'slug = "c1"\nsequences = ["NEW1"]\narchived_sequences = ["OLD1"]\n',
    )
    _write_stats(
        tmp_path,
        "acme",
        [{"id": "NEW1", "sent": 3}, {"id": "OLD1", "status": "paused", "sent": 24}],
    )
    c = cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]
    assert c["state"] == "started"  # archived statuses must not mask a current start

    _write_stats(tmp_path, "acme", [{"id": "NEW1", "sent": 0}, {"id": "OLD1", "sent": 24}])
    c = cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]
    assert c["state"] == "staged"  # archived sends are not current contacts


def test_on_record_excludes_archived_only_sequences(tmp_path):
    """A sequence can reach ``seq_ids`` via its staged-event campaign tag even after the
    manifest archives it (the ledger event predates the archive). If that is the ONLY
    sequence, nothing CURRENT is on record — the campaign must read "none", not "staged"."""
    _write_manifest(tmp_path, "acme", "c1", 'slug = "c1"\narchived_sequences = ["OLD1"]\n')
    _write_history(
        tmp_path, "acme", [{"event": "sequence_staged", "sequence_id": "OLD1", "campaign": "c1"}]
    )
    _write_stats(tmp_path, "acme", [{"id": "OLD1", "sent": 24}])
    c = cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]
    assert c["state"] == "none"


def test_state_is_none_for_a_campaign_with_no_sequences(tmp_path):
    _write_manifest(tmp_path, "acme", "c1", 'slug = "c1"\n')
    c = cd.build_campaigns("acme", content_root=tmp_path)["campaigns"][0]
    assert c["state"] == "none"


def test_dead_renderer_is_gone():
    import gtm_core.campaigns_dashboard as cd

    for name in ("render_html", "render_dashboard", "dashboard_path", "_cli"):
        assert not hasattr(cd, name), name
    assert callable(cd.build_campaigns) and callable(cd._load_manifests)
    assert callable(cd._experiment_block) and callable(cd._campaigns_dir)

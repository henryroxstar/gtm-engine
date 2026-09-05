"""Tests for the Phase 4 closed loop: gtm_core.outcomes (capture) + gtm_core.gtm_distill (learnings).

Deterministic, files-only. Proves the loop: append outcomes → summarize by tag → distill a learnings
note with a Promote? section → optionally stage a learnings candidate for the Phase-3 promote flow.
"""

from __future__ import annotations

import json
from datetime import date

from gtm_core import gtm_distill as gd
from gtm_core import knowledge_staging as ks
from gtm_core import outcomes as oc

# aggregate rows (value = count): tag A outperforms, tag B underperforms the 20% baseline
ROWS = [
    {"channel": "email", "outcome": "sent", "value": 10, "tags": ["myth-bust"]},
    {"channel": "email", "outcome": "reply", "value": 3, "tags": ["myth-bust"]},
    {"channel": "email", "outcome": "sent", "value": 10, "tags": ["feature-list"]},
    {"channel": "email", "outcome": "reply", "value": 1, "tags": ["feature-list"]},
]


def _seed(content_root, profile="acme", rows=ROWS):
    for r in rows:
        oc.append_outcome(content_root, profile, r, now="2026-06-15T00:00:00Z")


# --- capture ------------------------------------------------------------------


def test_append_and_read_roundtrip(tmp_path):
    oc.append_outcome(tmp_path, "acme", {"channel": "email", "outcome": "reply"})
    oc.append_outcome(
        tmp_path, "acme", {"channel": "email", "outcome": "sent"}, now="2026-01-02T00:00:00Z"
    )
    rows = oc.read_outcomes(tmp_path, "acme")
    assert len(rows) == 2
    assert all("ts" in r for r in rows)  # auto-stamped
    assert oc.read_outcomes(tmp_path, "acme", since_month="2026-01") == [rows[1]]


class TestTheWeekWindow:
    """A month window cannot answer a mid-week question.

    PRD 2026-08-27 §3.7 puts an outcomes read on Wednesday — the day an AE kills or
    boosts an angle — because nothing sat between Monday's run and Saturday's loop.
    ``--month`` on a Wednesday is month-to-date, which is a different read: early in a
    month it is three days, late in one it is four weeks.
    """

    def _seed_span(self, tmp_path):
        for day, outcome in (("2026-08-01", "sent"), ("2026-08-26", "reply")):
            oc.append_outcome(
                tmp_path, "acme", {"channel": "email", "outcome": outcome}, now=f"{day}T00:00:00Z"
            )

    def test_since_selects_a_window_narrower_than_a_month(self, tmp_path):
        self._seed_span(tmp_path)
        rows = oc.read_outcomes(tmp_path, "acme", since="2026-08-20")
        assert [r["outcome"] for r in rows] == ["reply"]

    def test_since_is_inclusive_of_its_own_day(self, tmp_path):
        self._seed_span(tmp_path)
        assert len(oc.read_outcomes(tmp_path, "acme", since="2026-08-26")) == 1

    def test_since_and_since_month_compose(self, tmp_path):
        """Both filters apply; neither silently wins."""
        self._seed_span(tmp_path)
        assert oc.read_outcomes(tmp_path, "acme", since_month="2026-07", since="2026-08-20") == []

    def test_no_window_still_reads_everything(self, tmp_path):
        self._seed_span(tmp_path)
        assert len(oc.read_outcomes(tmp_path, "acme")) == 2

    def test_the_cli_days_flag_resolves_to_a_since_date(self, tmp_path, capsys):
        self._seed_span(tmp_path)
        rc = oc.main(
            [
                "summary",
                "--profile",
                "acme",
                "--days",
                "7",
                "--json",
                "--content-root",
                str(tmp_path),
                "--as-of",
                "2026-08-28",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        # The 2026-08-01 `sent` row is outside a 7-day window ending 2026-08-28.
        assert payload["acme"]["totals"]["sent"] == 0
        assert payload["acme"]["totals"]["replies"] == 1


def test_read_skips_malformed_lines(tmp_path):
    p = oc.outcomes_path(tmp_path, "acme")
    p.parent.mkdir(parents=True)
    p.write_text('{"channel":"email","outcome":"reply"}\nnot json\n\n', encoding="utf-8")
    assert len(oc.read_outcomes(tmp_path, "acme")) == 1


# --- rate-append guard (F12) ---------------------------------------------------


def test_append_outcome_rejects_a_known_derived_rate_name(tmp_path):
    import pytest

    for name in oc.RATE_OUTCOME_NAMES:
        with pytest.raises(ValueError, match="derived rate"):
            oc.append_outcome(
                tmp_path, "acme", {"channel": "linkedin", "outcome": name, "value": 3.2}
            )
    # Nothing was written — the whole point is refusing before it lands.
    assert oc.read_outcomes(tmp_path, "acme") == []


def test_append_outcome_rejects_any_name_ending_rate(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="derived rate"):
        oc.append_outcome(tmp_path, "acme", {"channel": "x", "outcome": "some_future_rate"})


def test_append_outcome_still_accepts_ordinary_counts(tmp_path):
    """The guard must not over-fire — plain count names keep working."""
    oc.append_outcome(
        tmp_path, "acme", {"channel": "linkedin", "outcome": "impressions", "value": 100}
    )
    assert len(oc.read_outcomes(tmp_path, "acme")) == 1


def test_a_preexisting_rate_named_row_still_reads_and_summarizes(tmp_path):
    """The guard is at the WRITE path only — an already-written row (from before
    this guard existed) must still read back and summarize without raising."""
    p = oc.outcomes_path(tmp_path, "acme")
    p.parent.mkdir(parents=True)
    p.write_text(
        '{"channel":"linkedin","outcome":"engagement_rate","value":3.2,"ts":"2026-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    rows = oc.read_outcomes(tmp_path, "acme")
    assert len(rows) == 1
    oc.summarize(rows)  # must not raise


def test_cli_append_rejects_a_rate_name_with_exit_2(tmp_path, capsys):
    rc = oc.main(
        [
            "append",
            "--profile",
            "acme",
            "--channel",
            "linkedin",
            "--outcome",
            "engagement_rate",
            "--value",
            "3.2",
            "--content-root",
            str(tmp_path),
        ]
    )
    assert rc == 2
    assert "derived rate" in capsys.readouterr().out


def test_cli_append_flag_shape_round_trips(tmp_path, capsys):
    """Pins the CLI's real invocation shape (--channel/--outcome/--tag..., no
    --json) — content-outcomes-sync's body_template.md documents exactly this."""
    rc = oc.main(
        [
            "append",
            "--profile",
            "acme",
            "--channel",
            "linkedin",
            "--outcome",
            "impressions",
            "--ref",
            "post-1",
            "--value",
            "10",
            "--tag",
            "pillar-x",
            "--tag",
            "journey-y",
            "--content-root",
            str(tmp_path),
        ]
    )
    assert rc == 0
    rows = oc.read_outcomes(tmp_path, "acme")
    assert len(rows) == 1
    assert rows[0]["tags"] == ["pillar-x", "journey-y"]
    assert rows[0]["ref"] == "post-1"


def test_cli_append_with_meta(tmp_path):
    """Phase 9 (§5.8): the predictor's exact sub-scores ride in meta, alongside the
    banded predictor_band:<band> tag — meta is context, not a queryable correlation axis."""
    rc = oc.main(
        [
            "append",
            "--profile",
            "acme",
            "--channel",
            "instagram",
            "--outcome",
            "impressions",
            "--value",
            "500",
            "--tag",
            "predictor_band:medium",
            "--meta",
            '{"virality_index": 62, "hook_strength": 58}',
            "--content-root",
            str(tmp_path),
        ]
    )
    assert rc == 0
    rows = oc.read_outcomes(tmp_path, "acme")
    assert rows[0]["meta"] == {"virality_index": 62, "hook_strength": 58}
    assert rows[0]["tags"] == ["predictor_band:medium"]


def test_cli_append_meta_must_be_a_json_object(tmp_path):
    rc = oc.main(
        [
            "append",
            "--profile",
            "acme",
            "--channel",
            "x",
            "--outcome",
            "clicks",
            "--meta",
            "not json",
            "--content-root",
            str(tmp_path),
        ]
    )
    assert rc == 2
    rc2 = oc.main(
        [
            "append",
            "--profile",
            "acme",
            "--channel",
            "x",
            "--outcome",
            "clicks",
            "--meta",
            "[1, 2, 3]",
            "--content-root",
            str(tmp_path),
        ]
    )
    assert rc2 == 2


# --- summarize ----------------------------------------------------------------


def test_summarize_rates_by_tag(tmp_path):
    s = oc.summarize(ROWS)
    assert s["totals"]["sent"] == 20 and s["totals"]["replies"] == 4
    assert s["totals"]["reply_rate"] == 0.2
    assert s["by_tag"]["myth-bust"]["reply_rate"] == 0.3
    assert s["by_tag"]["feature-list"]["reply_rate"] == 0.1


def test_promote_candidates_flags_out_and_under_performers():
    cands = {c["tag"]: c["direction"] for c in gd.promote_candidates(oc.summarize(ROWS))}
    assert cands == {"myth-bust": "outperforms", "feature-list": "underperforms"}


def test_promote_candidates_need_enough_observations():
    thin = [
        {"channel": "email", "outcome": "sent", "value": 2, "tags": ["x"]},
        {"channel": "email", "outcome": "reply", "value": 2, "tags": ["x"]},
    ]
    assert gd.promote_candidates(oc.summarize(thin)) == []  # below DEFAULT_MIN_SENT


# --- distill ------------------------------------------------------------------


def test_distill_writes_note_with_promote_section(tmp_path):
    _seed(tmp_path)
    path = gd.distill(tmp_path, "acme", period="2026-06", today=date(2026, 6, 30))
    assert path == tmp_path / "acme" / "learnings" / "2026-06.md"
    text = path.read_text()
    assert "## Promote?" in text
    assert "myth-bust" in text and "outperforms" in text
    assert "feature-list" in text and "underperforms" in text


def test_distill_stage_option_stages_learnings_candidate(tmp_path):
    _seed(tmp_path)
    gd.distill(tmp_path, "acme", period="2026-06", today=date(2026, 6, 30), stage=True)
    assert "learnings" in ks.list_staged(tmp_path, "acme")


def test_distill_empty_is_quiet(tmp_path):
    (tmp_path / "acme").mkdir(parents=True)
    path = gd.distill(tmp_path, "acme", period="2026-06", today=date(2026, 6, 30))
    assert "No signal yet" in path.read_text()


def test_outcomes_loop_pack_wiring():
    from pathlib import Path

    from gtm_core.packs.loader import load_pack_graph
    from gtm_core.skills.registry import all_skills

    repo = Path(__file__).resolve().parents[1]
    graph = load_pack_graph(repo / "packs" / "outcomes-loop" / "graphs" / "outcomes-loop.toml")
    node = graph.nodes[0]
    assert node.skill == "outcomes-sync"
    # read-only loop: no gate, no external effect (it never sends/publishes)
    assert node.gate is False and node.external_effect is None
    assert node.model_role == "brain_plan"  # outcome rows carry account PII → stays on Claude
    assert "outcomes-sync" in {s.name for s in all_skills()}


# --- content-performance buckets (PRD 2026-08-14 §5.4) -------------------------
# The same ledger and the same tag correlation now serve published content as well as outreach.
# Content is rate-bearing against IMPRESSIONS, outreach against SENT — these must not mix.

CONTENT_ROWS = [
    {
        "channel": "linkedin",
        "outcome": "impressions",
        "ref": "p1",
        "value": 1000,
        "tags": ["rhythm-pillar", "awareness", "reach", "carousel"],
    },
    {
        "channel": "linkedin",
        "outcome": "reactions",
        "ref": "p1",
        "value": 40,
        "tags": ["rhythm-pillar", "awareness", "reach", "carousel"],
    },
    {
        "channel": "linkedin",
        "outcome": "comments",
        "ref": "p1",
        "value": 10,
        "tags": ["rhythm-pillar", "awareness", "reach", "carousel"],
    },
    {
        "channel": "linkedin",
        "outcome": "clicks",
        "ref": "p1",
        "value": 25,
        "tags": ["rhythm-pillar", "awareness", "reach", "carousel"],
    },
]


def test_content_rates_derive_from_impressions():
    s = oc.summarize(CONTENT_ROWS)
    b = s["by_channel"]["linkedin"]
    assert b["impressions"] == 1000
    assert b["content_engagements"] == 50  # reactions + comments
    assert b["engagement_rate"] == 0.05
    assert b["click_rate"] == 0.025


def test_content_tags_are_the_learning_axis():
    s = oc.summarize(CONTENT_ROWS)
    assert s["by_tag"]["carousel"]["engagement_rate"] == 0.05
    assert s["by_tag"]["reach"]["engagement_rate"] == 0.05


def test_engagement_without_impressions_is_unrateable_not_zero():
    """A missing denominator must read as 'unknown', never as 0% — that difference decides
    whether a hook looks like a failure or like an unmeasured one."""
    s = oc.summarize([{"channel": "x", "outcome": "reactions", "value": 12, "tags": ["t"]}])
    b = s["by_channel"]["x"]
    assert b["content_engagements"] == 12
    assert b["engagement_rate"] is None
    assert b["click_rate"] is None


def test_sales_rows_are_unaffected_by_the_content_buckets():
    """Regression: outreach rates must not pick up content counters, or vice versa."""
    s = oc.summarize(ROWS)
    assert s["totals"]["reply_rate"] == 0.2  # unchanged from the pre-existing test
    assert s["totals"]["impressions"] == 0
    assert s["totals"]["engagement_rate"] is None


def test_mixed_sales_and_content_rows_keep_separate_denominators():
    s = oc.summarize(ROWS + CONTENT_ROWS)
    assert s["totals"]["reply_rate"] == 0.2  # still sent-based
    assert s["totals"]["engagement_rate"] == 0.05  # impression-based


# --- attribute / unattributed (video-finish → outcomes.jsonl, PRD Phase B) ----------------


def _write_finish(
    repo_root,
    content_root,
    *,
    profile="acme",
    slug="slug1",
    ratio="9x16",
    body=b"video-bytes",
    executed=True,
    plan_id="plan-abc",
):
    from gtm_core.render_manifest import FinishManifest, write_finish_manifest

    video_dir = content_root / profile / "video" / slug
    rel_asset = f"content/{profile}/video/{slug}/{slug}-{ratio}-final.mp4"
    if executed:
        asset_abs = repo_root / rel_asset
        asset_abs.parent.mkdir(parents=True, exist_ok=True)
        asset_abs.write_bytes(body)
    fm = FinishManifest(
        profile=profile,
        slug=slug,
        ratio=ratio,
        asset_path=rel_asset,
        stages=("normalize", "captions", "encode"),
        census={"grade": 1, "overlay": 3},
        executed=executed,
        plan_id=plan_id,
        caption_route="local",
    )
    return write_finish_manifest(fm, out_dir=video_dir, repo_root=repo_root)


def test_attribute_appends_a_published_row_with_asset_sha256(tmp_path):
    import hashlib

    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root, body=b"the-real-bytes")

    record = oc.attribute(
        content_root, "acme", finish_path=finish_path, repo_root=repo_root, ref="post-1"
    )
    assert record["outcome"] == "published"
    assert record["value"] == 1
    assert record["ref"] == "post-1"
    assert record["meta"]["plan_id"] == "plan-abc"
    assert record["meta"]["census"] == {"grade": 1, "overlay": 3}
    assert record["meta"]["asset_sha256"] == hashlib.sha256(b"the-real-bytes").hexdigest()

    rows = oc.read_outcomes(content_root, "acme")
    assert len(rows) == 1 and rows[0]["outcome"] == "published"


def test_attribute_refuses_a_duplicate_asset_sha256(tmp_path):
    import pytest

    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)

    oc.attribute(content_root, "acme", finish_path=finish_path, repo_root=repo_root, ref="post-1")
    with pytest.raises(ValueError, match="already attributed"):
        oc.attribute(
            content_root, "acme", finish_path=finish_path, repo_root=repo_root, ref="post-2"
        )
    assert len(oc.read_outcomes(content_root, "acme")) == 1  # the refused call wrote nothing


def test_attribute_pulls_identity_cost_and_duration_from_render_manifest(tmp_path):
    from gtm_core.render_manifest import RenderManifest, write_render_manifest

    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)
    video_dir = content_root / "acme" / "video" / "slug1"
    asset_abs = repo_root / "content/acme/video/slug1/raw.mp4"
    asset_abs.parent.mkdir(parents=True, exist_ok=True)
    asset_abs.write_bytes(b"raw")
    rm = RenderManifest(
        profile="acme",
        slug="slug1",
        ratio="9x16",
        asset_path="content/acme/video/slug1/raw.mp4",
        identity_used=("generated",),
        provider="higgsfield",
        cost_credits=4.0,
        duration_s=15.04,
        # Phase 18: a synthetic asset must carry its verbatim prompt to be writable at all.
        prompt="Static camera, slow push-in. Abstract particles drift upward. No text.",
        # A synthetic asset must also name the storyboard approved at ⟦GATE:plan⟧.
        storyboard_path="storyboard.json",
        cost_source="preflight",
        cost_ledger_ts="2026-08-20T00:00:00Z",
    )
    (repo_root / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    render_path = write_render_manifest(rm, out_dir=video_dir, synthetic=True, repo_root=repo_root)

    record = oc.attribute(
        content_root,
        "acme",
        finish_path=finish_path,
        render_path=render_path,
        repo_root=repo_root,
    )
    assert record["meta"]["identity_used"] == ["generated"]
    assert record["meta"]["provider"] == "higgsfield"
    assert record["meta"]["cost_credits"] == 4.0
    assert record["meta"]["duration_s"] == 15.04


def test_attribute_records_the_would_post_binary(tmp_path):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)
    record = oc.attribute(
        content_root, "acme", finish_path=finish_path, repo_root=repo_root, would_post=False
    )
    assert record["meta"]["would_post"] is False


def test_attribute_adds_predictor_band_tag_from_score_json(tmp_path):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)
    score_path = tmp_path / "score.json"
    score_path.write_text(
        json.dumps(
            {
                "recommended": {
                    "ratio": "9x16",
                    "variant": 2,
                    "band": "high",
                    "band_note": "free text, never promoted into meta",
                }
            }
        )
    )
    record = oc.attribute(
        content_root, "acme", finish_path=finish_path, repo_root=repo_root, score_path=score_path
    )
    assert "predictor_band:high" in record["tags"]
    assert record["meta"]["predictor"] == {"ratio": "9x16", "variant": 2}


def test_attribute_accepts_hook_id_tag(tmp_path):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)
    record = oc.attribute(
        content_root,
        "acme",
        finish_path=finish_path,
        repo_root=repo_root,
        tags=["hook:rhythm-founder-conversation"],
    )
    assert "hook:rhythm-founder-conversation" in record["tags"]
    rows = oc.read_outcomes(content_root, "acme")
    summary = oc.summarize(rows)
    assert "hook:rhythm-founder-conversation" in summary["by_tag"]


def test_summarize_reports_hook_tag_rates(tmp_path):
    rows = [
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "ref": "p1",
            "value": 1000,
            "tags": ["hook:rhythm-founder-conversation"],
        },
        {
            "channel": "linkedin",
            "outcome": "reactions",
            "ref": "p1",
            "value": 60,
            "tags": ["hook:rhythm-founder-conversation"],
        },
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "ref": "p2",
            "value": 1000,
            "tags": ["hook:henry-build-in-public"],
        },
        {
            "channel": "linkedin",
            "outcome": "reactions",
            "ref": "p2",
            "value": 30,
            "tags": ["hook:henry-build-in-public"],
        },
    ]
    s = oc.summarize(rows)
    assert s["by_tag"]["hook:rhythm-founder-conversation"]["engagement_rate"] == 0.06
    assert s["by_tag"]["hook:henry-build-in-public"]["engagement_rate"] == 0.03


def test_promote_candidates_flags_outperforming_hook_tag():
    rows = [
        {"channel": "email", "outcome": "sent", "value": 1000, "tags": ["hook:a"]},
        {"channel": "email", "outcome": "reply", "value": 60, "tags": ["hook:a"]},
        {"channel": "email", "outcome": "sent", "value": 1000, "tags": ["hook:b"]},
        {"channel": "email", "outcome": "reply", "value": 10, "tags": ["hook:b"]},
    ]
    cands = {c["tag"]: c["direction"] for c in gd.promote_candidates(oc.summarize(rows))}
    assert cands == {"hook:a": "outperforms", "hook:b": "underperforms"}


def test_attribute_score_json_with_no_band_adds_no_predictor_tag(tmp_path):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)
    score_path = tmp_path / "score.json"
    score_path.write_text(json.dumps({"recommended": {"ratio": "9x16", "band": None}}))
    record = oc.attribute(
        content_root, "acme", finish_path=finish_path, repo_root=repo_root, score_path=score_path
    )
    assert record["tags"] == []
    assert "predictor" not in record["meta"]


def test_cli_attribute_flag_shape(tmp_path, capsys):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)
    rc = oc.main(
        [
            "attribute",
            "--profile",
            "acme",
            "--finish",
            str(finish_path),
            "--ref",
            "post-9",
            "--would-post",
            "true",
            "--tag",
            "pillar-x",
            "--content-root",
            str(content_root),
            "--repo-root",
            str(repo_root),
        ]
    )
    assert rc == 0
    printed = json.loads(capsys.readouterr().out.strip())
    assert printed["ref"] == "post-9"
    assert printed["meta"]["would_post"] is True
    assert printed["tags"] == ["pillar-x"]


def test_cli_attribute_reports_a_refused_duplicate_with_exit_2(tmp_path, capsys):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)
    argv = [
        "attribute",
        "--profile",
        "acme",
        "--finish",
        str(finish_path),
        "--content-root",
        str(content_root),
        "--repo-root",
        str(repo_root),
    ]
    assert oc.main(argv) == 0
    rc = oc.main(argv)
    assert rc == 2
    assert "already attributed" in capsys.readouterr().out


def test_unattributed_lists_finished_assets_with_no_published_row(tmp_path):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_a = _write_finish(repo_root, content_root, slug="slug-a", plan_id="a", body=b"asset-a")
    _write_finish(repo_root, content_root, slug="slug-b", plan_id="b", body=b"asset-b")

    oc.attribute(content_root, "acme", finish_path=finish_a, repo_root=repo_root)

    gaps = oc.unattributed(content_root, "acme", days=None, repo_root=repo_root)
    assert [g["slug"] for g in gaps] == ["slug-b"]


def test_unattributed_skips_unexecuted_manifests(tmp_path):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    _write_finish(repo_root, content_root, slug="slug-c", executed=False)
    gaps = oc.unattributed(content_root, "acme", days=None, repo_root=repo_root)
    assert gaps == []


def test_unattributed_no_video_dir_is_empty(tmp_path):
    content_root = tmp_path / "content"
    assert oc.unattributed(content_root, "acme", days=None, repo_root=tmp_path) == []


def test_cli_unattributed_json_output(tmp_path, capsys):
    repo_root = tmp_path
    content_root = tmp_path / "content"
    _write_finish(repo_root, content_root, slug="slug-d")
    rc = oc.main(
        [
            "unattributed",
            "--profile",
            "acme",
            "--days",
            "14",
            "--json",
            "--content-root",
            str(content_root),
            "--repo-root",
            str(repo_root),
        ]
    )
    assert rc == 0
    gaps = json.loads(capsys.readouterr().out)
    assert [g["slug"] for g in gaps] == ["slug-d"]


def test_unattributed_recency_window_excludes_an_old_manifest(tmp_path):
    import os
    import time

    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root, slug="slug-old")
    old_ts = time.time() - 30 * 86400  # 30 days ago
    os.utime(finish_path, (old_ts, old_ts))

    assert oc.unattributed(content_root, "acme", days=14, repo_root=repo_root) == []
    gaps = oc.unattributed(content_root, "acme", days=60, repo_root=repo_root)
    assert [g["slug"] for g in gaps] == ["slug-old"]


def test_unknown_metric_names_are_counted_but_not_rate_bearing():
    """A platform-specific metric we have not enumerated must still be recorded, not dropped."""
    s = oc.summarize(
        [
            {"channel": "instagram", "outcome": "impressions", "value": 100},
            {"channel": "instagram", "outcome": "sticker_taps", "value": 7},
        ]
    )
    b = s["by_channel"]["instagram"]
    assert b["counts"]["sticker_taps"] == 7
    assert b["content_engagements"] == 0
    assert b["engagement_rate"] == 0.0


# --- Phase 8 multi-source ingestion -----------------------------------------------------------


def test_append_outcome_accepts_retention_seconds_as_a_count(tmp_path):
    """True retention is a count (seconds), not a derived rate, and must append cleanly."""
    oc.append_outcome(
        tmp_path,
        "acme",
        {"channel": "youtube", "outcome": "retention_seconds", "ref": "v1", "value": 42},
    )
    rows = oc.read_outcomes(tmp_path, "acme")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "retention_seconds"
    assert rows[0]["value"] == 42


def test_append_outcome_preserves_hook_and_predictor_band_tags(tmp_path):
    """Every content row can carry hook_id and predictor_band tags for the calibration loop."""
    oc.append_outcome(
        tmp_path,
        "acme",
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "ref": "post-8",
            "value": 1000,
            "tags": ["hook:acme-augmentation", "predictor_band:high", "pillar:gtm"],
        },
    )
    rows = oc.read_outcomes(tmp_path, "acme")
    summary = oc.summarize(rows)
    assert "hook:acme-augmentation" in summary["by_tag"]
    assert "predictor_band:high" in summary["by_tag"]


def test_cli_append_retention_proxy_meta(tmp_path):
    """When true retention is unavailable, the proxy fact rides in meta, not outcome name."""
    rc = oc.main(
        [
            "append",
            "--profile",
            "acme",
            "--channel",
            "instagram",
            "--outcome",
            "reactions",
            "--ref",
            "post-9",
            "--value",
            "30",
            "--tag",
            "hook:rhythm-founder-conversation",
            "--tag",
            "predictor_band:medium",
            "--meta",
            '{"retention_proxy": true, "confidence": "low"}',
            "--content-root",
            str(tmp_path),
        ]
    )
    assert rc == 0
    rows = oc.read_outcomes(tmp_path, "acme")
    assert rows[0]["meta"] == {"retention_proxy": True, "confidence": "low"}
    assert "hook:rhythm-founder-conversation" in rows[0]["tags"]
    assert "predictor_band:medium" in rows[0]["tags"]


def test_attribute_uses_predictor_band_for_uncertain_band(tmp_path):
    """The band tag uses the predictor_band: prefix even when the band is uncertain."""
    repo_root = tmp_path
    content_root = tmp_path / "content"
    finish_path = _write_finish(repo_root, content_root)
    score_path = tmp_path / "score.json"
    score_path.write_text(json.dumps({"recommended": {"band": "uncertain"}}))
    record = oc.attribute(
        content_root, "acme", finish_path=finish_path, repo_root=repo_root, score_path=score_path
    )
    assert "predictor_band:uncertain" in record["tags"]

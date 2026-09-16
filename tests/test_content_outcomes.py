"""The content-outcomes producer: the missing half of the hook learning loop.

Before ``gtm_core.content_outcomes`` nothing in the repo ever appended a row whose ``outcome``
was in ``IMPRESSION_OUTCOMES``, so ``hook_score``'s prior was structurally dead and no hook
could fatigue. These tests pin the properties that make the producer trustworthy: it attributes
through files rather than guessing, it refuses rather than writing a wrong or zero number, and
the rows it emits are the exact shape ``hook_score._compute_prior`` and ``hooks.is_fatigued``
read back.

All fixtures are fictional (§R9): invented items, hooks and a `.example` permalink host.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import hooks as hooks_mod
from gtm_core.content_outcomes import (
    _cli,
    asset_digest_index,
    item_tags,
    metrics_in,
    plan_rows,
    publish_index,
    read_export,
    row_ref,
)
from gtm_core.hook_score import _compute_prior
from gtm_core.outcomes import read_outcomes
from gtm_core.publish_hash import content_hash

PROFILE = "example"
POST_URL = "https://social.example/posts/1001"
BODY = "A post body that was published verbatim."


@pytest.fixture()
def tree(tmp_path):
    """A content root with one published item, its asset, and its plan row."""
    root = tmp_path / "content"
    base = root / PROFILE
    (base / "assets").mkdir(parents=True)
    (base / "plans").mkdir(parents=True)
    (base / "assets" / "ci-01.asset.json").write_text(
        json.dumps({"body": BODY, "format": "carousel", "platform": "linkedin"}), encoding="utf-8"
    )
    (base / "plans" / "2026-40-plan.json").write_text(
        json.dumps(
            [
                {
                    "id": "ci-01",
                    "pillar": "agent-identity",
                    "journey_stage": "awareness",
                    "goal": "reach",
                    "format": "carousel",
                    "hook_id": "unattributed-agent-action",
                }
            ]
        ),
        encoding="utf-8",
    )
    return root, base


def _history(base, *rows):
    (base / "history.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


# ── export parsing ─────────────────────────────────────────────────────────────────────


def test_reads_csv_and_json_exports_the_same_way(tmp_path):
    csv_path = tmp_path / "e.csv"
    csv_path.write_text(f"post_url,impressions\n{POST_URL},1234\n", encoding="utf-8")
    json_path = tmp_path / "e.json"
    json_path.write_text(
        json.dumps({"posts": [{"post_url": POST_URL, "impressions": 1234}]}), encoding="utf-8"
    )
    for path in (csv_path, json_path):
        rows = read_export(path)
        assert len(rows) == 1
        assert row_ref(rows[0]) == POST_URL
        assert metrics_in(rows[0])[0] == {"impressions": 1234}


def test_thousands_separators_and_header_case_are_handled(tmp_path):
    path = tmp_path / "e.csv"
    path.write_text(f'Post URL,Impressions,Likes\n{POST_URL},"12,004",87\n', encoding="utf-8")
    row = read_export(path)[0]
    assert row_ref(row) == POST_URL
    assert metrics_in(row)[0] == {"impressions": 12004, "reactions": 87}


def test_a_precomputed_rate_column_is_dropped_not_summed():
    """`summarize` SUMS `value`, so appending a percentage corrupts the derived rate."""
    metrics, notes = metrics_in({"post_url": POST_URL, "impressions": 100, "engagement_rate": 3.2})
    assert metrics == {"impressions": 100}
    assert any("dropped rate column" in n for n in notes)


def test_an_unparseable_metric_is_skipped_never_recorded_as_zero():
    metrics, notes = metrics_in({"post_url": POST_URL, "impressions": "—", "likes": 4})
    assert metrics == {"reactions": 4}
    assert any("not written as 0" in n for n in notes)


def test_an_unrecognised_column_is_ignored_rather_than_guessed_into_a_bucket():
    metrics, _ = metrics_in({"post_url": POST_URL, "impressions": 10, "mystery_metric": 999})
    assert metrics == {"impressions": 10}


# ── the attribution chain ──────────────────────────────────────────────────────────────


def test_manual_publish_row_joins_by_ref(tree):
    root, base = tree
    _history(base, {"event": "published", "item_id": "ci-01", "ref": POST_URL})
    assert publish_index(_rows(base), {}) == {POST_URL: "ci-01"}


def test_automated_publish_row_joins_by_recomputed_content_digest(tree):
    """`agent/publish_dispatch` records post_id + content_sha256 but NO item_id. The item is
    recovered by recomputing the asset's digest — which is why this producer needs no change
    to the approval-bound publish path."""
    root, base = tree
    _history(
        base,
        {
            "event": "published",
            "post_id": "urn:post:1001",
            "content_sha256": content_hash(BODY, ()),
        },
    )
    index = publish_index(_rows(base), asset_digest_index(root, PROFILE))
    assert index == {"urn:post:1001": "ci-01"}


def test_a_scheduled_row_is_not_indexed(tree):
    """Booked is not live: a scheduled post has no metrics to attribute."""
    root, base = tree
    _history(base, {"event": "scheduled", "item_id": "ci-01", "ref": POST_URL})
    assert publish_index(_rows(base), {}) == {}


def test_item_tags_carry_the_key_prefix_on_every_axis():
    """A bare tag is not read as unknown — gtm_distill and hook_score match on `prefix:`, so it
    is INVISIBLE and the row lands in the baseline instead of its bucket."""
    tags = item_tags(
        [{"id": "ci-01", "pillar": "p", "goal": "reach", "format": "carousel", "hook_id": "h-1"}]
    )["ci-01"]
    assert set(tags) == {"pillar:p", "goal:reach", "format:carousel", "hook:h-1"}
    assert all(":" in t for t in tags)


def test_an_axis_the_item_lacks_is_omitted_not_guessed():
    tags = item_tags([{"id": "ci-01", "format": "carousel"}])["ci-01"]
    assert tags == ["format:carousel"]


def _rows(base):
    return [json.loads(x) for x in (base / "history.jsonl").read_text().splitlines() if x.strip()]


# ── row planning: refuse rather than guess ─────────────────────────────────────────────


def _plan(export, *, existing=(), refs=None, tags=None):
    return plan_rows(
        export,
        list(existing),
        refs_to_items=refs if refs is not None else {POST_URL: "ci-01"},
        tags_by_item=tags if tags is not None else {"ci-01": ["format:carousel", "hook:h-1"]},
        channel="linkedin",
        fetched="2026-09-04",
    )


def test_rows_carry_both_hook_and_format_which_is_what_the_prior_joins_on():
    rows, _ = _plan([{"post_url": POST_URL, "impressions": 900, "likes": 30}])
    assert {r["outcome"] for r in rows} == {"impressions", "reactions"}
    for row in rows:
        assert "hook:h-1" in row["tags"] and "format:carousel" in row["tags"]
        assert row["ref"] == POST_URL and row["channel"] == "linkedin"


def test_an_unmatched_ref_is_refused_not_tagged_by_hand():
    rows, notes = _plan([{"post_url": "https://social.example/posts/9999", "impressions": 5}])
    assert rows == []
    assert any(n.startswith("REFUSED") and "no `published` row" in n for n in notes)


def test_an_export_row_with_no_identifier_is_refused():
    rows, notes = _plan([{"impressions": 5}])
    assert rows == []
    assert any(n.startswith("REFUSED") for n in notes)


def test_an_item_with_no_hook_id_still_records_but_says_it_earns_no_prior():
    rows, notes = _plan(
        [{"post_url": POST_URL, "impressions": 5}], tags={"ci-01": ["format:carousel"]}
    )
    assert [r["outcome"] for r in rows] == ["impressions"]
    assert any("can never feed a hook prior" in n for n in notes)


def test_rerunning_the_same_window_does_not_double_count():
    export = [{"post_url": POST_URL, "impressions": 900}]
    first, _ = _plan(export)
    second, notes = _plan(export, existing=first)
    assert second == []
    assert any("already recorded" in n for n in notes)


def test_a_row_with_no_recognised_count_writes_nothing():
    rows, notes = _plan([{"post_url": POST_URL, "engagement_rate": 3.2}])
    assert rows == []
    assert any("no recognised count column" in n for n in notes)


# ── end to end: the loop actually closes ───────────────────────────────────────────────


def test_cli_dry_run_writes_nothing_then_apply_feeds_the_prior_and_fatigue(tree, tmp_path, capsys):
    """The whole point. Before this producer `prior_has_data` was False for every hook in
    every profile; after one export it is True, and the same rows can fatigue a hook."""
    root, base = tree
    _history(base, {"event": "published", "item_id": "ci-01", "ref": POST_URL})
    export = tmp_path / "linkedin.csv"
    export.write_text(f"post_url,impressions,likes\n{POST_URL},4200,180\n", encoding="utf-8")
    argv = [
        "--profile",
        PROFILE,
        "--content-root",
        str(root),
        "--export",
        str(export),
        "--channel",
        "linkedin",
        "--fetched",
        "2026-09-04",
    ]

    assert _cli(argv) == 0
    assert read_outcomes(root, PROFILE) == []
    assert "DRY" in capsys.readouterr().out

    assert _cli([*argv, "--apply"]) == 0
    rows = read_outcomes(root, PROFILE)
    assert {r["outcome"] for r in rows} == {"impressions", "reactions"}

    hook = "unattributed-agent-action"
    score, has_data, _ = _compute_prior(hook, "carousel", root, PROFILE)
    assert has_data is True, "the prior is still dead — the producer did not close the loop"
    assert score >= 0

    # …and the same rows are what fatigue counts, which nothing could reach before either.
    bank = hooks_mod.HookBank(
        hooks=[
            hooks_mod.Hook(
                id=hook,
                angle="a",
                payoff_promise="p",
                max_impressions=1000,
                fatigue_window_days=30,
            )
        ]
    )
    assert hooks_mod.is_fatigued(root, PROFILE, hook, bank=bank) is True


def test_cli_exits_nonzero_when_any_row_was_refused(tree, tmp_path):
    root, base = tree
    _history(base, {"event": "published", "item_id": "ci-01", "ref": POST_URL})
    export = tmp_path / "e.csv"
    export.write_text("post_url,impressions\nhttps://social.example/x,5\n", encoding="utf-8")
    assert (
        _cli(
            [
                "--profile",
                PROFILE,
                "--content-root",
                str(root),
                "--export",
                str(export),
                "--channel",
                "linkedin",
            ]
        )
        == 1
    )


# ── story_format, the one part of the method this system can check for itself (S8) ────
#
# The course claims story pieces earn more shares. This repo measures shares, so once a handful
# of story pieces have shipped the claim is testable against the rest of the bank — but only if
# the rows say which pieces were stories. One field, no interpretation: the tag goes on, the
# comparison is a later report.


def _story_item(**over):
    item = {
        "id": "ci-01",
        "format": "carousel",
        "brief": {"protagonist": "a founder who kept the wrong bet running"},
    }
    item.update(over)
    return item


def test_a_story_item_is_tagged_true():
    """S8-T1. The axis reaches the row, prefixed like every other."""
    tags = item_tags([_story_item()])["ci-01"]
    assert "story_format:true" in tags, (
        f"a protagonist-bearing item produced no story_format tag: {tags}"
    )


def test_an_item_with_a_brief_and_no_protagonist_gets_no_tag():
    """S8-T2. Not `false` — absent. The sync path never asserts a negative it did not check."""
    tags = item_tags([_story_item(brief={"angle": "trust is an infra problem"})])["ci-01"]
    assert not any(t.startswith("story_format:") for t in tags), (
        f"a non-story item was tagged: {tags}. `false` here would be indistinguishable from a "
        "measured negative, and the comparison this axis exists for would be gone"
    )


def test_an_item_with_no_brief_at_all_gets_no_tag():
    """S8-T2. Every row written before this axis existed is UNKNOWN, and must stay that way."""
    tags = item_tags([{"id": "ci-01", "format": "carousel"}])["ci-01"]
    assert tags == ["format:carousel"], (
        f"a pre-S8-shaped item gained a story_format tag: {tags} — a migration writing `false` "
        "onto legacy rows destroys the absent/false distinction irreversibly"
    )


def test_a_blank_protagonist_is_not_a_story():
    """S8-T2. An empty field is a gap, not a declaration — the same rule the preflight uses."""
    for blank in ("", "   ", None, 7):
        tags = item_tags([_story_item(brief={"protagonist": blank})])["ci-01"]
        assert not any(t.startswith("story_format:") for t in tags), (
            f"protagonist={blank!r} was read as a story"
        )


def test_the_tag_survives_the_round_trip_onto_a_row():
    """S8-T1. item_tags is upstream of plan_rows; assert the axis actually lands on the row."""
    rows, _ = _plan(
        [{"post_url": POST_URL, "impressions": 900, "likes": 30}],
        tags={"ci-01": item_tags([_story_item()])["ci-01"]},
    )
    assert rows, "no rows were produced"
    for row in rows:
        assert "story_format:true" in row["tags"], row["tags"]


# ── caption craft on the sync path (K8, 2026-09-11) ───────────────────────────────────


def test_item_tags_extracts_caption_craft_from_brief():
    """K8. item_tags extracts caption_mode and caption_voice from brief.caption_voice."""
    item = {
        "id": "ci-01",
        "brief": {
            "caption_voice": {
                "source": "model",
                "value": {
                    "mode": "narrative",
                    "voice": "close narrator",
                },
            }
        },
    }
    tags = item_tags([item])["ci-01"]
    assert "caption_mode:narrative" in tags, tags
    assert "caption_voice:close narrator" in tags, tags


def test_item_tags_extracts_caption_craft_from_item_fields():
    """K8. item_tags extracts caption_mode, caption_voice, describe_share from item fields."""
    item = {
        "id": "ci-02",
        "caption_mode": "narrative",
        "caption_voice": "second person",
        "describe_share": 0.25,
    }
    tags = item_tags([item])["ci-02"]
    assert "caption_mode:narrative" in tags, tags
    assert "caption_voice:second person" in tags, tags
    assert "describe_share:0.25" in tags, tags


def test_item_tags_omits_caption_craft_when_absent():
    """K8. Absent means unknown; no tags written when caption fields are missing."""
    item = {"id": "ci-03", "format": "short"}
    tags = item_tags([item])["ci-03"]
    assert not any(
        t.startswith(("caption_mode:", "caption_voice:", "describe_share:")) for t in tags
    ), tags

"""Tests for gtm_core.gtm_distill — content distiller path (PRD Phase 9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import gtm_distill as gd
from gtm_core import hooks as hk
from gtm_core import outcomes as oc


@pytest.fixture
def tmp_profile(tmp_path: Path) -> tuple[Path, Path, str]:
    """Return (profiles_root, content_root, profile_slug)."""
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    profile = "testco"
    (profiles_root / profile / "knowledge").mkdir(parents=True)
    (content_root / profile).mkdir(parents=True)
    return profiles_root, content_root, profile


def _write_hooks(profiles_root: Path, profile: str) -> None:
    bank = hk.HookBank(
        hooks=[
            hk.Hook(
                id="acme-augmentation",
                angle="AI that replaces judgment is a bad trade",
                payoff_promise="AI that hands you back your time",
                formats=["linkedin-text", "reel"],
                status="proven",
                max_impressions=100,
                fatigue_window_days=30,
            ),
            hk.Hook(
                id="weak-hook",
                angle="A weak angle",
                payoff_promise="A weak payoff",
                formats=["linkedin-text"],
                status="test",
            ),
        ]
    )
    hk.save_hooks(profiles_root, profile, bank)


def test_summarize_content_baseline_and_by_hook(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Baseline: 1000 impressions, 50 engagements = 5% rate.
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "impressions", "value": 1000, "tags": []},
    )
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "likes", "value": 50, "tags": []},
    )

    # Hook outperforming baseline: 600 impressions, 60 engagements = 10% rate.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 600,
            "tags": ["hook:acme-augmentation", "format:linkedin-text", "platform:linkedin"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 60,
            "tags": ["hook:acme-augmentation", "format:linkedin-text", "platform:linkedin"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "published",
            "value": 1,
            "tags": ["hook:acme-augmentation", "format:linkedin-text", "platform:linkedin"],
        },
    )

    summary = gd.summarize_content(content_root, profile, profiles_root=profiles_root)

    assert summary["baseline"]["engagement_rate"] == pytest.approx(0.05, abs=0.001)
    assert "acme-augmentation" in summary["by_hook"]
    totals = summary["by_hook"]["acme-augmentation"]["totals"]
    assert totals["engagement_rate"] == pytest.approx(0.1, abs=0.001)
    assert totals["posts"] == 1


def test_promote_candidates_require_lift_and_impressions(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Baseline 5%.
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "impressions", "value": 1000, "tags": []},
    )
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "likes", "value": 50, "tags": []},
    )

    # Hook with 10% rate and 600 impressions — should promote (lift 2.0 > 1.3).
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 600,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 60,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )

    # Hook with high rate but only 100 impressions — too few observations.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 100,
            "tags": ["hook:weak-hook", "format:linkedin-text"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 30,
            "tags": ["hook:weak-hook", "format:linkedin-text"],
        },
    )

    summary = gd.summarize_content(
        content_root, profile, profiles_root=profiles_root, min_impressions=500, lift=1.3
    )

    promote_ids = {c["hook_id"] for c in summary["promote_candidates"]}
    assert "acme-augmentation" in promote_ids
    assert "weak-hook" not in promote_ids


def test_demote_candidates_require_three_posts(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Baseline 10%.
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "impressions", "value": 1000, "tags": []},
    )
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "likes", "value": 100, "tags": []},
    )

    # Underperforming hook but only 2 posts — should NOT demote yet.
    for _ in range(2):
        oc.append_outcome(
            content_root,
            profile,
            {
                "channel": "linkedin",
                "outcome": "published",
                "value": 1,
                "tags": ["hook:weak-hook", "format:linkedin-text"],
            },
        )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 200,
            "tags": ["hook:weak-hook", "format:linkedin-text"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 5,
            "tags": ["hook:weak-hook", "format:linkedin-text"],
        },
    )

    summary = gd.summarize_content(content_root, profile, profiles_root=profiles_root, lift=1.3)
    assert summary["demote_candidates"] == []

    # Add a third published post and more bad performance.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "published",
            "value": 1,
            "tags": ["hook:weak-hook", "format:linkedin-text"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 100,
            "tags": ["hook:weak-hook", "format:linkedin-text"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 1,
            "tags": ["hook:weak-hook", "format:linkedin-text"],
        },
    )

    summary = gd.summarize_content(content_root, profile, profiles_root=profiles_root, lift=1.3)
    demote_ids = {c["hook_id"] for c in summary["demote_candidates"]}
    assert "weak-hook" in demote_ids


def test_fatigued_hooks_detected(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Exceed max_impressions (100) inside the window.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 150,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
            "ts": "2026-08-15T00:00:00Z",
        },
    )

    summary = gd.summarize_content(content_root, profile, profiles_root=profiles_root)
    fatigued_ids = {h["hook_id"] for h in summary["fatigued_hooks"]}
    assert "acme-augmentation" in fatigued_ids


def test_distill_content_writes_model_files(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "impressions", "value": 1000, "tags": []},
    )
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "likes", "value": 50, "tags": []},
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 600,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 60,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )

    paths = gd.distill_content(content_root, profile, profiles_root=profiles_root)

    assert paths["hook_performance"].is_file()
    assert paths["promote_candidates"].is_file()
    assert paths["demote_candidates"].is_file()
    assert paths["fatigued_hooks"].is_file()

    data = json.loads(paths["hook_performance"].read_text(encoding="utf-8"))
    assert data["by_hook"]["acme-augmentation"]["totals"]["engagement_rate"] == pytest.approx(
        0.1, abs=0.001
    )


def test_distill_content_cli(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "impressions", "value": 1000, "tags": []},
    )
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "likes", "value": 50, "tags": []},
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 600,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 60,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )

    rc = gd.main(
        [
            "distill-content",
            "--profile",
            profile,
            "--content-root",
            str(content_root),
            "--min-impressions",
            "500",
        ]
    )
    assert rc == 0
    assert (content_root / profile / "models" / "hook_performance.json").is_file()


def test_distill_cli_backward_compatible(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _ = profiles_root

    rc = gd.main(
        [
            "distill",
            "--profile",
            profile,
            "--content-root",
            str(content_root),
            "--period",
            "2026-08",
        ]
    )
    assert rc == 0
    assert (content_root / profile / "learnings" / "2026-08.md").is_file()


# --- pattern axis (X tweet-pattern catalog) --------------------------------------------------


def test_summarize_content_by_pattern_axis(tmp_profile: tuple[Path, Path, str]) -> None:
    """by_pattern must aggregate rows tagged `pattern:<id>`, INCLUDING rows carrying no `hook:`
    tag at all — the whole point of tag-at-sync attribution is that a post can be attributed to
    a pattern without being attributed to a hook."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Row WITH a hook tag.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "x",
            "outcome": "impressions",
            "value": 400,
            "tags": [
                "hook:acme-augmentation",
                "format:single",
                "platform:x",
                "pattern:harsh-truth",
            ],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "x",
            "outcome": "likes",
            "value": 40,
            "tags": [
                "hook:acme-augmentation",
                "format:single",
                "platform:x",
                "pattern:harsh-truth",
            ],
        },
    )
    # Row WITH the same pattern tag but NO hook tag — must still count toward by_pattern.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "x",
            "outcome": "impressions",
            "value": 100,
            "tags": ["format:single", "platform:x", "pattern:harsh-truth"],
        },
    )

    summary = gd.summarize_content(content_root, profile, profiles_root=profiles_root)

    assert "harsh-truth" in summary["by_pattern"]
    totals = summary["by_pattern"]["harsh-truth"]["totals"]
    assert totals["impressions"] == 500  # 400 (hook-tagged row) + 100 (hookless row)
    assert totals["engagement_rate"] == pytest.approx(40 / 500, abs=0.0001)

    key = "single|x"
    assert key in summary["by_pattern"]["harsh-truth"]["by_format_platform"]
    assert summary["by_pattern"]["harsh-truth"]["by_format_platform"][key]["impressions"] == 500


def test_unknown_pattern_id_row_is_dropped(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "x",
            "outcome": "impressions",
            "value": 100,
            "tags": ["format:single", "platform:x", "pattern:not-a-real-pattern-id"],
        },
    )

    summary = gd.summarize_content(content_root, profile, profiles_root=profiles_root)
    assert summary["by_pattern"] == {}


def test_pattern_axis_uses_its_own_baseline_not_the_hook_baseline(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """Reusing by_hook's baseline (rows with no `hook:` tag) for the pattern axis would compare
    a pattern's lift against the wrong population — the two populations must be independently
    computed and, when they genuinely differ, produce different numbers."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Carries a hook: tag (so it's excluded from `baseline`) but NO pattern: tag (so it IS
    # included in `pattern_baseline`) — a row that belongs to exactly one of the two baselines.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "x",
            "outcome": "impressions",
            "value": 900,
            "tags": ["hook:acme-augmentation", "format:single", "platform:x"],
        },
    )

    summary = gd.summarize_content(content_root, profile, profiles_root=profiles_root)
    assert summary["baseline"]["impressions"] == 0  # excluded: carries a hook: tag
    assert summary["pattern_baseline"]["impressions"] == 900  # included: carries no pattern: tag
    assert summary["baseline"] != summary["pattern_baseline"]


def test_by_hook_unchanged_by_presence_of_pattern_tagged_rows(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """Adding pattern-tagged rows to the outcome log must not perturb by_hook's numbers."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 600,
            "tags": ["hook:acme-augmentation", "format:linkedin-text", "platform:linkedin"],
        },
    )
    without_pattern = gd.summarize_content(content_root, profile, profiles_root=profiles_root)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "x",
            "outcome": "impressions",
            "value": 100,
            "tags": ["format:single", "platform:x", "pattern:harsh-truth"],
        },
    )
    with_pattern = gd.summarize_content(content_root, profile, profiles_root=profiles_root)

    # by_hook is scoped to rows carrying a hook: tag, so an added hookless row must not move it.
    assert with_pattern["by_hook"] == without_pattern["by_hook"]
    # `baseline` (rows with NO hook: tag) correctly DOES pick up the new hookless row — that is
    # the pre-existing, unrelated baseline semantics this test is not exercising.
    assert (
        with_pattern["baseline"]["impressions"] == without_pattern["baseline"]["impressions"] + 100
    )
    assert "harsh-truth" in with_pattern["by_pattern"]


def test_pattern_performance_model_file_written(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "x",
            "outcome": "impressions",
            "value": 200,
            "tags": ["format:single", "platform:x", "pattern:harsh-truth"],
        },
    )

    paths = gd.distill_content(content_root, profile, profiles_root=profiles_root)

    assert paths["pattern_performance"].is_file()
    data = json.loads(paths["pattern_performance"].read_text(encoding="utf-8"))
    assert set(data) == {"generated_at", "period", "baseline", "by_pattern"}
    assert "harsh-truth" in data["by_pattern"]
    assert data["by_pattern"]["harsh-truth"]["totals"]["impressions"] == 200


def test_distill_content_cli_writes_pattern_performance(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """The by_pattern axis must populate through the CLI path too — main() never passes
    profiles_root into distill_content, so a BANK-gated axis would silently empty out here
    even though it works fine when called in-process. by_pattern must not depend on the bank."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "x",
            "outcome": "impressions",
            "value": 300,
            "tags": ["format:single", "platform:x", "pattern:harsh-truth"],
        },
    )

    rc = gd.main(
        [
            "distill-content",
            "--profile",
            profile,
            "--content-root",
            str(content_root),
            "--min-impressions",
            "500",
        ]
    )
    assert rc == 0
    pattern_file = content_root / profile / "models" / "pattern_performance.json"
    assert pattern_file.is_file()
    data = json.loads(pattern_file.read_text(encoding="utf-8"))
    assert data["by_pattern"]["harsh-truth"]["totals"]["impressions"] == 300


# --- portfolio axes: journey_stage / goal / pillar ---------------------------------------------


def test_portfolio_axes_aggregate_with_their_own_baselines(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """journey_stage / goal / pillar each bucket on their own prefixed tag and each measure lift
    against rows that lack *that* tag. Before 2026-08-24 these three tags were emitted bare by
    content-outcomes-sync, so `_tag_value` never matched them and every row fell into the
    baseline — the axis was plannable and gated but structurally unmeasurable."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 400,
            "tags": [
                "format:single",
                "platform:linkedin",
                "journey_stage:conversion",
                "goal:conversion",
                "pillar:acme",
            ],
        },
    )
    # Carries journey_stage but NO goal/pillar — lands in one bucket and two baselines.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 100,
            "tags": ["format:single", "platform:linkedin", "journey_stage:awareness"],
        },
    )

    axes = gd.summarize_content(content_root, profile, profiles_root=profiles_root)["axes"]

    assert set(axes) == {"journey_stage", "goal", "pillar"}
    assert axes["journey_stage"]["by_value"]["conversion"]["totals"]["impressions"] == 400
    assert axes["journey_stage"]["by_value"]["awareness"]["totals"]["impressions"] == 100
    # Every row carries a journey_stage, so that axis's baseline is empty...
    assert axes["journey_stage"]["baseline"]["impressions"] == 0
    # ...while the untagged row is baseline for the other two. Distinct populations.
    assert axes["goal"]["baseline"]["impressions"] == 100
    assert axes["pillar"]["baseline"]["impressions"] == 100
    assert axes["goal"]["by_value"]["conversion"]["totals"]["impressions"] == 400
    assert axes["pillar"]["by_value"]["acme"]["totals"]["impressions"] == 400


def test_unknown_journey_stage_value_is_kept_not_dropped(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """Deliberately unlike the pattern axis. `journey_stage` and `goal` are engine-fixed enums
    and `pillar` is tenant config, so an out-of-vocabulary value is a data bug worth surfacing
    in the output rather than a row to hide — no `valid=` set is passed for these three."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 50,
            "tags": ["format:single", "platform:linkedin", "journey_stage:not-a-real-stage"],
        },
    )

    axes = gd.summarize_content(content_root, profile, profiles_root=profiles_root)["axes"]
    assert "not-a-real-stage" in axes["journey_stage"]["by_value"]
    assert axes["journey_stage"]["baseline"]["impressions"] == 0


def test_bare_unprefixed_axis_tag_is_invisible(tmp_profile: tuple[Path, Path, str]) -> None:
    """The regression that motivated the fix, pinned as behaviour: a bare `--tag conversion`
    is not read as an unknown value, it is not read at all — the row lands in the baseline.
    This is why the skill body must emit `journey_stage:<value>`, and why a prose note alone
    was not enough the first time."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 70,
            "tags": ["format:single", "platform:linkedin", "conversion"],
        },
    )

    axes = gd.summarize_content(content_root, profile, profiles_root=profiles_root)["axes"]
    assert axes["journey_stage"]["by_value"] == {}
    assert axes["journey_stage"]["baseline"]["impressions"] == 70


def test_distill_content_writes_axis_performance(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 250,
            "tags": ["format:single", "platform:linkedin", "goal:reach"],
        },
    )

    rc = gd.main(
        [
            "distill-content",
            "--profile",
            profile,
            "--content-root",
            str(content_root),
        ]
    )
    assert rc == 0
    axis_file = content_root / profile / "models" / "axis_performance.json"
    assert axis_file.is_file()
    data = json.loads(axis_file.read_text(encoding="utf-8"))
    assert set(data) == {"generated_at", "period", "journey_stage", "goal", "pillar"}
    assert data["goal"]["by_value"]["reach"]["totals"]["impressions"] == 250

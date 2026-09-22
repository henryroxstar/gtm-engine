"""Tests for gtm_core.gtm_distill — content distiller path (PRD Phase 9)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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

    # Exceed max_impressions (100) inside the 30-day window. Dated relative to now: a fixed
    # date silently ages out of the rolling window and turns this test red a month later.
    inside_window = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 150,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
            "ts": inside_window,
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


# =============================================================================================
# IC4 / IC5 — the OUTREACH distiller's remedy routing and its power.
#
# Scope fence: these touch `promote_candidates` / `render_learnings` only (reply rate over
# sends). The CONTENT distiller — summarize_content / _promote_candidates, keyed by_hook on a
# 500-impression floor — is a separate code path and must stay green unmodified, which
# tests/contracts/test_distiller_promotes_hook_on_evidence.py proves.
# =============================================================================================


def _axis(tag, *, hook_ids=(), icp_terms=()):
    from gtm_core.gtm_distill import _classify_axis

    return _classify_axis(tag, hook_ids=frozenset(hook_ids), icp_terms=frozenset(icp_terms))


def test_a_hook_prefix_is_the_message_axis():
    assert _axis("hook:winner") == "message"


def test_a_bare_tag_matching_a_hook_id_is_the_message_axis():
    assert _axis("winner", hook_ids={"winner"}) == "message"


def test_a_bare_tag_matching_a_persona_is_the_icp_axis():
    assert _axis("ciso", icp_terms={"ciso"}) == "icp"


def test_a_structural_prefix_is_neither():
    """It says WHERE a result was measured, not WHAT to change."""
    for tag in (
        "cell:base:enterprise:cto:compliance",
        "seq:123",
        "lane:generic",
        "overlay:x",
        "format:linkedin-text",
        "platform:linkedin",
    ):
        assert _axis(tag) == "structural", tag


def test_an_unknown_tag_is_unknown_not_an_angle():
    """The permissive branch today is 'assume it's an angle' — that is the defect."""
    assert _axis("wharrgarbl") == "unknown"


def test_a_tag_matching_both_axes_is_ambiguous():
    """Mirrors the content distiller's closed-vocabulary rule: a row with a
    present-but-invalid key belongs to neither."""
    assert _axis("ciso", hook_ids={"ciso"}, icp_terms={"ciso"}) == "ambiguous"


def test_classification_folds_case():
    assert _axis("CISO", icp_terms={"ciso"}) == "icp"


def test_a_renamed_persona_yields_unknown_never_a_silent_misroute():
    """§4.3: a persona renamed in role-vocabulary.toml after the tag was written must
    surface as unresolvable, never be quietly routed to the wrong file."""
    assert _axis("ciso", icp_terms={"chief-security"}) == "unknown"


# --- remedy routing in the rendered note -------------------------------------------------------


def _summary(tag, sent, replies, *, baseline_sent=10000, baseline_replies=500):
    """A summary shaped exactly like outcomes.summarize's, with one tag under test.

    Baseline defaults give a 5% reply rate, so a tag at 20% clears the 1.3x lift with room
    to spare and is genuinely powered at n=4000 — the fixture has to satisfy BOTH gates or
    the 'still promotes' control proves nothing.
    """
    total_sent = baseline_sent + sent
    total_replies = baseline_replies + replies

    def _b(s, r):
        return {
            "sent": s,
            "replies": r,
            "meetings": 0,
            "reply_rate": (r / s) if s else None,
            "meeting_rate": 0.0 if s else None,
        }

    return {
        "totals": _b(total_sent, total_replies),
        "by_channel": {"email": _b(total_sent, total_replies)},
        "by_tag": {tag: _b(sent, replies)},
    }


def _note(tag, *, sent=4000, replies=800, hook_ids=(), icp_terms=()):
    import gtm_core.gtm_distill as gd

    summary = _summary(tag, sent, replies)
    cands = gd.promote_candidates(
        summary, hook_ids=frozenset(hook_ids), icp_terms=frozenset(icp_terms)
    )
    return gd.render_learnings("acme", "2026-08", summary, cands)


def _remedy_lines(note):
    return [ln for ln in note.splitlines() if ln.startswith("- `")]


def test_a_persona_tag_routes_to_the_icp_files():
    note = _note("ciso", icp_terms={"ciso"})
    line = "\n".join(_remedy_lines(note))
    assert "icp-personas.md" in line
    assert "hook-matrix.md" not in line, "an ICP problem must not be answered with a copy fix"


def test_a_persona_tag_uses_the_segment_noun_not_angle():
    """The noun is half the defect: 'soften this angle' for a segment is the wrong verb,
    the wrong noun and the wrong file."""
    assert "angle" not in "\n".join(_remedy_lines(_note("ciso", icp_terms={"ciso"})))


def test_a_hook_tag_routes_to_the_message_files():
    assert "hook-matrix.md" in "\n".join(_remedy_lines(_note("hook:winner")))


def test_a_structural_tag_proposes_no_remedy_but_stays_in_the_table():
    """Today a cell:… tag over threshold renders as 'strengthen this angle in hook-matrix.md'."""
    note = _note("cell:base:enterprise:cto:compliance")
    assert not _remedy_lines(note), "a structural tag must propose no file"
    assert "cell:base" in note, "it is still reported in the table — measured, not routed"


def test_an_unknown_tag_names_itself_and_proposes_no_file():
    note = _note("wharrgarbl")
    assert "wharrgarbl" in note
    assert not _remedy_lines(note)


def test_an_ambiguous_tag_is_routed_to_neither():
    note = _note("ciso", hook_ids={"ciso"}, icp_terms={"ciso"})
    assert not _remedy_lines(note)


def test_inverting_the_classifier_breaks_the_routing(monkeypatch):
    """§R18 in-process negative control: swap icp <-> message and the routing test must go
    red, otherwise it only asserts that SOME file is named."""
    import gtm_core.gtm_distill as gd

    real = gd._classify_axis
    flip = {"icp": "message", "message": "icp"}
    monkeypatch.setattr(
        gd, "_classify_axis", lambda t, **kw: flip.get(real(t, **kw), real(t, **kw))
    )
    line = "\n".join(_remedy_lines(_note("ciso", icp_terms={"ciso"})))
    assert "hook-matrix.md" in line, "the inversion must actually change the routing"
    assert "icp-personas.md" not in line


def test_the_structural_class_changes_what_the_operator_is_told(monkeypatch):
    """§R18 control for the structural class, discriminating on what it actually buys.

    Both `structural` and `unknown` refuse to name a file — that is correct and deliberate.
    So the class is not doing its work in the routing decision; it is doing it in the REASON.
    "Measured here, not caused here" tells an operator to stop looking for a file; "its axis
    is unresolvable" tells them to go name the tag. Conflating the two sends them hunting for
    an edit that does not exist.
    """
    import gtm_core.gtm_distill as gd

    assert gd._classify_axis("cell:base:x") == "structural"
    with_class = _note("cell:base:x")
    assert "(structural)" in with_class

    monkeypatch.setattr(gd, "_STRUCTURAL_PREFIXES", frozenset())
    assert gd._classify_axis("cell:base:x") == "unknown"
    without_class = _note("cell:base:x")
    assert "(unknown)" in without_class
    assert with_class != without_class, "the class must change what the note says"


# --- IC5: the distiller states its own power ---------------------------------------------------


def test_default_min_sent_is_still_five():
    """PRD D3, and it is the counter-intuitive one. Raising it to a statistically defensible
    floor (~3,000) empties the note at current volume, and an empty note reads as 'no signal'
    rather than 'no data'. Those are different states and the operator must tell them apart."""
    import gtm_core.gtm_distill as gd

    assert gd.DEFAULT_MIN_SENT == 5


def test_every_candidate_carries_its_mde_and_powered_flag():
    import gtm_core.gtm_distill as gd

    c = gd.promote_candidates(_summary("ciso", 5, 1))[0]
    assert {"tag", "direction", "rate", "sent", "mde", "powered"} <= set(c)


def test_an_underpowered_candidate_is_watch_not_a_promotion():
    import gtm_core.gtm_distill as gd

    c = gd.promote_candidates(_summary("ciso", 5, 1))[0]
    assert c["powered"] is False
    assert c["direction"] == "watch"


def test_a_powered_candidate_still_promotes():
    """NEGATIVE CONTROL. Without this, a bug making EVERYTHING watch looks like the fix."""
    import gtm_core.gtm_distill as gd

    c = gd.promote_candidates(_summary("ciso", 4000, 800))[0]
    assert c["powered"] is True
    assert c["direction"] == "outperforms"


def test_a_none_mde_yields_watch():
    """§4.2: powered is granted only by an explicit MDE comparison; absence never grants."""
    import gtm_core.gtm_distill as gd

    summary = _summary("ciso", 5, 1)
    summary["totals"]["reply_rate"] = 0.9999  # detectable_lift returns None here
    cands = gd.promote_candidates(summary)
    for c in cands:
        assert c["powered"] is False and c["direction"] == "watch"


def test_the_mde_comes_from_gtm_core_power(monkeypatch):
    """§4.5 surface agreement: the note's MDE and the dashboard learn view must be ONE
    derivation. Assert the distiller asks gtm_core.power rather than computing its own."""
    import gtm_core.gtm_distill as gd

    seen = []

    def _spy(n, baseline):
        seen.append((n, baseline))
        return 3.2

    monkeypatch.setattr(gd, "detectable_lift", _spy)
    gd.promote_candidates(_summary("ciso", 5, 1))
    assert seen, "the distiller computed an MDE without asking gtm_core.power"


def test_the_rendered_line_prints_the_mde():
    note = _note("ciso", sent=5, replies=1, icp_terms={"ciso"})
    assert "detectable at this n" in note


def test_watch_renders_in_its_own_section_distinct_from_promote():
    """An operator skimming must not mistake an n=5 candidate for a result."""
    note = _note("ciso", sent=5, replies=1, icp_terms={"ciso"})
    assert "## Watch" in note
    assert "## Promote?" in note
    assert note.index("## Promote?") < note.index("## Watch")


def test_forcing_powered_true_breaks_the_watch_test(monkeypatch):
    """§R18 control: if everything is powered, the watch classification is meaningless."""
    import gtm_core.gtm_distill as gd

    monkeypatch.setattr(gd, "detectable_lift", lambda n, b: 1.0)
    assert gd.promote_candidates(_summary("ciso", 5, 1))[0]["direction"] != "watch"


def test_a_non_list_tags_row_refuses_the_distill_run(tmp_path):
    """§4.4: refuse, don't skip. Today three sites silently ignore a non-list `tags` and the
    row falls into the BASELINE, which shifts every comparison without saying so."""
    import gtm_core.gtm_distill as gd

    with pytest.raises(ValueError, match="tags"):
        gd._require_list_tags({"tags": {"persona": "ciso"}})
    gd._require_list_tags({"tags": ["ciso"]})  # must not raise
    gd._require_list_tags({})  # absent is fine — it is a row with no tags


def test_the_distiller_round_trip_routes_each_axis_to_its_own_file(tmp_path):
    """Test plan §3.E: one persona tag, one hook tag, one `cell:` tag and one unknown tag in a
    real outcomes file — the rendered note must name icp-personas.md, hook-matrix.md, no file
    and no file respectively. This is the end-to-end proof that membership resolution actually
    reaches the note, not just the classifier."""
    import gtm_core.gtm_distill as gd
    import gtm_core.hooks as hooks_mod
    import gtm_core.outcomes as oc

    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    (profiles_root / "acme" / "knowledge").mkdir(parents=True)
    (profiles_root / "acme" / "knowledge" / "hooks.toml").write_text(
        '[[hook]]\nid = "winner"\nstatus = "proven"\ntext = "a hook"\n', encoding="utf-8"
    )
    (profiles_root / "acme" / "knowledge" / "role-vocabulary.toml").write_text(
        'default_persona = "ciso"\n\n'
        '[[persona]]\nname = "ciso"\ncues = ["ciso"]\n\n'
        '[[seat]]\nname = "security"\npersonas = ["ciso"]\nstakes = ["audit"]\n',
        encoding="utf-8",
    )
    assert hooks_mod  # the bank is read through gtm_distill._membership

    for tag in ("ciso", "hook:winner", "cell:base:enterprise", "wharrgarbl"):
        oc.append_outcome(
            content_root,
            "acme",
            {"channel": "email", "outcome": "sent", "value": 4000, "tags": [tag]},
            now="2026-08-15T00:00:00Z",
        )
        oc.append_outcome(
            content_root,
            "acme",
            {"channel": "email", "outcome": "reply", "value": 800, "tags": [tag]},
            now="2026-08-15T00:00:00Z",
        )
    # a large untagged baseline so the tags have something to beat
    oc.append_outcome(
        content_root,
        "acme",
        {"channel": "email", "outcome": "sent", "value": 40000, "tags": []},
        now="2026-08-15T00:00:00Z",
    )
    oc.append_outcome(
        content_root,
        "acme",
        {"channel": "email", "outcome": "reply", "value": 2000, "tags": []},
        now="2026-08-15T00:00:00Z",
    )

    note = gd.distill(
        content_root, "acme", period="2026-08", profiles_root=profiles_root
    ).read_text()

    icp_line = next(ln for ln in note.splitlines() if ln.startswith("- `ciso`"))
    assert "icp-personas.md" in icp_line and "hook-matrix.md" not in icp_line

    hook_line = next(ln for ln in note.splitlines() if ln.startswith("- `hook:winner`"))
    assert "hook-matrix.md" in hook_line and "icp-personas.md" not in hook_line

    # structural and unknown reach no remedy line at all
    assert not [ln for ln in note.splitlines() if ln.startswith("- `cell:")]
    assert not [ln for ln in note.splitlines() if ln.startswith("- `wharrgarbl`")]
    assert "name no knowledge file" in note


def test_a_malformed_role_vocabulary_refuses_the_distill_run(tmp_path):
    """§4.4 absence vs corruption. A MISSING role-vocabulary.toml is fine — rv.load answers
    with the shipped default. A PRESENT BUT MALFORMED one must stop the run: the file exists,
    so someone meant it to be read, and quietly falling back to the default would classify
    every tag against a vocabulary the tenant did not write, silently changing which file the
    note tells them to edit."""
    import gtm_core.gtm_distill as gd
    from gtm_core.role_vocabulary import VocabularyError

    profiles_root = tmp_path / "profiles"
    (profiles_root / "acme" / "knowledge").mkdir(parents=True)
    (profiles_root / "acme" / "knowledge" / "role-vocabulary.toml").write_text(
        '[[persona]]\nname = "ciso"\ncues = ["ciso"]\n',
        encoding="utf-8",  # no default_persona
    )
    with pytest.raises(VocabularyError):
        gd._membership("acme", profiles_root, tmp_path / "content")


def test_a_missing_role_vocabulary_is_not_an_error(tmp_path):
    """NEGATIVE CONTROL for the above: absence must NOT raise, or every profile without a
    tenant vocabulary would stop distilling."""
    import gtm_core.gtm_distill as gd

    profiles_root = tmp_path / "profiles"
    (profiles_root / "acme" / "knowledge").mkdir(parents=True)
    hook_ids, icp_terms = gd._membership("acme", profiles_root, tmp_path / "content")
    assert icp_terms, "the shipped default vocabulary must still supply terms"


@pytest.mark.parametrize("tag", [None, "", "   ", "\t\n"])
def test_an_empty_tag_is_unknown_never_an_axis(tag):
    """§4.2 absence is never permissive, and §R18: found by a mutation pass, not by review.

    `_classify_axis` has TWO `return "unknown"` paths — the empty-tag guard and the final
    fallback. Only the fallback was covered, so mutating the guard to return "message" survived:
    a blank or whitespace tag could have been routed to a knowledge file and no test would have
    noticed. Both paths are pinned now.
    """
    from gtm_core.gtm_distill import _classify_axis

    assert _classify_axis(tag) == "unknown"
    # and it stays unknown even when the membership sets are populated — a blank tag matches
    # nothing by definition, so it must not fall through to a lookup that could coincide.
    assert _classify_axis(tag, hook_ids=frozenset({""}), icp_terms=frozenset({""})) == "unknown"

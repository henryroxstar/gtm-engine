"""Tests for gtm_core.hook_score — internal virality scorer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import hook_score as hs
from gtm_core import hooks as hk
from gtm_core import outcomes as oc


@pytest.fixture
def tmp_profile(tmp_path: Path) -> tuple[Path, Path, str]:
    """Return (profiles_root, content_root, profile_slug) for a temp tenant."""
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
                opening_beats=[
                    hk.OpeningBeat(
                        format="linkedin-text",
                        variant="A",
                        text="Your niche is already in conversation on LinkedIn.",
                    ),
                    hk.OpeningBeat(format="reel", variant="A", video="cold open: founder at desk"),
                ],
            )
        ],
        banned=hk.BannedConfig(stems=["just use AI"]),
    )
    hk.save_hooks(profiles_root, profile, bank)


def test_score_without_retention_is_uncertain(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    text = "AI that replaces judgment is a bad trade. AI that hands you back your time."
    result = hs.score(
        "acme-augmentation",
        "linkedin-text",
        text,
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
    )

    assert result.hook_id == "acme-augmentation"
    assert result.format == "linkedin-text"
    # Retention missing → 0, prior missing → 40, pattern strong → ~100.
    # Weighted: 0*.35 + 40*.35 + 100*.30 = 44 → band uncertain.
    assert result.band == "uncertain"
    assert result.components["retention"] == 0
    assert result.components["prior"] == 40
    assert result.weakest_dim == "retention-craft"


def test_score_with_retention_and_no_prior_is_uncertain(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    text = "AI that replaces judgment is a bad trade. AI that hands you back your time."
    retention = {"part_a_score": 12, "weakest_dims": ["micro-retention-beats"], "fix": "Add cuts."}
    result = hs.score(
        "acme-augmentation",
        "reel",
        text,
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
        retention_component=retention,
    )

    # Retention 12/14=86, prior 40, pattern 100. Weighted ~81. But no prior data → uncertain.
    assert result.band == "uncertain"
    assert result.components["retention"] == 86
    assert result.weakest_dim == "prior"
    assert "No reliable historical data" in result.fix
    assert result.prior_has_data is False


# --------------------------------------------------------------------------- #
# Audited cold-start override (does NOT touch _band_for / _compute_prior)
# --------------------------------------------------------------------------- #


def _cold_start_result(tmp_profile: tuple[Path, Path, str]) -> hs.ScoreResult:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)
    retention = {"part_a_score": 12, "weakest_dims": ["micro-retention-beats"], "fix": "Add cuts."}
    return hs.score(
        "acme-augmentation",
        "reel",
        "AI that replaces judgment is a bad trade. AI that hands you back your time.",
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
        retention_component=retention,
    )


def test_override_refuses_blank_reason(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    result = _cold_start_result(tmp_profile)

    for blank in ("", "   ", None):
        with pytest.raises(ValueError, match="must not be blank"):
            hs.record_override(content_root, profile, result, blank)  # type: ignore[arg-type]

    assert oc.read_outcomes(content_root, profile) == []


def test_override_refuses_when_prior_has_data(tmp_profile: tuple[Path, Path, str]) -> None:
    """An override is only meaningful for the cold-start (no-prior) case. A hook with
    real prior data that still bands low/uncertain has no cold start to bypass —
    refuse rather than silently becoming a general-purpose gate bypass."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Give this hook×format a real (weak) prior: >=500 impressions, some engagement.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 600,
            "tags": ["hook:acme-augmentation", "format:reel"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "impressions", "value": 1000, "tags": []},
    )
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": "engagement", "value": 50, "tags": []},
    )

    retention = {"part_a_score": 4, "weakest_dims": ["cold-open"], "fix": "Cut the intro."}
    result = hs.score(
        "acme-augmentation",
        "reel",
        "AI that replaces judgment is a bad trade. AI that hands you back your time.",
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
        retention_component=retention,
    )
    assert result.prior_has_data is True

    with pytest.raises(ValueError, match="cold-start case only"):
        hs.record_override(content_root, profile, result, "ship it anyway")


def test_override_accepted_appends_audited_row_and_leaves_band_unchanged(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    result = _cold_start_result(tmp_profile)
    assert result.band == "uncertain"
    band_before = result.band

    hs.record_override(
        content_root,
        profile,
        result,
        "  new hook, first publish to seed outcomes  ",
        platform="linkedin",
    )

    # The override never mutates the ScoreResult or re-derives a band.
    assert result.band == band_before == "uncertain"

    rows = oc.read_outcomes(content_root, profile)
    override_rows = [r for r in rows if r.get("outcome") == "hook_score_override"]
    assert len(override_rows) == 1
    row = override_rows[0]
    assert "hook:acme-augmentation" in row["tags"]
    assert "format:reel" in row["tags"]
    assert "band_override:cold_start" in row["tags"]
    assert row["meta"]["reason"] == "new hook, first publish to seed outcomes"  # stripped
    assert row["meta"]["score"] == result.score
    assert row["meta"]["band"] == "uncertain"
    assert row["channel"] == "linkedin"


def test_override_row_is_excluded_from_its_own_prior(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """An override row must never feed _compute_prior — otherwise repeated overrides
    would eventually manufacture a fake prior and defeat the whole guard."""
    profiles_root, content_root, profile = tmp_profile
    result = _cold_start_result(tmp_profile)
    hs.record_override(content_root, profile, result, "seed outcomes", platform="linkedin")

    # hook_score_override is neither an impression nor an engagement outcome, so
    # re-scoring immediately after must still see no prior data.
    rescored = hs.score(
        "acme-augmentation",
        "reel",
        "AI that replaces judgment is a bad trade. AI that hands you back your time.",
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
        retention_component={"part_a_score": 12, "weakest_dims": [], "fix": ""},
    )
    assert rescored.prior_has_data is False
    assert rescored.band == "uncertain"


def test_cli_override_reason_flag(tmp_profile: tuple[Path, Path, str], capsys) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    exit_code = hs.main(
        [
            "--profile",
            profile,
            "--hook",
            "acme-augmentation",
            "--format",
            "reel",
            "--text",
            "AI that replaces judgment is a bad trade. AI that hands you back your time.",
            "--retention-raw",
            "12",
            "--content-root",
            str(content_root),
            "--profiles-root",
            str(profiles_root),
            "--override-reason",
            "cold start — first script for this hook",
        ]
    )
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["band"] == "uncertain"
    assert out["override"]["accepted"] is True
    assert out["override"]["reason"] == "cold start — first script for this hook"

    rows = oc.read_outcomes(content_root, profile)
    assert any(r.get("outcome") == "hook_score_override" for r in rows)


def test_cli_override_reason_blank_is_reported_not_raised(
    tmp_profile: tuple[Path, Path, str], capsys
) -> None:
    """A blank --override-reason should surface as a reported CLI error, not a
    traceback — main() must still exit 0 with the score, since the score computation
    itself succeeded."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    exit_code = hs.main(
        [
            "--profile",
            profile,
            "--hook",
            "acme-augmentation",
            "--format",
            "reel",
            "--text",
            "AI that replaces judgment is a bad trade. AI that hands you back your time.",
            "--retention-raw",
            "12",
            "--content-root",
            str(content_root),
            "--profiles-root",
            str(profiles_root),
            "--override-reason",
            "   ",
        ]
    )
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["override"]["accepted"] is False
    rows = oc.read_outcomes(content_root, profile)
    assert not any(r.get("outcome") == "hook_score_override" for r in rows)


def test_prior_beat_baseline_boosts_score(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Baseline: 1000 impressions, 100 engagements = 10% rate.
    for _ in range(10):
        oc.append_outcome(
            content_root,
            profile,
            {"channel": "linkedin", "outcome": "impressions", "value": 100, "tags": []},
        )
    for _ in range(10):
        oc.append_outcome(
            content_root,
            profile,
            {"channel": "linkedin", "outcome": "likes", "value": 10, "tags": []},
        )

    # Hook: 500 impressions, 100 engagements = 20% rate (2x baseline).
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 500,
            "tags": ["hook:acme-augmentation", "format:reel"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 100,
            "tags": ["hook:acme-augmentation", "format:reel"],
        },
    )

    text = "AI that replaces judgment is a bad trade. AI that hands you back your time."
    retention = {"part_a_score": 12, "weakest_dims": ["micro-retention-beats"], "fix": "Add cuts."}
    result = hs.score(
        "acme-augmentation",
        "reel",
        text,
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
        retention_component=retention,
    )

    assert result.components["prior"] == 100
    assert result.band == "high"


def test_pattern_penalizes_missing_payoff(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Baseline for the profile (no hook tag).
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
    # Hook prior outperforms baseline so prior is not the weakest.
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
            "value": 120,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )

    # Text omits the payoff promise entirely.
    text = "AI that replaces judgment is a bad trade."
    retention = {"part_a_score": 14, "weakest_dims": ["completion-realism"], "fix": "Trim it."}
    result = hs.score(
        "acme-augmentation",
        "linkedin-text",
        text,
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
        retention_component=retention,
    )

    assert result.components["pattern"] < 100
    assert result.weakest_dim == "payoff-presence"
    assert "payoff_promise not present" in result.fix


def test_score_rejects_missing_hook(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    with pytest.raises(hs.ScoreError, match="not found"):
        hs.score(
            "missing",
            "reel",
            "text",
            profile,
            profiles_root=profiles_root,
            content_root=content_root,
        )


def test_score_rejects_undeclared_format(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    with pytest.raises(hs.ScoreError, match="not declared"):
        hs.score(
            "acme-augmentation",
            "carousel",
            "text",
            profile,
            profiles_root=profiles_root,
            content_root=content_root,
        )


def test_normalize_retention_clamps_and_scales() -> None:
    assert hs.normalize_retention(0) == 0
    assert hs.normalize_retention(14) == 100
    assert hs.normalize_retention(7) == 50
    assert hs.normalize_retention(100) == 100
    assert hs.normalize_retention(-5) == 0


def test_parse_retention_response_json() -> None:
    text = '{"part_a_score": 10, "weakest_dims": ["silent-legibility"], "fix": "Add captions."}'
    parsed = hs.parse_retention_response(text)
    assert parsed["part_a_score"] == 10
    assert parsed["weakest_dims"] == ["silent-legibility"]
    assert parsed["fix"] == "Add captions."


def test_parse_retention_response_markdown_fenced() -> None:
    text = '```json\n{"part_a_score": 8, "weakest_dims": ["cold-open"], "fix": "Start mid-sentence."}\n```'
    parsed = hs.parse_retention_response(text)
    assert parsed["part_a_score"] == 8


def test_parse_retention_response_invalid_raises() -> None:
    with pytest.raises(hs.ScoreError):
        hs.parse_retention_response("not json")


def test_build_retention_prompt_includes_angle_and_payoff(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)
    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
    hook = bank.by_id("acme-augmentation")
    assert hook is not None

    prompt = hs.build_retention_prompt(hook, "reel", "script text", platform="instagram")
    assert hook.angle in prompt
    assert hook.payoff_promise in prompt
    assert "script text" in prompt
    assert "instagram" in prompt
    assert "part_a_score" in prompt


def test_load_and_save_weights(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _ = profiles_root

    defaults = hs.load_weights(content_root, profile)
    assert defaults == hs.DEFAULT_WEIGHTS

    custom = {"retention": 0.5, "prior": 0.3, "pattern": 0.2}
    hs.save_weights(content_root, profile, custom)
    loaded = hs.load_weights(content_root, profile)
    assert loaded == custom


def test_record_score_appends_hook_score_row(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    result = hs.score(
        "acme-augmentation",
        "linkedin-text",
        "AI that replaces judgment is a bad trade. AI that hands you back your time.",
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
    )
    hs.record_score(content_root, profile, result, platform="linkedin")

    rows = oc.read_outcomes(content_root, profile)
    score_rows = [r for r in rows if r.get("outcome") == "hook_score"]
    assert len(score_rows) == 1
    assert "hook:acme-augmentation" in score_rows[0]["tags"]
    assert "format:linkedin-text" in score_rows[0]["tags"]
    assert f"predictor_band:{result.band}" in score_rows[0]["tags"]
    assert score_rows[0]["meta"]["components"] == result.components


def test_load_weights_falls_back_on_bad_file(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    _ = profiles_root
    path = content_root / profile / "models" / "hook_score_weights.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")

    loaded = hs.load_weights(content_root, profile)
    assert loaded == hs.DEFAULT_WEIGHTS


@pytest.mark.anyio
async def test_score_with_model_uses_injected_caller(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Baseline for the profile (no hook tag).
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
    # Hook prior outperforms baseline so prior is not the weakest.
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
            "value": 120,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )

    async def fake_caller(prompt: str) -> str:
        assert "part_a_score" in prompt
        return '{"part_a_score": 11, "weakest_dims": ["payoff-density"], "fix": "Close the loop."}'

    text = "AI that replaces judgment is a bad trade. AI that hands you back your time."
    result = await hs.score_with_model(
        "acme-augmentation",
        "linkedin-text",
        text,
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
        model_caller=fake_caller,
    )

    assert result.components["retention"] == hs.normalize_retention(11)
    assert result.weakest_dim == "payoff-density"


def test_hook_score_unaffected_by_pattern_id(tmp_profile: tuple[Path, Path, str]) -> None:
    """hook_score's "pattern" scoring component is a different, unrelated concept (the
    deterministic lint score, DEFAULT_WEIGHTS key). Setting OpeningBeat.pattern_id must not
    change hs.score()'s output at all — same text scored against a hook whose matching beat
    carries a pattern_id and one that doesn't must produce byte-identical ScoreResults."""
    profiles_root, content_root, profile = tmp_profile
    _write_hooks(profiles_root, profile)

    # Give the linkedin-text beat a pattern_id in place (the field is beat-agnostic to what
    # value it holds — this test exercises hook_score, not hooks_lint's cross-catalog check).
    loaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    hook = loaded.by_id("acme-augmentation")
    for beat in hook.opening_beats:
        if beat.format == "linkedin-text":
            beat.pattern_id = "harsh-truth"
    hk.save_hooks(profiles_root, profile, loaded)

    text = "AI that replaces judgment is a bad trade. AI that hands you back your time."
    with_pattern = hs.score(
        "acme-augmentation",
        "linkedin-text",
        text,
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
    )

    # Reset to a bank with no pattern_id set, same everything else, same profiles/content root.
    _write_hooks(profiles_root, profile)
    without_pattern = hs.score(
        "acme-augmentation",
        "linkedin-text",
        text,
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
    )

    assert with_pattern == without_pattern

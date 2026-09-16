"""Contract tests for Phase 8 multi-source outcomes ingestion.

The skill body is the spec; these tests prove the spec documents Buffer MCP first,
native platform MCPs second, manual fallback third, and the required hook/predictor_band
tagging convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BODY = REPO / "plugin" / "skills" / "content-outcomes-sync" / "body_template.md"
SKILL_MD = REPO / "plugin" / "skills" / "content-outcomes-sync" / "SKILL.md"
MANIFEST = REPO / "gtm_core" / "skills" / "content_outcomes_sync.py"


def _text(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.parent.name} is withheld from this build (OSS carve)")
    return path.read_text(encoding="utf-8")


def test_body_mentions_all_three_sources_in_priority_order():
    body = _text(BODY)
    assert "Buffer MCP" in body
    assert "Native platform MCPs" in body
    assert "Manual operator input" in body
    # Primary source is named first in the ordered list.
    assert body.index("Buffer MCP") < body.index("Native platform MCPs")
    assert body.index("Native platform MCPs") < body.index("Manual operator input")


def test_body_preserves_one_channel_per_call_rule():
    body = _text(BODY)
    assert "one org-wide call" in body
    assert "drops impressions" in body


def test_body_uses_predictor_band_tag_not_predictor_colon():
    body = _text(BODY)
    assert "predictor_band:high" in body
    assert "predictor_band:medium" in body
    assert "predictor_band:low" in body
    # The old tag convention must be gone to prevent drift.
    assert "predictor:high" not in body
    assert "predictor:mid" not in body
    assert "predictor:low" not in body


def test_body_tags_hook_id():
    body = _text(BODY)
    assert "--tag hook:<hook_id>" in body
    assert "--tag hook:<id>" in body


def test_body_tags_format_with_prefix_not_bare():
    """--tag <format> (unprefixed) is a defect: gtm_distill._tag_value(row, "format") and
    hook_score._compute_prior both join on the "format:" prefix, so an unprefixed tag makes
    every content row aggregate as format="unknown". Guards the fix landed alongside the
    X tweet-pattern catalog."""
    body = _text(BODY)
    assert "--tag format:<format>" in body
    assert "--tag <format> \\" not in body
    assert "--tag <format>\n" not in body


def test_body_tags_portfolio_axes_with_prefix_not_bare():
    """pillar / journey_stage / goal have the same defect format had, and for the same reason:
    gtm_distill._tag_value(row, prefix) matches only tag.startswith(f"{prefix}:"), so a bare
    tag is invisible and the row falls into that axis's baseline instead of its bucket. The
    format: fix landed on this very line and left its three neighbours bare — this test is
    what makes that non-repeatable."""
    body = _text(BODY)
    for key in ("pillar", "journey_stage", "goal"):
        assert f"--tag {key}:<{key}>" in body
        assert f"--tag <{key}>" not in body


def test_body_tags_pattern_id():
    body = _text(BODY)
    assert "--tag pattern:<pattern_id>" in body
    assert "pattern_performance.json" in body
    assert "axis_performance.json" in body


def test_body_records_retention_seconds_and_proxy_meta():
    body = _text(BODY)
    assert "outcome: retention_seconds" in body
    assert '"retention_proxy": true' in body
    assert '"confidence": "low"' in body


def test_skill_md_is_in_sync_with_body():
    if not BODY.is_file():
        pytest.skip("content-outcomes-sync is withheld from this build (OSS carve)")
    skill_md = _text(SKILL_MD)
    assert "Buffer MCP" in skill_md
    assert "predictor_band" in skill_md


def test_manifest_bumped_for_phase_8():
    from gtm_core.skills.content_outcomes_sync import SKILL

    # Phase 8 landed at 0.6.0 and the manifest may only move FORWARD from there. Pinned as a
    # floor, not an equality: a later, unrelated bump (0.7.0 wired the deterministic
    # `gtm_core.content_outcomes` producer into Step 3) is not a Phase-8 regression, and an
    # equality here made every legitimate bump look like one.
    assert tuple(int(x) for x in SKILL.version.split(".")) >= (0, 6, 0)
    assert SKILL.phase == "8"
    assert "Buffer MCP" in SKILL.description
    assert "predictor_band" in SKILL.description

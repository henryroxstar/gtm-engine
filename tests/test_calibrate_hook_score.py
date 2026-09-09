"""Tests for gtm_core.calibrate_hook_score (PRD Phase 9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import calibrate_hook_score as chs
from gtm_core import hook_score as hs
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


def test_load_score_records_filters_and_parses(tmp_profile: tuple[Path, Path, str]) -> None:
    _, content_root, profile = tmp_profile
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "hook_score",
            "value": 1,
            "tags": [
                "hook:acme-augmentation",
                "format:linkedin-text",
                "predictor_band:medium",
            ],
            "meta": {
                "score": 65,
                "components": {"retention": 70, "prior": 60, "pattern": 65},
                "weakest_dim": "prior",
                "fix": "Collect more outcomes.",
            },
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 100,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )

    rows = oc.read_outcomes(content_root, profile)
    records = chs.load_score_records(rows)
    assert len(records) == 1
    assert records[0]["hook_id"] == "acme-augmentation"
    assert records[0]["format"] == "linkedin-text"
    assert records[0]["band"] == "medium"
    assert records[0]["components"]["retention"] == 70


def test_actual_performance_aggregates_by_hook_format(tmp_profile: tuple[Path, Path, str]) -> None:
    _, content_root, profile = tmp_profile
    for _ in range(2):
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
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "published",
            "value": 1,
            "tags": ["hook:acme-augmentation", "format:reel"],
        },
    )

    rows = oc.read_outcomes(content_root, profile)
    perf = chs.actual_performance(rows, "acme-augmentation", "reel")
    assert perf["impressions"] == 1000
    assert perf["engagements"] == 100
    assert perf["posts"] == 1
    assert perf["engagement_rate"] == pytest.approx(0.1, abs=0.001)


def test_fit_weights_falls_back_when_sparse() -> None:
    records = [
        {
            "components": {"retention": 80, "prior": 60, "pattern": 90},
            "actual_engagement_rate": 0.1,
        }
    ]
    weights, diagnostics = chs.fit_weights(records)
    assert weights == hs.DEFAULT_WEIGHTS
    assert diagnostics["fallback"] is True
    assert "need at least" in diagnostics["reason"]


def test_fit_weights_prefers_retention_when_it_predicts(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """Synthetic dataset where retention component strongly predicts performance."""
    records = []
    for i in range(10):
        retention = 40 + i * 6  # 40–94
        performance = retention / 500.0  # strong correlation
        records.append(
            {
                "components": {
                    "retention": retention,
                    "prior": 50,
                    "pattern": 50,
                },
                "actual_engagement_rate": performance,
            }
        )

    weights, diagnostics = chs.fit_weights(records)
    assert not diagnostics["fallback"]
    assert weights["retention"] > weights["prior"]
    assert weights["retention"] > weights["pattern"]


def test_calibrate_writes_weights_and_report(tmp_profile: tuple[Path, Path, str]) -> None:
    _, content_root, profile = tmp_profile

    # Score record.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "hook_score",
            "value": 1,
            "tags": [
                "hook:acme-augmentation",
                "format:linkedin-text",
                "predictor_band:high",
            ],
            "meta": {
                "score": 85,
                "components": {"retention": 90, "prior": 80, "pattern": 85},
            },
        },
    )

    # Matching performance.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 1000,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "likes",
            "value": 100,
            "tags": ["hook:acme-augmentation", "format:linkedin-text"],
        },
    )

    report = chs.calibrate(content_root, profile)

    assert report["records_matched"] == 1
    weights_path = content_root / profile / "models" / "hook_score_weights.json"
    report_path = content_root / profile / "models" / "hook_score_calibration.json"
    assert weights_path.is_file()
    assert report_path.is_file()
    assert json.loads(weights_path.read_text(encoding="utf-8")) == report["weights"]


def test_calibrate_ignores_unmatched_score_records(tmp_profile: tuple[Path, Path, str]) -> None:
    _, content_root, profile = tmp_profile
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "hook_score",
            "value": 1,
            "tags": ["hook:acme-augmentation", "format:reel", "predictor_band:high"],
            "meta": {
                "score": 85,
                "components": {"retention": 90, "prior": 80, "pattern": 85},
            },
        },
    )
    # No matching performance rows.
    report = chs.calibrate(content_root, profile)
    assert report["records_matched"] == 0
    assert report["diagnostics"]["fallback"] is True

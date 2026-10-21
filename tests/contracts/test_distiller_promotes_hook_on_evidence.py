"""Contract test: content distiller promotes/demotes hooks on evidence.

Verifies the G-A+-9 learning loop: `gtm_core.gtm_distill.summarize_content` and
`distill_content` read `outcomes.jsonl`, compare hook-level engagement to the
profile baseline, and emit promote/demote candidates only when the evidence
thresholds are met.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import gtm_distill as gd
from gtm_core import hooks as hk
from gtm_core import outcomes as oc


def _setup_profile(tmp_path: Path, profile: str = "testco") -> tuple[Path, Path]:
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    (profiles_root / profile / "knowledge").mkdir(parents=True)
    (content_root / profile).mkdir(parents=True)

    bank = hk.HookBank(
        hooks=[
            hk.Hook(id="winner", angle="a", payoff_promise="b"),
            hk.Hook(id="loser", angle="c", payoff_promise="d"),
            hk.Hook(id="noisy", angle="e", payoff_promise="f"),
        ]
    )
    hk.save_hooks(profiles_root, profile, bank)
    return profiles_root, content_root


def _append_outcome(
    content_root: Path, profile: str, outcome: str, value: float, tags: list[str]
) -> None:
    oc.append_outcome(
        content_root,
        profile,
        {"channel": "linkedin", "outcome": outcome, "value": value, "tags": tags},
    )


def test_distiller_emits_promote_candidate_when_hook_beats_baseline(tmp_path: Path) -> None:
    profiles_root, content_root = _setup_profile(tmp_path)
    profile = "testco"

    # Baseline: 2000 impressions, 200 engagements = 10% rate.
    for _ in range(20):
        _append_outcome(content_root, profile, "impressions", 100, [])
        _append_outcome(content_root, profile, "likes", 10, [])

    # Winner hook: 1000 impressions, 200 engagements = 20% rate (2x baseline).
    for _ in range(10):
        _append_outcome(content_root, profile, "impressions", 100, ["hook:winner"])
        _append_outcome(content_root, profile, "likes", 20, ["hook:winner"])

    summary = gd.summarize_content(
        content_root, profile, profiles_root=profiles_root, min_impressions=500, lift=1.3
    )

    promote_ids = {c["hook_id"] for c in summary["promote_candidates"]}
    assert "winner" in promote_ids
    winner = next(c for c in summary["promote_candidates"] if c["hook_id"] == "winner")
    assert winner["engagement_rate"] == pytest.approx(0.2, abs=0.001)
    assert winner["baseline_rate"] == pytest.approx(0.1, abs=0.001)
    assert winner["direction"] == "outperforms"


def test_distiller_emits_demote_candidate_when_hook_trails_baseline(tmp_path: Path) -> None:
    profiles_root, content_root = _setup_profile(tmp_path)
    profile = "testco"

    # Baseline: 3000 impressions, 300 engagements = 10% rate.
    for _ in range(30):
        _append_outcome(content_root, profile, "impressions", 100, [])
        _append_outcome(content_root, profile, "likes", 10, [])

    # Loser hook: 3000 impressions, 100 engagements = ~3.3% rate, 3 posts.
    for _ in range(3):
        _append_outcome(content_root, profile, "published", 1, ["hook:loser"])
    for _ in range(30):
        _append_outcome(content_root, profile, "impressions", 100, ["hook:loser"])
        _append_outcome(content_root, profile, "likes", 3.33, ["hook:loser"])

    summary = gd.summarize_content(content_root, profile, profiles_root=profiles_root, lift=1.3)

    demote_ids = {c["hook_id"] for c in summary["demote_candidates"]}
    assert "loser" in demote_ids
    loser = next(c for c in summary["demote_candidates"] if c["hook_id"] == "loser")
    assert loser["baseline_rate"] == pytest.approx(0.1, abs=0.001)
    assert loser["direction"] == "underperforms"


def test_distiller_ignores_sparse_or_inconclusive_hooks(tmp_path: Path) -> None:
    profiles_root, content_root = _setup_profile(tmp_path)
    profile = "testco"

    # Baseline.
    for _ in range(10):
        _append_outcome(content_root, profile, "impressions", 100, [])
        _append_outcome(content_root, profile, "likes", 10, [])

    # Noisy hook: only 100 impressions (below min_impressions default 500).
    _append_outcome(content_root, profile, "impressions", 100, ["hook:noisy"])
    _append_outcome(content_root, profile, "likes", 50, ["hook:noisy"])

    summary = gd.summarize_content(
        content_root, profile, profiles_root=profiles_root, min_impressions=500, lift=1.3
    )

    assert "noisy" not in {c["hook_id"] for c in summary["promote_candidates"]}
    assert "noisy" not in {c["hook_id"] for c in summary["demote_candidates"]}


def test_distill_content_writes_model_files(tmp_path: Path) -> None:
    profiles_root, content_root = _setup_profile(tmp_path)
    profile = "testco"

    for _ in range(20):
        _append_outcome(content_root, profile, "impressions", 100, [])
        _append_outcome(content_root, profile, "likes", 10, [])
    for _ in range(10):
        _append_outcome(content_root, profile, "impressions", 100, ["hook:winner"])
        _append_outcome(content_root, profile, "likes", 20, ["hook:winner"])

    paths = gd.distill_content(content_root, profile, profiles_root=profiles_root, period="2026-08")

    assert paths["hook_performance"].exists()
    assert paths["promote_candidates"].exists()
    assert paths["demote_candidates"].exists()
    assert paths["fatigued_hooks"].exists()

    perf = json.loads(paths["hook_performance"].read_text(encoding="utf-8"))
    assert perf["period"] == "2026-08"
    assert "winner" in perf["by_hook"]

    promote = json.loads(paths["promote_candidates"].read_text(encoding="utf-8"))
    assert any(c["hook_id"] == "winner" for c in promote)

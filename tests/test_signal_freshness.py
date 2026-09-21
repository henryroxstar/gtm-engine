"""Tests for gtm_core.signal_freshness (re-validating signal_observed freshness on existing rows)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from gtm_core.signal_freshness import audit_signal_freshness, revalidate_profile_signals


def test_fresh_signal_passes() -> None:
    today = date(2026, 9, 18)
    items = [
        {
            "company": "Acme Robotics",
            "signal_observed": (today - timedelta(days=30)).isoformat(),
            "verdict": "send",
            "why_now": "Launched autonomous forklift fleet",
        }
    ]

    res = audit_signal_freshness(items, as_of=today, max_age_days=210)
    assert len(res.fresh) == 1
    assert len(res.stale) == 0
    assert len(res.missing) == 0
    assert items[0]["verdict"] == "send"


def test_stale_signal_demoted() -> None:
    today = date(2026, 9, 18)
    items = [
        {
            "company": "Legacy Automation",
            "signal_observed": (today - timedelta(days=220)).isoformat(),
            "verdict": "send",
            "why_now": "Series B funding closed",
        }
    ]

    res = audit_signal_freshness(items, as_of=today, max_age_days=210, mutate=True)
    assert len(res.fresh) == 0
    assert len(res.stale) == 1
    assert res.stale[0]["company"] == "Legacy Automation"
    # Verdict must be downgraded to re-angle with staleness reason
    assert items[0]["verdict"] == "re-angle"
    assert "signal_stale_220d" in items[0]["verdict_reason"]


def test_boundary_conditions() -> None:
    today = date(2026, 9, 18)
    items = [
        # Exactly 210 days -> fresh (inclusive boundary)
        {"company": "Day 210 Co", "signal_observed": (today - timedelta(days=210)).isoformat()},
        # 211 days -> stale
        {"company": "Day 211 Co", "signal_observed": (today - timedelta(days=211)).isoformat()},
    ]

    res = audit_signal_freshness(items, as_of=today, max_age_days=210)
    assert len(res.fresh) == 1
    assert res.fresh[0]["company"] == "Day 210 Co"
    assert len(res.stale) == 1
    assert res.stale[0]["company"] == "Day 211 Co"


def test_missing_or_invalid_observed_date() -> None:
    today = date(2026, 9, 18)
    items = [
        {"company": "No Signal Co"},
        {"company": "Bad Date Co", "signal_observed": "not-a-date"},
    ]

    res = audit_signal_freshness(items, as_of=today, max_age_days=210)
    assert len(res.missing) == 2


def test_revalidate_profile_signals_file_mutation(tmp_path: Path) -> None:
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)
    latest_file = prospects_dir / "latest.json"

    today = date(2026, 9, 18)
    stale_date = (today - timedelta(days=250)).isoformat()
    fresh_date = (today - timedelta(days=10)).isoformat()

    initial_items = [
        {"company": "Stale Co", "signal_observed": stale_date, "verdict": "send"},
        {"company": "Fresh Co", "signal_observed": fresh_date, "verdict": "send"},
    ]
    latest_file.write_text(json.dumps(initial_items), encoding="utf-8")

    # 1. Dry run: flags stale but does not mutate file
    res_dry = revalidate_profile_signals(
        profile,
        as_of=today,
        dry_run=True,
        content_root=tmp_path,
    )
    assert len(res_dry.stale) == 1
    data_after_dry = json.loads(latest_file.read_text(encoding="utf-8"))
    assert data_after_dry[0]["verdict"] == "send"

    # 2. Live run: mutates file
    res_live = revalidate_profile_signals(
        profile,
        as_of=today,
        dry_run=False,
        content_root=tmp_path,
    )
    assert len(res_live.stale) == 1
    data_after_live = json.loads(latest_file.read_text(encoding="utf-8"))
    assert data_after_live[0]["verdict"] == "re-angle"
    assert "signal_stale" in data_after_live[0]["verdict_reason"]
    assert data_after_live[1]["verdict"] == "send"


def test_revalidate_profile_signals_canonical_dict_format(tmp_path: Path) -> None:
    profile = "test-tenant-dict"
    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)
    latest_file = prospects_dir / "latest.json"

    today = date(2026, 9, 18)
    stale_date = (today - timedelta(days=300)).isoformat()

    dict_data = {
        "meta": {"version": "1.0"},
        "items": [
            {"company": "Dict Stale Co", "signal_observed": stale_date, "verdict": "send"},
        ],
    }
    latest_file.write_text(json.dumps(dict_data), encoding="utf-8")

    res = revalidate_profile_signals(profile, as_of=today, dry_run=False, content_root=tmp_path)
    assert len(res.stale) == 1

    saved = json.loads(latest_file.read_text(encoding="utf-8"))
    assert "items" in saved
    assert saved["meta"] == {"version": "1.0"}
    assert saved["items"][0]["verdict"] == "re-angle"

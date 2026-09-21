"""Tests for gtm_core.prospect_guards (deterministic CLI runtime guards)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.prospect_guards import (
    check_budget_cap,
    check_generic_lane_cap,
    check_interpreter_sanity,
    main,
)


def test_interpreter_sanity_passes() -> None:
    ok, msg = check_interpreter_sanity()
    assert ok is True
    assert "ok" in msg.lower() or "paths" in msg.lower()


def test_generic_lane_cap_under_limit(tmp_path: Path) -> None:
    # 4 out of 10 is 40% <= 50% cap
    ok, msg = check_generic_lane_cap(
        "test-p", generic_count=4, total_count=10, content_root=tmp_path
    )
    assert ok is True
    assert "within cap" in msg


def test_generic_lane_cap_over_limit(tmp_path: Path) -> None:
    # 6 out of 10 is 60% > 50% cap
    ok, msg = check_generic_lane_cap(
        "test-p", generic_count=6, total_count=10, content_root=tmp_path
    )
    assert ok is False
    assert "exceeds cap" in msg


def test_generic_lane_cap_custom_setting(tmp_path: Path) -> None:
    # Custom cap of 0.25 in settings.json
    profile_dir = tmp_path / "custom-p"
    profile_dir.mkdir(parents=True, exist_ok=True)
    settings_file = profile_dir / "settings.json"
    settings_file.write_text(json.dumps({"generic_lane_share_cap": 0.25}), encoding="utf-8")

    # 3 out of 10 is 30% > 25%
    ok, msg = check_generic_lane_cap(
        "custom-p", generic_count=3, total_count=10, content_root=tmp_path
    )
    assert ok is False
    assert "exceeds cap 25.0%" in msg

    # 2 out of 10 is 20% <= 25%
    ok2, msg2 = check_generic_lane_cap(
        "custom-p", generic_count=2, total_count=10, content_root=tmp_path
    )
    assert ok2 is True


def test_generic_lane_cap_zero_total(tmp_path: Path) -> None:
    ok, msg = check_generic_lane_cap(
        "test-p", generic_count=0, total_count=0, content_root=tmp_path
    )
    assert ok is True


def test_budget_cap_enforcement(tmp_path: Path) -> None:
    # Setup mock profile with PROFILE.md
    profiles_root = tmp_path / "profiles"
    profile_dir = profiles_root / "acme"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_md = profile_dir / "PROFILE.md"
    profile_md.write_text(
        """```
company: "Acme Corp"
per_run_cap_usd: 10.0
monthly_tool_budget_usd: 50.0
```""",
        encoding="utf-8",
    )

    content_root = tmp_path / "content"
    prospects_dir = content_root / "acme"
    prospects_dir.mkdir(parents=True, exist_ok=True)

    # 1. Under per-run cap
    ok, msg = check_budget_cap(
        "acme",
        estimated_spend_usd=5.0,
        content_root=content_root,
        profiles_root=profiles_root,
    )
    assert ok is True

    # 2. Exceeds per-run cap (15.0 > 10.0)
    ok, msg = check_budget_cap(
        "acme",
        estimated_spend_usd=15.0,
        content_root=content_root,
        profiles_root=profiles_root,
    )
    assert ok is False
    assert "per_run_cap_usd" in msg

    # 3. Simulate existing costs in costs.jsonl reaching near monthly cap
    costs_jsonl = prospects_dir / "costs.jsonl"
    costs_jsonl.write_text(
        '{"ts": "2026-09-01T00:00:00Z", "cost_usd": 45.0}\n',
        encoding="utf-8",
    )

    # 8.0 estimated + 45.0 existing = 53.0 > 50.0 monthly cap
    ok, msg = check_budget_cap(
        "acme",
        estimated_spend_usd=8.0,
        content_root=content_root,
        profiles_root=profiles_root,
    )
    assert ok is False
    assert "monthly_tool_budget_usd" in msg


def test_cli_guards_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Test CLI interface
    assert main(["--check", "interpreter"]) == 0

    assert (
        main(
            [
                "--check",
                "generic-lane-cap",
                "--profile",
                "test",
                "--generic-count",
                "2",
                "--total-count",
                "10",
            ]
        )
        == 0
    )

    assert (
        main(
            [
                "--check",
                "generic-lane-cap",
                "--profile",
                "test",
                "--generic-count",
                "8",
                "--total-count",
                "10",
            ]
        )
        == 2
    )

"""End-to-end integration test suite for the hardened prospecting pipeline.

Exercises the full pipeline: locking, preflight, budget guards, state machine,
signal verification, circuit breaker waterfall enrichment, ledger merge,
drop suppression, PII retention, and structured run summary generation.
Uses fictional data only (§R9).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.circuit_breaker import CircuitBreaker, CircuitBreakerState
from gtm_core.ledger_integrity import check_verdict_leak, enforce_drop_suppression
from gtm_core.prospect_guards import (
    check_budget_cap,
    check_generic_lane_cap,
    check_interpreter_sanity,
)
from gtm_core.prospect_paths import run_summary_json, suppression_ledger
from gtm_core.prospects_state import upsert_latest
from gtm_core.retention_sweep import sweep_stale_pii
from gtm_core.run_lock import RunLockBusy, prospect_run_lock
from gtm_core.run_state import RunState, load_run_state, save_run_state
from gtm_core.run_summary import build_run_summary, save_run_summary
from gtm_core.signal_freshness import audit_signal_freshness
from gtm_core.suppression import load_index


def _get_e2e_discovered_accounts() -> list[dict]:
    return [
        {
            "company": "Apex Robotics",
            "domain": "apexrobotics.example",
            "segment": "enterprise",
            "signal_source_url": "https://apexrobotics.example/news/launch",
            "signal_observed": "2026-08-15",
            "signal_evidence": "Apex launched new autonomous multi-agent fleet.",
            "why_now": "Launched autonomous multi-agent fleet across commercial sites",
            "signal_subject": "Apex Robotics",
            "signal_agent_kind": "ai",
            "category_relation": "prospect",
            "verdict": "send",
            "lane": "personalised",
        },
        {
            "company": "Beacon Automation",
            "domain": "beaconautomation.example",
            "segment": "startup",
            "signal_source_url": "https://beaconautomation.example/press/compliance",
            "signal_observed": "2026-08-20",
            "signal_evidence": "Beacon achieves ISO compliance for agentic runtime.",
            "why_now": "Achieved ISO compliance for agentic runtime",
            "signal_subject": "Beacon Automation",
            "signal_agent_kind": "ai",
            "category_relation": "prospect",
            "verdict": "send",
            "lane": "personalised",
        },
        {
            "company": "Crestline Health",
            "domain": "crestlinehealth.example",
            "segment": "enterprise",
            "why_now": "",
            "category_relation": "prospect",
            "verdict": "re-angle",
            "lane": "generic",
            "lane_reason": "no-signal-standard-body",
        },
        {
            "company": "Delta Solutions",
            "domain": "deltasolutions.example",
            "segment": "startup",
            "why_now": "Launched competitor product",
            "category_relation": "competitor",
            "verdict": "drop",
            "verdict_reason": "competitor",
            "lane": "generic",
            "email": "ceo@deltasolutions.example",
        },
        {
            "company": "Epsilon Systems",
            "domain": "epsilonsystems.example",
            "segment": "startup",
            "signal_source_url": "https://epsilonsystems.example/news/expansion",
            "signal_observed": "2026-09-01",
            "signal_evidence": "Epsilon expands agent deployment.",
            "why_now": "Expanded agent deployment to APAC region",
            "category_relation": "prospect",
            "verdict": "send",
            "lane": "personalised",
        },
        {
            "company": "Foxtrot AI",
            "domain": "foxtrotai.example",
            "segment": "startup",
            "signal_source_url": "https://foxtrotai.example/archive",
            "signal_observed": "2025-12-01",
            "signal_evidence": "Old funding announcement.",
            "why_now": "Raised Series A round",
            "category_relation": "prospect",
        },
    ]


def test_full_pipeline_e2e_lifecycle(tmp_path: Path) -> None:
    profile = "synthetic-corp"
    run_id = "run-e2e-20260918"
    content_root = tmp_path / "content"
    profiles_root = tmp_path / "profiles"

    profile_dir = profiles_root / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "PROFILE.md").write_text(
        '```\ncompany: "Synthetic Dynamics"\nper_run_cap_usd: 25.0\nmonthly_tool_budget_usd: 100.0\n```',
        encoding="utf-8",
    )

    with prospect_run_lock(profile, run_id=run_id, content_root=content_root) as lock_info:
        assert lock_info.run_id == run_id

        # Verify concurrent run rejection
        with pytest.raises(RunLockBusy):
            with prospect_run_lock(
                profile, run_id="run-concurrent", content_root=content_root, blocking=False
            ):
                pass

        # Runtime guards
        ok_interp, _ = check_interpreter_sanity()
        assert ok_interp is True
        ok_budget, _ = check_budget_cap(
            profile,
            estimated_spend_usd=12.0,
            content_root=content_root,
            profiles_root=profiles_root,
        )
        assert ok_budget is True

        # Run State Machine: init & discovery
        state = RunState.new(profile=profile, mode="full", run_id=run_id)
        state.start_stage("init")
        state.complete_stage("init", metrics={"connectors": {"vibe": "ok", "rocketreach": "ok"}})

        state.start_stage("discovery")
        discovered_accounts = _get_e2e_discovered_accounts()
        state.complete_stage("discovery", metrics={"accounts_discovered": len(discovered_accounts)})

        # Signal verification & freshness audit
        state.start_stage("signal_hunt")
        assert len(check_verdict_leak(discovered_accounts)) == 0
        freshness = audit_signal_freshness(discovered_accounts, mutate=True)
        assert len(freshness.stale) == 1
        assert discovered_accounts[5]["verdict"] == "re-angle"
        state.complete_stage("signal_hunt")

        # Gate & Score
        state.start_stage("gate_score")
        ok_lane, _ = check_generic_lane_cap(
            profile, generic_count=1, total_count=4, content_root=content_root
        )
        assert ok_lane is True
        state.complete_stage("gate_score", metrics={"accounts_gated": 4, "accounts_dropped": 1})

        # Waterfall enrichment with circuit breaker
        state.start_stage("enrichment")
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_s=60.0)
        enriched = 0
        for acc in [discovered_accounts[0], discovered_accounts[1], discovered_accounts[4]]:
            if cb.is_available("rocketreach"):
                cb.record_success("rocketreach")
                acc["email"] = f"lead@{acc['domain']}"
                acc["status"] = "contact-resolved"
                enriched += 1
        state.complete_stage("enrichment", metrics={"accounts_enriched": enriched, "cost_usd": 3.0})

        # Output & state persistence
        state.start_stage("output")
        summary = upsert_latest(
            profile, discovered_accounts, source_run=run_id, content_root=content_root
        )
        assert summary["total"] == len(discovered_accounts)

        # Drop suppression verification
        assert (
            enforce_drop_suppression(profile, discovered_accounts, content_root=content_root) == 1
        )
        idx = load_index(suppression_ledger(profile, content_root=content_root))
        assert idx.match({"email": "ceo@deltasolutions.example"}) is not None

        state.complete_stage("output", metrics={"accounts_ready": 3, "accounts_generic": 1})
        save_run_state(state, content_root / profile / "prospects" / "run_state.json")

        # Run summary creation
        run_sum = build_run_summary(profile, run_id=run_id, content_root=content_root)
        assert run_sum.accounts_discovered == 6
        assert run_sum.accounts_ready == 3
        save_run_summary(run_sum, run_summary_json(profile, content_root))

    # Resumption state & PII retention
    loaded = load_run_state(content_root / profile / "prospects" / "run_state.json")
    assert loaded is not None and loaded.is_completed is True
    assert len(sweep_stale_pii(profile, ttl_days=7, content_root=content_root).errors) == 0


def test_circuit_breaker_trips_mid_run_and_falls_back() -> None:
    cb = CircuitBreaker(failure_threshold=3, reset_timeout_s=300.0)
    for _ in range(3):
        cb.record_failure("rocketreach")

    assert cb.is_available("rocketreach") is False
    assert cb.state("rocketreach") == CircuitBreakerState.OPEN
    assert cb.is_available("vibe") is True
    cb.record_success("vibe")
    assert cb.state("vibe") == CircuitBreakerState.CLOSED


def test_resume_from_failed_stage() -> None:
    state = RunState.new(profile="test-resume-profile", run_id="run-partial")
    state.start_stage("init")
    state.complete_stage("init")
    state.start_stage("discovery")
    state.complete_stage("discovery", metrics={"accounts_discovered": 10})
    state.start_stage("signal_hunt")
    state.fail_stage("signal_hunt", "API Timeout on signal search")

    assert state.resume_from() == "signal_hunt"
    assert state.can_resume("discovery") is True
    assert state.can_resume("signal_hunt") is False

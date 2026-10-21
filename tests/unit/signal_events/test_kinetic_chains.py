import ast
import json
import os
from pathlib import Path

from gtm_core.paths import resolve_knowledge_file, resolve_profiles_root
from gtm_core.signal_events.contracts import BusinessEvent
from gtm_core.signal_events.kinetic import (
    KineticChainMatch,
    KineticChainRule,
    detect_kinetic_chains,
    load_kinetic_chains_config,
)


def test_load_config_missing_file_returns_empty_dict(tmp_path: Path):
    missing_path = tmp_path / "nonexistent-chains.toml"
    rules = load_kinetic_chains_config(missing_path)
    assert rules == {}


def test_load_config_valid_toml(tmp_path: Path):
    toml_content = """
[chains.mandate]
events = ["regulatory", "cluster_expansion"]
max_duration_days = 90
description = "Regulatory action followed by hiring cluster"
"""
    config_file = tmp_path / "kinetic-chains.toml"
    config_file.write_text(toml_content, encoding="utf-8")

    rules = load_kinetic_chains_config(config_file)
    assert "mandate" in rules
    rule = rules["mandate"]
    assert isinstance(rule, KineticChainRule)
    assert rule.name == "mandate"
    assert rule.events == ("regulatory", "cluster_expansion")
    assert rule.max_duration_days == 90
    assert rule.description == "Regulatory action followed by hiring cluster"


def test_load_config_malformed_block_skipped_gracefully(tmp_path: Path):
    toml_content = """
[chains.invalid_missing_events]
max_duration_days = 60

[chains.valid_chain]
events = ["regulatory", "hiring"]
max_duration_days = 45
"""
    config_file = tmp_path / "kinetic-chains.toml"
    config_file.write_text(toml_content, encoding="utf-8")

    rules = load_kinetic_chains_config(config_file)
    assert "invalid_missing_events" not in rules
    assert "valid_chain" in rules
    assert rules["valid_chain"].events == ("regulatory", "hiring")


def test_kinetic_module_zero_network_egress():
    """Verify gtm_core.signal_events.kinetic contains zero network library imports (Test Plan §3.B)."""
    module_path = Path("gtm_core/signal_events/kinetic.py")
    assert module_path.is_file()
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    forbidden_modules = {"httpx", "requests", "urllib.request", "aiohttp", "socket"}
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module)

    for mod in forbidden_modules:
        assert not any(imported.startswith(mod) for imported in imported_names), (
            f"Forbidden network library {mod!r} imported in {module_path}"
        )


def _make_event(
    event_type: str,
    date_str: str,
    domain: str | None = "acme.example",
    company: str = "Acme Corp",
) -> BusinessEvent:
    return BusinessEvent(
        company_name=company,
        company_domain=domain,
        event_type=event_type,
        event_date=date_str,
        headline=f"Test {event_type} headline",
        snippet=f"Test {event_type} snippet",
        source_url="https://example.com/event",
        provider="test_sweep",
    )


def test_detect_kinetic_chain_success_chronological():
    events = [
        _make_event("regulatory", "2026-08-01"),
        _make_event("cluster_expansion", "2026-08-20"),
    ]
    rule = KineticChainRule(
        name="mandate",
        events=("regulatory", "cluster_expansion"),
        max_duration_days=90,
    )
    matches = detect_kinetic_chains(events, config={"mandate": rule})
    assert len(matches) == 1
    m = matches[0]
    assert isinstance(m, KineticChainMatch)
    assert m.chain_name == "mandate"
    assert m.company_domain == "acme.example"
    assert len(m.matched_events) == 2
    assert "Regulatory" in m.compound_why_now
    assert "AI Hiring Expansion" in m.compound_why_now


def test_detect_kinetic_chain_same_day_permitted():
    events = [
        _make_event("regulatory", "2026-09-01"),
        _make_event("cluster_expansion", "2026-09-01"),
    ]
    rule = KineticChainRule(
        name="mandate",
        events=("regulatory", "cluster_expansion"),
        max_duration_days=90,
    )
    matches = detect_kinetic_chains(events, config={"mandate": rule})
    assert len(matches) == 1
    assert matches[0].chain_name == "mandate"


def test_detect_kinetic_chain_rejects_reverse_chronology():
    # cluster_expansion happened BEFORE regulatory
    events = [
        _make_event("cluster_expansion", "2026-08-01"),
        _make_event("regulatory", "2026-08-20"),
    ]
    rule = KineticChainRule(
        name="mandate",
        events=("regulatory", "cluster_expansion"),
        max_duration_days=90,
    )
    matches = detect_kinetic_chains(events, config={"mandate": rule})
    assert len(matches) == 0


def test_detect_kinetic_chain_rejects_exceeded_duration():
    # 106 days apart when limit is 90 days
    events = [
        _make_event("regulatory", "2026-05-01"),
        _make_event("cluster_expansion", "2026-08-15"),
    ]
    rule = KineticChainRule(
        name="mandate",
        events=("regulatory", "cluster_expansion"),
        max_duration_days=90,
    )
    matches = detect_kinetic_chains(events, config={"mandate": rule})
    assert len(matches) == 0


def test_detect_kinetic_chain_two_pass_domain_alias_normalization():
    # Event 1 has domain; Event 2 for same clean company name has domain=None
    events = [
        _make_event("regulatory", "2026-08-01", domain="acme.example", company="Acme Corp"),
        _make_event("cluster_expansion", "2026-08-10", domain=None, company="Acme"),
    ]
    rule = KineticChainRule(
        name="mandate",
        events=("regulatory", "cluster_expansion"),
        max_duration_days=90,
    )
    matches = detect_kinetic_chains(events, config={"mandate": rule})
    assert len(matches) == 1
    assert matches[0].company_domain == "acme.example"


def test_evaluator_never_modifies_ledger_mtime(tmp_path: Path):
    """Test Plan §4.1: Assert mtime of events-YYYY-MM.jsonl is untouched after evaluation."""
    ledger_file = tmp_path / "events-2026-10.jsonl"
    ev = _make_event("regulatory", "2026-10-01")
    ledger_file.write_text(
        json.dumps(
            {
                "company_name": ev.company_name,
                "company_domain": ev.company_domain,
                "event_type": ev.event_type,
                "event_date": ev.event_date,
                "headline": ev.headline,
                "snippet": ev.snippet,
                "source_url": ev.source_url,
                "provider": ev.provider,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    initial_mtime = os.path.getmtime(ledger_file)

    rule = KineticChainRule(
        name="mandate",
        events=("regulatory", "cluster_expansion"),
        max_duration_days=90,
    )
    matches = detect_kinetic_chains([ev], config={"mandate": rule})
    assert matches == []
    final_mtime = os.path.getmtime(ledger_file)

    assert initial_mtime == final_mtime


def test_signal_events_package_exports():
    import gtm_core.signal_events as se

    assert hasattr(se, "detect_kinetic_chains")
    assert hasattr(se, "load_kinetic_chains_config")
    assert hasattr(se, "KineticChainMatch")
    assert hasattr(se, "KineticChainRule")
    assert hasattr(se, "detect_chains_for_profile")


def test_cli_file_line_budget_strictly_guarded():
    """Verify gtm_core.signal_events.cli stays under the 500 line budget (§R10)."""
    cli_path = Path("gtm_core/signal_events/cli.py")
    line_count = len(cli_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 500, f"cli.py exceeded 500 lines: {line_count}"


def test_tenant_kinetic_chains_config_resolves_and_parses():
    profiles_root = resolve_profiles_root()
    template_toml = resolve_knowledge_file(profiles_root, "_template", "kinetic-chains.toml")
    assert template_toml.is_file()

    template_rules = load_kinetic_chains_config(template_toml)
    assert len(template_rules) == 1
    assert "mandate" in template_rules
    rule = template_rules["mandate"]
    assert rule.events == ("regulatory", "cluster_expansion")
    assert rule.max_duration_days == 90


def test_detect_chains_for_profile_synthesizes_clusters_for_mvp_chain(tmp_path: Path):
    """Critical 1: Verify detect_chains_for_profile synthesizes cluster_expansion from raw hiring events."""
    from gtm_core.signal_events.kinetic import detect_chains_for_profile

    content_root = tmp_path / "content"
    profiles_root = tmp_path / "profiles"

    # Create tenant knowledge with mandate chain
    sample_knowledge = profiles_root / "sample_tenant" / "knowledge"
    sample_knowledge.mkdir(parents=True, exist_ok=True)
    (sample_knowledge / "kinetic-chains.toml").write_text(
        """
[chains.mandate]
events = ["regulatory", "cluster_expansion"]
max_duration_days = 90
""",
        encoding="utf-8",
    )

    # Create rolling ledger with 1 regulatory event + 2 distinct AI hiring events
    signals_dir = content_root / "sample_tenant" / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    events_file = signals_dir / "events-2026-10.jsonl"

    raw_events = [
        {
            "company_name": "Acme Corp",
            "company_domain": "acme.example",
            "event_type": "regulatory",
            "event_date": "2026-08-01",
            "headline": "Regulatory Inquiry Opened",
            "snippet": "Compliance audit initiated.",
            "source_url": "https://example.com/reg",
            "provider": "regulatory_sweep",
        },
        {
            "company_name": "Acme Corp",
            "company_domain": "acme.example",
            "event_type": "hiring",
            "event_date": "2026-08-15",
            "headline": "Senior AI Agent Security Architect",
            "snippet": "Hiring LangChain and CrewAI engineer",
            "source_url": "https://example.com/job1",
            "provider": "firecrawl",
            "meta": {"frameworks": ["LangChain"]},
        },
        {
            "company_name": "Acme",
            "company_domain": None,
            "event_type": "hiring",
            "event_date": "2026-08-20",
            "headline": "AI Governance Lead",
            "snippet": "Hiring agent governance lead",
            "source_url": "https://example.com/job2",
            "provider": "firecrawl",
            "meta": {"frameworks": ["CrewAI"]},
        },
    ]
    with open(events_file, "w", encoding="utf-8") as f:
        for ev in raw_events:
            f.write(json.dumps(ev) + "\n")

    matches = detect_chains_for_profile(
        content_root=content_root,
        profiles_root=profiles_root,
        profile="sample_tenant",
        days=90,
    )
    assert len(matches) == 1
    m = matches[0]
    assert m.chain_name == "mandate"
    assert m.company_domain == "acme.example"
    assert len(m.matched_events) == 2
    assert m.matched_events[0].event_type == "regulatory"
    assert m.matched_events[1].event_type == "cluster_expansion"


def test_detect_kinetic_chain_malformed_and_non_iso_date_resilience():
    """Important 3: Unparseable/malformed dates must not crash evaluation with uncaught ValueError."""
    events = [
        _make_event("regulatory", "not-a-date"),
        _make_event("regulatory", "2026-08-01"),
        _make_event("cluster_expansion", "2026-08-20"),
    ]
    rule = KineticChainRule(
        name="mandate", events=("regulatory", "cluster_expansion"), max_duration_days=90
    )
    matches = detect_kinetic_chains(events, config={"mandate": rule})
    assert len(matches) == 1
    assert matches[0].matched_events[0].event_date == "2026-08-01"


def test_detect_kinetic_chain_iso_datetime_and_date_ordering():
    """Important 3: ISO datetime strings and date strings for same-day should compare as same-day."""
    events = [
        _make_event("regulatory", "2026-09-01T14:30:00Z"),
        _make_event("cluster_expansion", "2026-09-01"),
    ]
    rule = KineticChainRule(
        name="mandate", events=("regulatory", "cluster_expansion"), max_duration_days=90
    )
    matches = detect_kinetic_chains(events, config={"mandate": rule})
    assert len(matches) == 1
    assert matches[0].chain_name == "mandate"


def test_detect_kinetic_chain_duplicate_event_deduplication():
    """Important 4: Duplicate events in ledger should be deduplicated per account."""
    dup_event = _make_event("regulatory", "2026-08-01")
    events = [
        dup_event,
        dup_event,  # Duplicate
        _make_event("cluster_expansion", "2026-08-20"),
    ]
    rule = KineticChainRule(
        name="mandate", events=("regulatory", "cluster_expansion"), max_duration_days=90
    )
    matches = detect_kinetic_chains(events, config={"mandate": rule})
    assert len(matches) == 1


def test_detect_chains_for_profile_overlay_gating(tmp_path: Path):
    """Important 5: Overlay access must be admitted via experiments gate."""
    import pytest

    from gtm_core.experiments import OverlayError
    from gtm_core.signal_events.kinetic import detect_chains_for_profile

    content_root = tmp_path / "content"
    profiles_root = tmp_path / "profiles"

    with pytest.raises(OverlayError, match="overlays are disabled|no such experiment"):
        detect_chains_for_profile(
            content_root=content_root,
            profiles_root=profiles_root,
            profile="sample_tenant",
            overlay="unadmitted_experiment",
        )

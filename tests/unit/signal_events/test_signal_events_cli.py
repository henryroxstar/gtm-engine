import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from gtm_core.signal_events.cli import (
    _stage_token_safely,
    detect_clusters,
    execute_probe,
    execute_sweep,
    read_recent_events,
    render_hook_preview,
)
from gtm_core.signal_events.contracts import BusinessEvent


def test_read_recent_events_spans_month_boundary(tmp_path):
    signals_dir = tmp_path / "content" / "test_tenant" / "signals"
    signals_dir.mkdir(parents=True)

    file_prev = signals_dir / "events-2026-09.jsonl"
    ev1 = BusinessEvent(
        company_name="Acme Financial",
        company_domain=None,
        event_type="hiring",
        event_date="2026-09-28",
        headline="Hiring AI Lead",
        snippet="Snippet 1",
        source_url="https://example.com/1",
        provider="ats_sweep",
    )
    file_prev.write_text(json.dumps(ev1.__dict__) + "\n")

    file_curr = signals_dir / "events-2026-10.jsonl"
    ev2 = BusinessEvent(
        company_name="Acme Financial Inc.",
        company_domain=None,
        event_type="hiring",
        event_date="2026-10-02",
        headline="Hiring LangGraph Dev",
        snippet="Snippet 2",
        source_url="https://example.com/2",
        provider="ats_sweep",
    )
    file_curr.write_text(json.dumps(ev2.__dict__) + "\n")

    events = read_recent_events(
        tmp_path / "content", "test_tenant", today=datetime.date(2026, 10, 3), days=30
    )
    assert len(events) == 2

    clusters = detect_clusters(events)
    # clean_company ensures Acme Financial and Acme Financial Inc. cluster together
    assert "acme financial" in clusters
    cluster_item = clusters["acme financial"]
    assert cluster_item["cluster_size"] == 2
    assert cluster_item["cluster_event"].event_type == "cluster_expansion"
    # cluster event_date must be anchored to the latest event date
    assert cluster_item["cluster_event"].event_date == "2026-10-02"


def test_detect_clusters_deduplicates_identical_events():
    ev1 = BusinessEvent(
        company_name="Acme Financial",
        company_domain=None,
        event_type="hiring",
        event_date="2026-10-02",
        headline="Hiring AI Lead",
        snippet="Snippet 1",
        source_url="https://example.com/1",
        provider="ats_sweep",
    )
    # Identical entity, role, event_type, and event_date -> identical event_dedup_hash
    ev2 = BusinessEvent(
        company_name="Acme Financial Inc.",
        company_domain=None,
        event_type="hiring",
        event_date="2026-10-02",
        headline="Hiring AI Lead",
        snippet="Snippet 2",
        source_url="https://example.com/2",
        provider="ats_sweep",
    )
    clusters = detect_clusters([ev1, ev2])
    # Deduplication collapses to 1 distinct event, so no cluster expansion is triggered
    assert "acme financial" not in clusters
    assert len(clusters) == 0


def test_detect_clusters_multiple_roles_same_date():
    ev1 = BusinessEvent(
        company_name="Acme Financial",
        company_domain="acmefinancial.example.com",
        event_type="hiring",
        event_date="2026-10-04",
        headline="Staff AI Architect",
        snippet="Snippet 1",
        source_url="https://example.com/1",
        provider="ats_sweep",
    )
    ev2 = BusinessEvent(
        company_name="Acme Financial",
        company_domain="acmefinancial.example.com",
        event_type="hiring",
        event_date="2026-10-04",
        headline="VP of AI Engineering",
        snippet="Snippet 2",
        source_url="https://example.com/2",
        provider="ats_sweep",
    )
    clusters = detect_clusters([ev1, ev2])
    assert "acmefinancial.example.com" in clusters
    assert clusters["acmefinancial.example.com"]["cluster_size"] == 2


def test_render_hook_preview_carries_caveat():
    event = BusinessEvent.from_job_post(
        company_name="Acme Financial",
        company_domain="acme.example.com",
        job_title="Staff AI Architect",
        matched_keywords=["LangGraph"],
        event_date="2026-10-04",
        job_url="https://boards.greenhouse.io/acme",
        snippet="Experience with agent orchestration required.",
        frameworks=["LangGraph"],
        pain_cue="delegation security",
    )
    preview = render_hook_preview(event)
    assert "LangGraph" in preview
    assert "delegation security" in preview
    assert (
        "[NOTE: Hook preview only. Full 5-slot message (claims, proof points, CTA, word limits) "
        "is drafted downstream via draft-outreach during prospect Step 8 using active angles.toml]"
        in preview
    )


def test_execute_probe_headless_and_staging(tmp_path):
    hits = [
        {
            "title": f"Staff AI Engineer at Acme Financial {i} | Greenhouse",
            "url": f"https://boards.greenhouse.io/acme{i}/jobs/1",
            "snippet": "Experience with LangGraph and autonomous agents.",
        }
        for i in range(10)
    ]
    code, output = execute_probe(
        token="LangGraph",
        profile="test-tenant",
        hits=hits,
        limit=10,
        non_interactive=True,
        auto_yes=True,
        content_root=tmp_path / "content",
        profiles_root=tmp_path / "profiles",
        kind="jobs",
    )
    assert code == 0
    assert "Yield: 10/10" in output
    assert "PASS (>= 70%)" in output
    assert "Staged 'LangGraph'" in output
    assert "(jobs)" in output


def test_execute_sweep_writes_ledger_and_buffers(tmp_path):
    hits = [
        {
            "title": "Staff AI Engineer at Riverbend Corp | Greenhouse",
            "url": "https://boards.greenhouse.io/riverbend/jobs/1",
            "snippet": "Experience with LangGraph and autonomous agents.",
        },
        {
            "title": "AI Architect at Unknown Corp | Lever",
            "url": "https://jobs.lever.co/unknown/2",
            "snippet": "Experience with LangGraph and autonomous agents.",
        },
    ]
    # Setup known account
    (tmp_path / "content" / "test-tenant" / "accounts" / "riverbend-corp").mkdir(parents=True)

    code = execute_sweep(
        profile="test-tenant",
        hits=hits,
        content_root=tmp_path / "content",
        max_queries=10,
        max_events=50,
        kind="hiring",
    )
    assert code == 0
    today_str = datetime.date.today().strftime("%Y-%m")
    ledger = tmp_path / "content" / "test-tenant" / "signals" / f"events-{today_str}.jsonl"
    assert ledger.exists()
    lines = ledger.read_text().strip().split("\n")
    assert len(lines) == 1  # Riverbend Corp matched
    assert "Riverbend" in lines[0]

    unresolved = tmp_path / "content" / "test-tenant" / "signals" / "unresolved_events.jsonl"
    assert unresolved.exists()
    unresolved_lines = unresolved.read_text().strip().split("\n")
    assert len(unresolved_lines) == 1  # Unknown Corp buffered
    assert "Unknown" in unresolved_lines[0]


def test_execute_sweep_deduplicates_on_rerun(tmp_path):
    hits = [
        {
            "title": "Staff AI Engineer at Riverbend Corp | Greenhouse",
            "url": "https://boards.greenhouse.io/riverbend/jobs/1",
            "snippet": "Experience with LangGraph and autonomous agents.",
        }
    ]
    (tmp_path / "content" / "test-tenant" / "accounts" / "riverbend-corp").mkdir(parents=True)

    # First run
    execute_sweep(
        profile="test-tenant",
        hits=hits,
        content_root=tmp_path / "content",
        kind="hiring",
    )
    # Second run with exact same hit
    execute_sweep(
        profile="test-tenant",
        hits=hits,
        content_root=tmp_path / "content",
        kind="hiring",
    )
    today_str = datetime.date.today().strftime("%Y-%m")
    ledger = tmp_path / "content" / "test-tenant" / "signals" / f"events-{today_str}.jsonl"
    lines = ledger.read_text().strip().split("\n")
    assert len(lines) == 1  # Deduplicated on rerun


def test_stage_token_safely_accumulates_across_runs(tmp_path):
    c_root = tmp_path / "content"
    p_root = tmp_path / "profiles"

    # Run 1: stage LangGraph
    p1 = _stage_token_safely(c_root, p_root, "test-tenant", "LangGraph")
    assert p1.exists()

    # Run 2: stage CrewAI, ensuring LangGraph remains staged
    p2 = _stage_token_safely(c_root, p_root, "test-tenant", "CrewAI")
    assert p2.exists()
    content = p2.read_text(encoding="utf-8")
    assert '"LangGraph"' in content
    assert '"CrewAI"' in content


def test_cli_probe_help():
    res = subprocess.run(
        [sys.executable, "-m", "gtm_core.signal_events.cli", "probe", "--help"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "--kind" in res.stdout
    assert "--non-interactive" in res.stdout
    assert "--yes" in res.stdout
    assert "--hits-file" in res.stdout


def test_cli_sweep_help():
    res = subprocess.run(
        [sys.executable, "-m", "gtm_core.signal_events.cli", "sweep", "--help"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "--kind" in res.stdout
    assert "--max-queries" in res.stdout
    assert "--max-events" in res.stdout


def test_cli_probe_subprocess_invocation(tmp_path):
    env = os.environ.copy()
    env["GTM_CONTENT_ROOT"] = str(tmp_path / "content")
    hits = [
        {
            "title": f"Staff AI Engineer at Acme Financial {i} | Greenhouse",
            "url": f"https://jobs.example.com/acme{i}/jobs/1",
            "snippet": "Experience with LangGraph and autonomous agents.",
        }
        for i in range(10)
    ]
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtm_core.signal_events.cli",
            "probe",
            "--token",
            "LangGraph",
            "--profile",
            "test-tenant",
            "--yes",
            "--hits-file",
            "-",
        ],
        input=json.dumps(hits),
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0
    assert "Yield:" in res.stdout
    assert "Staged" in res.stdout


def test_cli_path_traversal_refused(tmp_path):
    from gtm_core.signal_events.cli import main

    # CLI path traversal refusal (exit code 2)
    code = main(["probe", "--token", "LangGraph", "--profile", "../outside"])
    assert code == 2

    # Direct function call raises ValueError
    with pytest.raises(ValueError, match="unsafe profile"):
        read_recent_events(tmp_path, "../outside")


def test_stage_token_safely_validation_and_ordering(tmp_path):
    c_root = tmp_path / "content"
    p_root = tmp_path / "profiles"

    # Malicious tokens rejected
    with pytest.raises(ValueError, match="Invalid token format"):
        _stage_token_safely(c_root, p_root, "test-tenant", 'bad\ntoken"')

    with pytest.raises(ValueError, match="Invalid token format"):
        _stage_token_safely(c_root, p_root, "test-tenant", "bad;injection")

    # Staging prepends token at index 0
    p1 = _stage_token_safely(c_root, p_root, "test-tenant", "LangGraph")
    assert p1.exists()
    p2 = _stage_token_safely(c_root, p_root, "test-tenant", "CrewAI")
    content = p2.read_text(encoding="utf-8")
    # CrewAI should appear before LangGraph
    assert content.index('"CrewAI"') < content.index('"LangGraph"')


def test_signal_kill_switches(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_SIGNAL_PROBES_ENABLED", "false")
    code_probe, out_probe = execute_probe(
        token="LangGraph",
        profile="test-tenant",
        hits=[],
        content_root=tmp_path / "content",
    )
    assert code_probe == 0
    assert "Disabled by GTM_SIGNAL_PROBES_ENABLED" in out_probe

    monkeypatch.delenv("GTM_SIGNAL_PROBES_ENABLED", raising=False)
    monkeypatch.setenv("GTM_SIGNAL_SWEEP_ENABLED", "false")
    code_sweep = execute_sweep(
        profile="test-tenant",
        hits=[],
        content_root=tmp_path / "content",
    )
    assert code_sweep == 0


def test_execute_sweep_logs_invalid_events(tmp_path):
    hits = [
        "not-a-dict-hit",
        {
            "title": "Staff AI Engineer at Expired Corp | Greenhouse",
            "url": "https://boards.greenhouse.io/expired/jobs/1",
            "snippet": "LangGraph role.",
            "http_status": 404,
        },
    ]
    code = execute_sweep(
        profile="test-tenant",
        hits=hits,
        content_root=tmp_path / "content",
    )
    assert code == 0
    invalid_file = tmp_path / "content" / "test-tenant" / "signals" / "invalid_events.jsonl"
    assert invalid_file.exists()
    lines = invalid_file.read_text().strip().split("\n")
    assert len(lines) == 2
    assert "malformed_hit" in lines[0]
    assert "expired_job_404" in lines[1]


def test_execute_probe_80_col_viewport_conformance(tmp_path):
    hits = [
        {
            "title": (
                "Principal Senior Staff Lead Architect of Autonomous Intelligent Systems "
                "at Very Long Name Enterprise Financial Holding Corp | Greenhouse"
            ),
            "url": "https://boards.greenhouse.io/verylong/jobs/101",
            "snippet": (
                "We need extensive experience in LangGraph, multi-agent orchestration, "
                "eval latency benchmarking, security controls, and high-concurrency systems."
            ),
        }
    ]
    code, output = execute_probe(
        token="LangGraph",
        profile="test-tenant",
        hits=hits,
        limit=10,
        content_root=tmp_path / "content",
    )
    assert code == 0
    table_lines = [
        line
        for line in output.splitlines()
        if line.startswith(("[PROBE]", "---", "Company")) or "Target Buyer" in line
    ]
    assert len(table_lines) >= 4
    for line in table_lines:
        assert len(line) <= 80, f"Table line exceeds 80 columns ({len(line)}): {line}"


def test_detect_clusters_hybrid_domain_and_non_domain():
    # ev1 has no domain, ev2 has domain
    ev1 = BusinessEvent(
        company_name="Acme Financial",
        company_domain=None,
        event_type="hiring",
        event_date="2026-10-02",
        headline="Staff AI Lead",
        snippet="Snippet 1",
        source_url="https://example.com/1",
        provider="ats_sweep",
    )
    ev2 = BusinessEvent(
        company_name="Acme Financial, Inc.",
        company_domain="acmefinancial.example.com",
        event_type="hiring",
        event_date="2026-10-04",
        headline="AI Research Scientist",
        snippet="Snippet 2",
        source_url="https://example.com/2",
        provider="ats_sweep",
    )
    clusters = detect_clusters([ev1, ev2])
    # Both keys exist for lookup
    assert "acmefinancial.example.com" in clusters
    assert "acme financial" in clusters
    cluster_item = clusters["acmefinancial.example.com"]
    assert cluster_item["cluster_size"] == 2
    # Resolved domain propagated to cluster event
    assert cluster_item["cluster_event"].company_domain == "acmefinancial.example.com"


def test_execute_probe_theirstack_header_and_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")

    fake_data = {
        "data": [
            {
                "job_title": "AI Architect at Acme Robotics | Greenhouse",
                "snippet": "LangGraph systems.",
                "url": "https://example.com/jobs/1",
                "company_domain": "example.com",
                "posted_at": "2026-10-04T12:00:00Z",
            }
        ]
    }
    from tests.unit.signal_events.test_providers import _FakeOpener

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *args: _FakeOpener(json.dumps(fake_data).encode("utf-8")),
    )

    code, out = execute_probe(
        token="LangGraph",
        profile="test-tenant",
        limit=5,
        content_root=tmp_path / "content",
    )
    assert code == 0
    assert "Provider: TheirStack Accelerator" in out

    # Now test fallback in probe (e.g. 429)
    err_429 = urllib.error.HTTPError(
        "https://api.theirstack.com/v1/jobs/search", 429, "Too Many Requests", {}, None
    )
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *args: _FakeOpener(err_429),
    )
    code_fb, out_fb = execute_probe(
        token="LangGraph",
        profile="test-tenant",
        hits=[
            {
                "title": "AI Architect at Acme Robotics | Greenhouse",
                "snippet": "LangGraph systems.",
                "url": "https://example.com/jobs/fallback",
            }
        ],
        limit=5,
        content_root=tmp_path / "content",
    )
    assert code_fb == 0
    assert "Fallback: HTTP 429 (Rate Limit)" in out_fb


def test_execute_sweep_short_circuits_on_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    from tests.unit.signal_events.test_providers import _FakeOpener

    call_count = 0

    class _CountingOpener(_FakeOpener):
        def open(self, request, timeout=None):
            nonlocal call_count
            call_count += 1
            raise urllib.error.HTTPError(
                "https://api.theirstack.com/v1/jobs/search", 429, "Too Many Requests", {}, None
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: _CountingOpener(b""))

    content_dir = tmp_path / "content"
    profiles_dir = tmp_path / "profiles"
    prof = "test-tenant"
    (profiles_dir / prof / "knowledge").mkdir(parents=True)
    sweep_toml = 'ai_vocabulary = ["T1", "T2", "T3", "T4", "T5"]\n'
    (profiles_dir / prof / "knowledge" / "web-sweep.toml").write_text(sweep_toml)

    execute_sweep(
        profile=prof,
        max_queries=5,
        max_events=10,
        content_root=content_dir,
        profiles_root=profiles_dir,
    )
    # Loop must short-circuit after the first 429 rather than querying 5 times
    assert call_count == 1


def test_execute_sweep_uses_zero_cost_ats_sweep_when_hits_provided(tmp_path, monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    from tests.unit.signal_events.test_providers import _FakeOpener

    fake_opener = _FakeOpener(b"{}")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    content_dir = tmp_path / "content"
    profiles_dir = tmp_path / "profiles"
    prof = "test-tenant"
    (profiles_dir / prof / "knowledge").mkdir(parents=True)
    sweep_toml = 'ai_vocabulary = ["LangGraph"]\n'
    (profiles_dir / prof / "knowledge" / "web-sweep.toml").write_text(sweep_toml)

    hits = [
        {
            "title": "AI Architect at Acme Robotics | Greenhouse",
            "snippet": "LangGraph systems.",
            "url": "https://example.com/jobs/1",
        }
    ]

    execute_sweep(
        profile=prof,
        max_queries=1,
        max_events=5,
        hits=hits,
        content_root=content_dir,
        profiles_root=profiles_dir,
    )
    # ZeroCostATSSweep must process hits without making any HTTP calls to TheirStack API
    assert len(fake_opener.requests) == 0

"""Unit tests for gtm_core.web_sweep (6-source signal hunt queries & normalization)."""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core.web_sweep import (
    generate_queries,
    is_valid_source_url,
    main,
    normalize_sweep,
)


def test_generate_queries_produces_6_sources():
    queries = generate_queries("Acme Robotics, Inc.", segment="enterprise")
    assert len(queries) == 6
    types = [q["type"] for q in queries]
    assert types == ["newsroom", "hiring", "eng", "regulatory", "funding", "incident"]
    # Verify clean_company removed legal suffix in the query
    assert '"Acme Robotics"' in queries[0]["query"]


def test_is_valid_source_url_validation():
    assert is_valid_source_url("https://news.example/2026/09/01/acme-launch")
    assert is_valid_source_url("https://blog.acmerobotics.example/mcp-support")

    # Insecure HTTP rejected
    assert not is_valid_source_url("http://news.example/2026/09/01/acme-launch")

    # Search engines rejected
    assert not is_valid_source_url("https://www.google.com/search?q=acme+robotics")
    assert not is_valid_source_url("https://bing.com/search?q=acme")
    assert not is_valid_source_url("https://duckduckgo.com/?q=acme")
    assert not is_valid_source_url("")


def test_normalize_sweep_empty_hits_yields_reangle():
    res = normalize_sweep("Beta Corp", raw_hits=[], segment="startup")
    assert res["verdict"] == "re-angle"
    assert res["verdict_reason"] == "no dated public why-now found this pass"
    assert res["why_now"] == ""
    assert res["signal_source_url"] == ""


def test_normalize_sweep_stale_hits_yields_reangle():
    stale_hits = [
        {
            "type": "newsroom",
            "date": "2025-01-01",
            "url": "https://news.example/acme",
            "snippet": "Acme launches agent platform",
        }
    ]
    # Enterprise max age is 90 days. Against reference date 2026-09-20, 2025-01-01 is stale.
    res = normalize_sweep(
        "Beta Corp", raw_hits=stale_hits, segment="enterprise", as_of="2026-09-20"
    )
    assert res["verdict"] == "re-angle"
    assert res["why_now"] == ""


def test_normalize_sweep_startup_funding_allows_18_months():
    hits = [
        {
            "type": "funding",
            "date": "2025-08-01",  # ~13 months old from 2026-09-20 (< 18 months)
            "url": "https://news.example/funding",
            "snippet": "Beta raises Series A for autonomous agent platform",
            "strength": "H",
        }
    ]
    res = normalize_sweep("Beta Corp", raw_hits=hits, segment="startup", as_of="2026-09-20")
    assert res["verdict"] == ""
    assert res["category_relation"] == ""
    assert res["why_now"] != ""
    assert res["signal_observed"] == "2025-08-01"
    assert res["signal_source_url"] == "https://news.example/funding"
    assert "[funding | 2025-08-01 | https://news.example/funding | H]" in res["fire_tag"]


def test_normalize_sweep_picks_highest_strength_signal():
    hits = [
        {
            "type": "hiring",
            "date": "2026-09-10",
            "url": "https://linkedin.com/jobs/view/123",
            "snippet": "Hiring ML Platform Engineer",
            "strength": "M",
        },
        {
            "type": "eng",
            "date": "2026-09-08",
            "url": "https://github.com/beta/agent-mcp",
            "snippet": "Open-sourced production MCP gateway for enterprise tools",
            "strength": "H",
        },
    ]
    res = normalize_sweep("Beta Corp", raw_hits=hits, segment="startup", as_of="2026-09-20")
    assert res["verdict"] == ""
    assert res["category_relation"] == ""
    # Strength H wins over M even if M is two days newer
    assert res["signal_source_url"] == "https://github.com/beta/agent-mcp"
    assert "H" in res["fire_tag"]


def test_normalize_sweep_rejects_unusable_signal():
    hits = [
        {
            "type": "funding",
            "date": "2026-09-01",
            "url": "https://news.example/funding",
            "snippet": "no confirmed funding or hiring signal found",
            "strength": "H",
        }
    ]
    res = normalize_sweep("Acme Corp", raw_hits=hits, segment="startup", as_of="2026-09-20")
    assert res["why_now"] == ""
    assert res["verdict"] == "re-angle"
    assert "rejected by signal_clause" in res["verdict_reason"]


def test_cli_queries_and_normalize(tmp_path: Path):
    # Test queries CLI
    q_file = tmp_path / "queries.json"
    code_q = main(["queries", "--company", "Acme Labs", "--out", str(q_file)])
    assert code_q == 0
    queries = json.loads(q_file.read_text(encoding="utf-8"))
    assert len(queries) == 6

    # Test normalize CLI
    hits_file = tmp_path / "hits.json"
    norm_file = tmp_path / "normalized.json"
    hits = [
        {
            "type": "newsroom",
            "date": "2026-09-15",
            "url": "https://acmelabs.example/announcement",
            "evidence": "Acme Labs announces multi-agent governance system",
            "strength": "H",
        }
    ]
    hits_file.write_text(json.dumps(hits), encoding="utf-8")

    code_n = main(
        [
            "normalize",
            "--company",
            "Acme Labs",
            "--hits",
            str(hits_file),
            "--as-of",
            "2026-09-20",
            "--out",
            str(norm_file),
        ]
    )
    assert code_n == 0
    res = json.loads(norm_file.read_text(encoding="utf-8"))
    assert res["verdict"] == ""
    assert res["why_now"] != ""
    assert res["signal_source_url"] == "https://acmelabs.example/announcement"

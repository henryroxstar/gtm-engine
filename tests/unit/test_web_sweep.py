"""Unit tests for gtm_core.web_sweep (6-source signal hunt queries & normalization)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def mock_get_ai_vocab_regex(monkeypatch):
    import re

    from gtm_core import web_sweep_hits

    regex = re.compile(
        r"\bai\b|\bllm\b|\bmcp\b|\bagentic\b|\bcopilot\b|"
        r"\bmachine learning\b|\bml platforms?\b|"
        r"\bagent platforms?\b|\bagent marketplaces?\b|\bautonomous agents?\b|"
        r"\bvirtual agents?\b|\bdigital agents?\b|\bai[-  ]powered\b|"
        r"\bthird-party agents?\b|\bcross-org agents?\b|\bagent authentication\b|"
        r"\bagent delegation\b|\bagent identity\b|\bx402\b|\bap2\b|\ba2a\b|"
        r"\bpride framework\b|\bai impact\b|\bagentic finance\b|\bagent breakouts?\b|"
        r"\bunattended agents?\b|\bconfused deputy\b|\bshadow ai\b",
        re.IGNORECASE,
    )
    monkeypatch.setattr(web_sweep_hits, "get_ai_vocab_regex", lambda profile: regex)


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


def test_generate_queries_neutral_defaults_with_no_profile():
    """PSK-019: with no --profile, the vocabulary is the neutral, tenant-agnostic default —
    never the vocabulary of any one tenant."""
    queries = generate_queries("Acme Robotics")
    by_type = {q["type"]: q["query"] for q in queries}
    assert (
        by_type["newsroom"] == '"Acme Robotics" (launch OR announces OR partnership OR expansion)'
    )
    assert (
        by_type["hiring"]
        == '"Acme Robotics" ("head of" OR director OR "vice president") site:linkedin.com/jobs'
    )
    assert by_type["funding"] == '"Acme Robotics" (raised OR "Series")'


def test_generate_queries_domain_reuses_resolved_eng_group():
    """PSK-019: the --domain special case must reuse the resolved eng group, never a
    hardcoded one."""
    queries = generate_queries("Acme Robotics", domain="acme.example")
    eng_q = next(q for q in queries if q["type"] == "eng")
    assert eng_q["query"] == (
        '"Acme Robotics" OR site:acme.example ("engineering blog" OR github OR "open source")'
    )


def test_generate_queries_loads_profile_vocabulary_and_reproduces_the_legacy_six(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PSK-019: a profile's web-sweep.toml overrides the neutral vocabulary. Proven here with
    the SAME (generic, non-identifying search-term) vocabulary that used to be hardcoded, under
    a throwaway profile name — the six resulting query strings are the golden legacy output."""
    profiles_root = tmp_path / "profiles"
    knowledge_dir = profiles_root / "sample-tenant" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "web-sweep.toml").write_text(
        """
[queries]
newsroom = '(agentic OR "AI governance" OR "agent identity")'
hiring = '("AI platform" OR agent OR "ML platform")'
hiring_site = "linkedin.com/jobs"
eng = '(MCP OR A2A OR "engineering blog" OR github)'
regulatory = '(regulator OR compliance OR standards OR MAS OR IMDA OR W3C OR DIF OR "earnings call")'
funding = '(raised OR "Series")'
incident = '(breach OR audit OR "EU AI Act" OR incident OR stall)'
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles_root))

    queries = generate_queries("Northwind Robotics", profile="sample-tenant")

    golden = [
        '"Northwind Robotics" (agentic OR "AI governance" OR "agent identity")',
        '"Northwind Robotics" ("AI platform" OR agent OR "ML platform") site:linkedin.com/jobs',
        '"Northwind Robotics" (MCP OR A2A OR "engineering blog" OR github)',
        '"Northwind Robotics" (regulator OR compliance OR standards OR MAS OR IMDA OR W3C OR'
        ' DIF OR "earnings call")',
        '"Northwind Robotics" (raised OR "Series")',
        '"Northwind Robotics" (breach OR audit OR "EU AI Act" OR incident OR stall)',
    ]
    assert [q["query"] for q in queries] == golden


def test_generate_queries_missing_profile_file_falls_back_with_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    queries = generate_queries("Acme Robotics", profile="ghost-tenant")
    captured = capsys.readouterr()
    assert "no web-sweep.toml" in captured.err
    assert (
        queries[0]["query"] == '"Acme Robotics" (launch OR announces OR partnership OR expansion)'
    )


def test_generate_queries_rejects_a_toml_value_with_braces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_root = tmp_path / "profiles"
    knowledge_dir = profiles_root / "bad-tenant" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "web-sweep.toml").write_text(
        '[queries]\nnewsroom = "(launch OR {company})"\n', encoding="utf-8"
    )
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles_root))
    with pytest.raises(ValueError):
        generate_queries("Acme Robotics", profile="bad-tenant")


def test_generate_queries_product_level_file_overrides_profile_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PSK-019: vocabulary resolution is product-first, profile-fallback, via
    gtm_core.paths.resolve_knowledge_file — never a hand-built profiles/... path."""
    profiles_root = tmp_path / "profiles"
    profile_knowledge = profiles_root / "multi-tenant" / "knowledge"
    profile_knowledge.mkdir(parents=True)
    (profile_knowledge / "web-sweep.toml").write_text(
        '[queries]\nnewsroom = "(profile-level-term)"\n', encoding="utf-8"
    )
    product_knowledge = profiles_root / "multi-tenant" / "products" / "widgets"
    product_knowledge.mkdir(parents=True)
    (product_knowledge / "web-sweep.toml").write_text(
        '[queries]\nnewsroom = "(product-level-term)"\n', encoding="utf-8"
    )
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles_root))

    with_product = generate_queries("Acme Robotics", profile="multi-tenant", product="widgets")
    without_product = generate_queries("Acme Robotics", profile="multi-tenant")

    assert '"Acme Robotics" (product-level-term)' == next(
        q["query"] for q in with_product if q["type"] == "newsroom"
    )
    assert '"Acme Robotics" (profile-level-term)' == next(
        q["query"] for q in without_product if q["type"] == "newsroom"
    )


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


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://news.example/2026/09/01/launch", True),
        ("https://blog.acmerobotics.example/mcp-support", True),
        ("http://news.example/insecure", False),  # not https
        ("https://www.google.com/search?q=acme", False),
        ("https://google.dev/search?q=acme", False),  # a TLD other than com, same "google" label
        ("https://bing.com/search?q=acme", False),
        ("https://duckduckgo.com/?q=acme", False),
        ("https://search.yahoo.com/search?p=acme", False),
        ("https://www.baidu.com/s?wd=acme", False),
        ("https://yandex.com/search/?text=acme", False),
        ("https://cloud.google.com/blog/announcing-agents", True),  # subdomain, not a search page
        ("https://google.dev/docs/acme", True),  # a TLD other than com, but not a search page
        ("https://alice@northwind.example/news/a", True),  # userinfo stripped, ordinary host
        ("https://www.google.com:443/search?q=x", False),  # port stripped, still a search host
        ("https://news", False),  # no dot in hostname
        ("https://localhost/path", False),
        ("https://192.0.2.10/path", False),  # IPv4 literal (RFC 5737 TEST-NET-1)
        ("https://[2001:db8::1]/path", False),  # IPv6 literal
        ("https://[::1", False),  # malformed authority — must not crash
        ("", False),
        (None, False),
    ],
)
def test_is_valid_source_url_table(url: str | None, expected: bool) -> None:
    assert is_valid_source_url(url) is expected  # type: ignore[arg-type]


def test_normalize_sweep_empty_hits_yields_reangle():
    res = normalize_sweep("Beta Corp", raw_hits=[], segment="startup")
    assert res["verdict"] == "re-angle"
    assert res["verdict_reason"] == "no dated public why-now found this pass"
    assert res["why_now"] == ""
    assert res["signal_source_url"] == ""
    assert res["rejected"] == []
    assert res["context_hits"] == []


def test_normalize_sweep_stale_hits_yields_reangle():
    stale_hits = [
        {
            "type": "newsroom",
            "date": "2025-01-01",
            "url": "https://news.example/acme",
            "snippet": "Acme launches its agent platform.",
        }
    ]
    # Enterprise max age is 90 days. Against reference date 2026-09-20, 2025-01-01 is stale.
    res = normalize_sweep("Acme", raw_hits=stale_hits, segment="enterprise", as_of="2026-09-20")
    assert res["verdict"] == "re-angle"
    assert res["why_now"] == ""
    assert res["rejected"] == [{"index": 0, "reason": "stale"}]


def test_normalize_sweep_startup_funding_over_210_days_is_context_only():
    """PSK-021: a startup funding hit within the legacy 18-month window (<=540 days) but past
    the load gate's 210-day ceiling must never become the selected why-now signal — it is
    demoted to `context_hits` and the sweep re-angles."""
    hits = [
        {
            "type": "funding",
            "date": "2025-08-01",  # ~415 days before 2026-09-20: legacy-fresh, general-stale
            "url": "https://news.example/funding",
            "snippet": "Beta Corp raises Series A for its autonomous agent platform.",
            "strength": "H",
        }
    ]
    res = normalize_sweep("Beta Corp", raw_hits=hits, segment="startup", as_of="2026-09-20")
    assert res["verdict"] == "re-angle"
    assert res["why_now"] == ""
    assert res["hits"] == []
    assert res["rejected"] == [{"index": 0, "reason": "stale"}]
    assert len(res["context_hits"]) == 1
    assert res["context_hits"][0]["date"] == "2025-08-01"
    assert res["context_hits"][0]["type"] == "funding"


def test_enterprise_hit_aged_120_days_is_rejected():
    hit = {
        "type": "newsroom",
        "date": "2026-05-24",  # 120 days before 2026-09-21
        "url": "https://news.example/acme",
        "snippet": "Acme Corp opened its agent platform to enterprise customers.",
    }
    res = normalize_sweep("Acme Corp", raw_hits=[hit], segment="enterprise", as_of="2026-09-21")
    assert res["verdict"] == "re-angle"
    assert res["hits"] == []
    assert res["context_hits"] == []
    assert res["rejected"] == [{"index": 0, "reason": "stale"}]


def test_same_hit_aged_120_days_is_accepted_for_startup():
    hit = {
        "type": "newsroom",
        "date": "2026-05-24",
        "url": "https://news.example/acme",
        "snippet": "Acme Corp opened its agent platform to enterprise customers.",
    }
    res = normalize_sweep("Acme Corp", raw_hits=[hit], segment="startup", as_of="2026-09-21")
    assert res["verdict"] == ""
    assert len(res["hits"]) == 1
    assert res["rejected"] == []


def test_400_day_funding_hit_lands_in_context_hits_and_verdict_is_reangle():
    hit = {
        "type": "funding",
        "date": "2025-08-17",  # 400 days before 2026-09-21
        "url": "https://news.example/funding",
        "snippet": "Beta Corp raised a growth round for its agent platform.",
        "strength": "H",
    }
    res = normalize_sweep("Beta Corp", raw_hits=[hit], segment="startup", as_of="2026-09-21")
    assert res["verdict"] == "re-angle"
    assert res["hits"] == []
    assert len(res["context_hits"]) == 1
    assert res["context_hits"][0]["date"] == "2025-08-17"
    assert res["rejected"] == [{"index": 0, "reason": "stale"}]


def test_normalize_sweep_rejects_an_unknown_segment():
    with pytest.raises(ValueError):
        normalize_sweep("Acme Corp", raw_hits=[], segment="mid-market")


def test_normalize_sweep_rejects_an_unparseable_as_of():
    with pytest.raises(ValueError):
        normalize_sweep("Acme Corp", raw_hits=[], segment="startup", as_of="not-a-date")


def test_normalize_sweep_picks_highest_strength_signal():
    hits = [
        {
            "type": "hiring",
            "date": "2026-09-10",
            "url": "https://linkedin.com/jobs/view/123",
            "snippet": "Beta Corp is hiring an ML Platform Engineer.",
            "strength": "M",
        },
        {
            "type": "eng",
            "date": "2026-09-08",
            "url": "https://github.com/beta/agent-mcp",
            "snippet": "Beta Corp open-sourced a production MCP gateway for enterprise tools.",
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
            "snippet": "Acme Corp research found no confirmed funding or hiring signal.",
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


def test_cli_normalize_all_mis_keyed_hits_exits_2_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PSK-018: every hit rejected for a shape reason must exit loudly, never write a file
    that looks like a genuine empty-handed research pass."""
    hits_file = tmp_path / "hits.json"
    norm_file = tmp_path / "normalized.json"
    hits_file.write_text(
        json.dumps([{"link": "https://northwind.example/a", "published_date": "2026-09-10"}]),
        encoding="utf-8",
    )

    code = main(
        [
            "normalize",
            "--company",
            "Northwind Robotics",
            "--hits",
            str(hits_file),
            "--as-of",
            "2026-09-20",
            "--out",
            str(norm_file),
        ]
    )
    assert code == 2
    assert not norm_file.exists()
    assert capsys.readouterr().err.strip()


def test_cli_normalize_non_dict_element_does_not_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hits_file = tmp_path / "hits.json"
    hits_file.write_text(json.dumps(["just a string"]), encoding="utf-8")

    code = main(
        [
            "normalize",
            "--company",
            "Northwind Robotics",
            "--hits",
            str(hits_file),
            "--as-of",
            "2026-09-20",
        ]
    )
    assert code == 2
    assert capsys.readouterr().err.strip()


def test_cli_normalize_unparseable_as_of_is_refused(tmp_path: Path) -> None:
    hits_file = tmp_path / "hits.json"
    hits_file.write_text(json.dumps([]), encoding="utf-8")

    code = main(
        [
            "normalize",
            "--company",
            "Northwind Robotics",
            "--hits",
            str(hits_file),
            "--as-of",
            "not-a-date",
        ]
    )
    assert code == 2


def test_cli_rejects_an_unknown_segment_choice(tmp_path: Path) -> None:
    hits_file = tmp_path / "hits.json"
    hits_file.write_text(json.dumps([]), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "normalize",
                "--company",
                "Northwind Robotics",
                "--hits",
                str(hits_file),
                "--segment",
                "mid-market",
            ]
        )
    assert exc_info.value.code == 2


def test_cli_normalize_bytes_that_are_not_text_are_one_clear_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review L2 (2026-09-21): a hits file that is not UTF-8 ended the step in a traceback."""
    hits_file = tmp_path / "hits.json"
    hits_file.write_bytes(b'[{"title": "Northwind Robotics \xff\xfe"}]')
    out_file = tmp_path / "sweep.json"
    argv = ["normalize", "--company", "Northwind Robotics", "--hits", str(hits_file)]
    assert main([*argv, "--as-of", "2026-09-20", "--out", str(out_file)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and not out_file.exists()
    assert captured.err.startswith("Error: ") and "UTF-8" in captured.err
    assert len(captured.err.strip().splitlines()) == 1


# --- W3a / R3.1, R3.2, R3.4 tests -------------------------------------------


def test_r3_1_valid_segments_equal_role_vocabulary_segments():
    """R3.1: the sweep's valid segments are derived from role_vocabulary segments."""
    from gtm_core.role_vocabulary import DEFAULT_SEGMENTS
    from gtm_core.web_sweep import _VALID_SEGMENTS, VALID_SEGMENTS

    assert VALID_SEGMENTS == DEFAULT_SEGMENTS
    assert _VALID_SEGMENTS == DEFAULT_SEGMENTS
    assert "builder" in VALID_SEGMENTS


def test_r3_1_builder_segment_accepted_and_unknown_refused(tmp_path: Path):
    """R3.1: --segment builder is accepted; an unknown segment is refused."""
    hits = [
        {
            "type": "newsroom",
            "date": "2026-09-15",
            "url": "https://builder.example/launch",
            "evidence": "Builder Co released an agentic developer SDK for building agents.",
            "strength": "H",
        }
    ]
    res = normalize_sweep("Builder Co", raw_hits=hits, segment="builder", as_of="2026-09-20")
    assert res["verdict"] == ""
    assert res["why_now"] != ""

    with pytest.raises(ValueError):
        normalize_sweep("Builder Co", raw_hits=hits, segment="unknown-seg", as_of="2026-09-20")

    hits_file = tmp_path / "hits_builder.json"
    hits_file.write_text(json.dumps(hits), encoding="utf-8")
    code = main(
        [
            "normalize",
            "--company",
            "Builder Co",
            "--hits",
            str(hits_file),
            "--segment",
            "builder",
            "--as-of",
            "2026-09-20",
        ]
    )
    assert code == 0

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "normalize",
                "--company",
                "Builder Co",
                "--hits",
                str(hits_file),
                "--segment",
                "unknown-seg",
                "--as-of",
                "2026-09-20",
            ]
        )
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("virtual agent", "ai"),
        ("digital agents", "ai"),
        ("AI-powered", "ai"),
        ("AI powered", "ai"),
        ("AI\u00a0powered", "ai"),
        ("appointed 40 new insurance agents", "human"),
        ("third-party agents", "ai"),
        ("cross-org agents", "ai"),
        ("agent authentication", "ai"),
        ("x402 protocol", "ai"),
        ("AI IMPACT framework", "ai"),
        ("agentic finance", "ai"),
    ],
)
def test_r3_2_ai_vocabulary(text: str, expected: str):
    """R3.2: _AI_VOCAB_RE gains virtual agents?, digital agents?, ai[- \u00a0]powered."""
    from gtm_core.web_sweep import _determine_agent_kind

    assert _determine_agent_kind(text) == expected


@pytest.mark.parametrize(
    ("evidence", "expected_rule"),
    [
        ("machine learning & artificial intelligence (intent score 81)", "intent label"),
        (
            "No dated funding round or launch event confirmed in research, Summitline still appears bootstrapped",
            "no signal",
        ),
        (
            "AI-powered clinical Care Pathways coordinating agents across hospital networks in real time. *(Dated launch/funding signal to confirm at outreach.)*",
            "unverified",
        ),
        ("short", "too short"),
        (
            "oversubscribed customer-led round (2025-11-05, Riverbend FCU / Members First / "
            "Summitline / Halyard Ventures) earmarked for full-borrower-journey automation + scaling Lila",
            "too long",
        ),
        ("Launch of the thing — with detail (2026-01-01)", "banned em-dash"),
        ("Acme launched its agent platform (unclosed bracket", "unbalanced parentheses"),
        ("12345 67890 12345 67890", "no letters"),
        ("Acme launched its agent platform:", "incomplete sentence"),
    ],
)
def test_r3_4_normalize_rejection_reason_names_actual_rule(evidence: str, expected_rule: str):
    """R3.4: The normalize rejection reason states the actual rejecting rule for each rule in the corpus, not 'e.g. contains digits'."""
    hits = [
        {
            "type": "newsroom",
            "date": "2026-09-15",
            "url": "https://news.example/acme",
            "subject": "Acme",
            "evidence": evidence,
            "strength": "H",
        }
    ]
    res = normalize_sweep("Acme", raw_hits=hits, segment="startup", as_of="2026-09-20")
    assert res["why_now"] == ""
    assert res["verdict"] == "re-angle"
    assert "e.g. contains digits" not in res["verdict_reason"]
    assert res["verdict_reason"] == f"evidence rejected by signal_clause ({expected_rule})"

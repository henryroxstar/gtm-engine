import datetime
import json
import urllib.error
import urllib.request

import pytest

from gtm_core.signal_events.contracts import BusinessEvent
from gtm_core.signal_events.providers import (
    EgressRefused,
    PreLLMRegexFilter,
    TheirStackAccelerator,
    ZeroCostATSSweep,
    ZeroCostNewswireSweep,
    _NoRedirect,
    build_ats_query,
    check_url_head_status,
    extract_company_from_title,
    get_provider,
    parse_ats_serp_item,
)


def test_pre_llm_regex_filter():
    filter_fn = PreLLMRegexFilter()
    assert (
        filter_fn.is_agency("We are a premier staffing agency hiring on behalf of our client.")
        is True
    )
    assert filter_fn.is_agency("Apex Talent Solutions is placing an AI Architect.") is True
    assert (
        filter_fn.is_agency(
            "Acme Corp is hiring a Staff AI Architect to build internal agent pipelines."
        )
        is False
    )

    assert filter_fn.is_agency(None) is False


def test_extract_company_from_title():
    assert (
        extract_company_from_title("Staff AI Engineer at Acme Financial | Greenhouse")
        == "Acme Financial"
    )
    assert (
        extract_company_from_title("Senior Platform Engineer at Global Health - Lever")
        == "Global Health"
    )
    assert extract_company_from_title("AI Researcher at Stealth Startup") is None
    # Word boundary checks: "Threat" and "Chat" must not match inside the role title
    assert (
        extract_company_from_title("Threat Researcher at Acme Financial | Greenhouse")
        == "Acme Financial"
    )
    assert (
        extract_company_from_title("Chat Specialist at Acme Financial | Lever") == "Acme Financial"
    )
    # Hyphenated company name must be preserved
    assert extract_company_from_title("AI Engineer at Apex-Tech | Greenhouse") == "Apex-Tech"
    assert (
        extract_company_from_title("Senior Platform Engineer at Apex-Tech - Lever") == "Apex-Tech"
    )
    # Em-dash separator support
    assert extract_company_from_title("Lead Engineer at Acme Financial — Ashby") == "Acme Financial"
    # Legal suffix normalization via clean_company
    assert (
        extract_company_from_title("Staff AI Engineer at Acme Financial, Inc. | Greenhouse")
        == "Acme Financial"
    )
    # Defensive None/empty checks
    assert extract_company_from_title(None) is None
    assert extract_company_from_title("") is None


def test_check_url_head_status_non_blocking():
    assert check_url_head_status(404) == ("drop", "expired_job_404")
    assert check_url_head_status(410) == ("drop", "expired_job_410")
    assert check_url_head_status(403) == ("keep", "unverified_bot_guard")
    assert check_url_head_status(200) == ("keep", "verified_active")


def test_build_ats_query():
    q = build_ats_query("LangGraph")
    assert "site:boards.greenhouse.io" in q
    assert "site:jobs.lever.co" in q
    assert "site:jobs.ashbyhq.com" in q
    assert '"LangGraph"' in q


def test_parse_ats_serp_item_valid():
    raw_hit = {
        "title": "Staff AI Architect at Acme Financial | Greenhouse",
        "url": "https://example.com/acme/jobs/101",
        "snippet": (
            "We are seeking a Staff AI Architect to scale our LangGraph agent workflows "
            "and solve tool-calling delegation security."
        ),
    }
    event = parse_ats_serp_item(raw_hit, matched_token="LangGraph", event_date="2026-10-04")
    assert event is not None
    assert isinstance(event, BusinessEvent)
    assert event.company_name == "Acme Financial"
    assert "LangGraph" in event.meta["frameworks"]
    assert "tool-calling" in event.meta["pain_cue"] or "delegation" in event.meta["pain_cue"]

    # Framework signal present in job title
    hit_title_fw = {
        "title": "LangGraph Architect at Acme Financial | Greenhouse",
        "url": "https://example.com/acme/jobs/102",
        "snippet": "Building production agent workflows.",
    }
    event_fw = parse_ats_serp_item(hit_title_fw, matched_token="CrewAI", event_date="2026-10-04")
    assert event_fw is not None
    assert "LangGraph" in event_fw.meta["frameworks"]


def test_parse_ats_serp_item_none_fields():
    hit = {"title": "AI Role at Acme Financial", "snippet": None, "url": None}
    event = parse_ats_serp_item(hit, matched_token="LangGraph", event_date="2026-10-04")
    assert event is not None
    assert event.company_name == "Acme Financial"
    assert event.snippet == ""
    assert event.source_url == ""


def test_parse_ats_serp_item_drops_agency():
    raw_hit = {
        "title": "Staff AI Engineer at Apex Talent Solutions",
        "url": "https://example.com/apex/jobs/102",
        "snippet": "Our client is an enterprise bank hiring through our staffing agency.",
    }
    assert parse_ats_serp_item(raw_hit, matched_token="LangGraph", event_date="2026-10-04") is None


def test_fallback_dispatcher(monkeypatch):
    monkeypatch.delenv("THEIRSTACK_API_KEY", raising=False)
    hiring_provider = get_provider("hiring")
    assert isinstance(hiring_provider, ZeroCostATSSweep)

    ts = TheirStackAccelerator()
    assert ts.is_available() is False
    with pytest.raises(RuntimeError, match="without THEIRSTACK_API_KEY"):
        ts.fetch_events("LangGraph")

    monkeypatch.setenv("VIBE_PROSPECTING_CONNECTED", "false")
    events_provider = get_provider("events")
    assert isinstance(events_provider, ZeroCostNewswireSweep)


def test_parse_ats_serp_item_http_status_drop():
    hit_expired = {
        "title": "Staff AI Engineer at Acme Financial | Greenhouse",
        "url": "https://example.com/jobs/1",
        "snippet": "Experience with LangGraph.",
        "http_status": 404,
    }
    assert parse_ats_serp_item(hit_expired, matched_token="LangGraph") is None

    hit_active = {
        "title": "Staff AI Engineer at Acme Financial | Greenhouse",
        "url": "https://example.com/jobs/2",
        "snippet": "Experience with LangGraph.",
        "http_status": 200,
    }
    event = parse_ats_serp_item(hit_active, matched_token="LangGraph")
    assert event is not None
    assert event.meta["url_status"] == "verified_active"


def test_framework_and_pain_word_boundaries():
    hit = {
        "title": "Data Architect at Acme Financial | Greenhouse",
        "url": "https://example.com/jobs/3",
        "snippet": "We handle large-scale data retrievals and bedrock foundation architectures.",
    }
    event = parse_ats_serp_item(hit, matched_token="LangGraph")
    assert event is not None
    # "retrievals" should NOT trigger pain_cue="evals"
    assert event.meta["pain_cue"] == ""
    # "bedrock" is matched as the standalone word
    assert "Bedrock" in event.meta["frameworks"]


def test_extract_company_from_title_greedy_at():
    # Ensures 'at Scale' is not captured as the company name
    title = "Platform Engineer at Scale at Acme Financial | Greenhouse"
    assert extract_company_from_title(title) == "Acme Financial"


def test_parse_ats_serp_item_freshness_cutoff():
    import datetime

    today = datetime.date.today()
    stale_date = (today - datetime.timedelta(days=46)).isoformat()
    fresh_date = (today - datetime.timedelta(days=30)).isoformat()

    hit_stale = {
        "title": "AI Engineer at Acme Financial | Greenhouse",
        "url": "https://example.com/jobs/stale",
        "snippet": "LangGraph developer needed.",
    }
    # Stale hit should be dropped (> 45 days)
    assert parse_ats_serp_item(hit_stale, matched_token="LangGraph", event_date=stale_date) is None

    # Fresh hit should be parsed (<= 45 days)
    event = parse_ats_serp_item(hit_stale, matched_token="LangGraph", event_date=fresh_date)
    assert event is not None
    assert event.event_date == fresh_date


def test_zero_cost_ats_sweep_skips_non_dict_and_handles_limit_zero():
    provider = ZeroCostATSSweep()
    # Non-dict hits in the list should be safely skipped
    hits = [
        "not-a-dict",
        None,
        123,
        {
            "title": "AI Engineer at Acme Financial | Greenhouse",
            "url": "https://example.com/jobs/1",
            "snippet": "Experience with LangGraph.",
        },
    ]
    events = provider.fetch_events("LangGraph", limit=10, hits=hits)
    assert len(events) == 1
    assert events[0].company_name == "Acme Financial"

    # limit <= 0 returns empty list immediately
    assert provider.fetch_events("LangGraph", limit=0, hits=hits) == []
    assert provider.fetch_events("LangGraph", limit=-5, hits=hits) == []


def test_signal_provider_accepts_profile_kwarg():
    ats = ZeroCostATSSweep()
    assert ats.fetch_events("LangGraph", limit=1, profile="test-tenant") == []

    newswire = ZeroCostNewswireSweep()
    assert newswire.fetch_events("Funding", limit=1, profile="test-tenant") == []


class _FakeResponse:
    def __init__(self, data: bytes, status: int = 200):
        self.data = data
        self.status = status

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeOpener:
    def __init__(self, response_data: bytes | Exception):
        self.response_data = response_data
        self.requests: list[tuple[urllib.request.Request, float | None]] = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if isinstance(self.response_data, Exception):
            raise self.response_data
        return _FakeResponse(self.response_data)


def test_theirstack_fetch_events_success_and_cost_metering(tmp_path, monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))

    fake_data = {
        "data": [
            {
                "job_title": "Senior AI Architect at Acme Robotics | Greenhouse",
                "snippet": "Building LangGraph multi-agent systems and resolving tool-calling latency.",
                "url": "https://example.com/jobs/101",
                "company_domain": "example.com",
                "posted_at": "2026-10-04T12:00:00Z",
            }
        ]
    }
    fake_opener = _FakeOpener(json.dumps(fake_data).encode("utf-8"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    ts = TheirStackAccelerator()
    events = ts.fetch_events("LangGraph", limit=5, profile="test-tenant")

    assert len(events) == 1
    ev = events[0]
    assert ev.company_name == "Acme Robotics"
    assert ev.company_domain == "example.com"
    assert ev.provider == "theirstack"
    assert ev.event_date == "2026-10-04"
    assert "LangGraph" in ev.meta["frameworks"]
    assert "tool-calling" in ev.meta["pain_cue"]

    # Check payload sent to API: technology canonicalization
    assert len(fake_opener.requests) == 1
    req, timeout = fake_opener.requests[0]
    assert timeout == 10.0
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["job_technology_slug_or"] == ["langgraph"]
    assert payload["posted_at_max_age_days"] == 45
    assert payload["limit"] == 5

    # Check cost ledger
    cost_file = tmp_path / "test-tenant" / "costs.jsonl"
    assert cost_file.is_file()
    lines = cost_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    cost_record = json.loads(lines[0])
    assert cost_record["tool"] == "theirstack"
    assert cost_record["op"] == "jobs_search"
    assert cost_record["units"] == 1


def test_theirstack_fetch_events_keyword_payload(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_data = {"data": []}
    fake_opener = _FakeOpener(json.dumps(fake_data).encode("utf-8"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    ts = TheirStackAccelerator()
    events = ts.fetch_events("CustomSearchTerm", limit=3)
    assert events == []

    req, _ = fake_opener.requests[0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["job_keyword_slug_or"] == ["CustomSearchTerm"]
    assert "job_technology_slug_or" not in payload


def test_theirstack_fallback_on_402_and_429(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")

    ats_hit = {
        "title": "AI Engineer at Acme Robotics | Greenhouse",
        "url": "https://example.com/jobs/fallback",
        "snippet": "Experience with LangGraph.",
    }

    # Test HTTP 402
    err_402 = urllib.error.HTTPError(
        "https://api.theirstack.com/v1/jobs/search", 402, "Payment Required", {}, None
    )
    fake_opener_402 = _FakeOpener(err_402)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener_402)

    ts = TheirStackAccelerator()
    events_402 = ts.fetch_events("LangGraph", limit=5, hits=[ats_hit])
    assert len(events_402) == 1
    assert events_402[0].company_name == "Acme Robotics"
    assert events_402[0].provider == "ats_sweep"
    assert ts.fallback_reason == "HTTP 402 (Credit Exhaustion)"

    # Test HTTP 429
    err_429 = urllib.error.HTTPError(
        "https://api.theirstack.com/v1/jobs/search", 429, "Too Many Requests", {}, None
    )
    fake_opener_429 = _FakeOpener(err_429)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener_429)

    ts_429 = TheirStackAccelerator()
    events_429 = ts_429.fetch_events("LangGraph", limit=5, hits=[ats_hit])
    assert len(events_429) == 1
    assert events_429[0].company_name == "Acme Robotics"
    assert ts_429.fallback_reason == "HTTP 429 (Rate Limit)"


def test_theirstack_error_boundary_returns_empty(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")

    # HTTP 500
    err_500 = urllib.error.HTTPError(
        "https://api.theirstack.com/v1/jobs/search", 500, "Internal Server Error", {}, None
    )
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: _FakeOpener(err_500))
    ts = TheirStackAccelerator()
    assert ts.fetch_events("LangGraph", limit=5) == []

    # Malformed JSON
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *args: _FakeOpener(b"invalid-json-blob")
    )
    assert ts.fetch_events("LangGraph", limit=5) == []

    # Non-dict JSON
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: _FakeOpener(b"[]"))
    assert ts.fetch_events("LangGraph", limit=5) == []


def test_theirstack_no_redirect_refuses():
    handler = _NoRedirect()
    with pytest.raises(EgressRefused, match="refused, not followed"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example.com")


def test_theirstack_date_and_agency_robustness(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")

    fake_data = {
        "data": [
            "not-a-dict-item",
            # Agency item — should be filtered
            {
                "job_title": "AI Architect at Apex Talent Solutions",
                "snippet": "We are a premier staffing agency.",
                "url": "https://example.com/jobs/agency",
            },
            # Missing posted_at date — should fall back to today
            {
                "job_title": "AI Architect at Acme Financial | Greenhouse",
                "snippet": "Working on LangGraph systems.",
                "url": "https://example.com/jobs/no-date",
                "posted_at": None,
            },
        ]
    }
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *args: _FakeOpener(json.dumps(fake_data).encode("utf-8")),
    )

    ts = TheirStackAccelerator()
    events = ts.fetch_events("LangGraph", limit=5)
    assert len(events) == 1
    assert events[0].company_name == "Acme Financial"
    assert events[0].event_date == datetime.date.today().isoformat()


def test_theirstack_unapproved_host_refused(monkeypatch):
    import gtm_core.signal_events.providers as prov_mod

    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    monkeypatch.setattr(prov_mod, "ALLOWED_THEIRSTACK_HOSTS", ("other.example.com",))
    ts = TheirStackAccelerator()
    with pytest.raises(EgressRefused, match="not in ALLOWED_THEIRSTACK_HOSTS"):
        ts.fetch_events("LangGraph")


def test_theirstack_malformed_posted_at_date_falls_back_to_today(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_data = {
        "data": [
            {
                "job_title": "AI Architect at Acme Financial | Greenhouse",
                "snippet": "Working on LangGraph systems.",
                "url": "https://example.com/jobs/bad-date-1",
                "posted_at": "not-a-valid-date-string",
            },
            {
                "job_title": "AI Architect at Acme Financial | Greenhouse",
                "snippet": "Working on LangGraph systems.",
                "url": "https://example.com/jobs/bad-date-2",
                "posted_at": 99999999,
            },
        ]
    }
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *args: _FakeOpener(json.dumps(fake_data).encode("utf-8")),
    )
    ts = TheirStackAccelerator()
    events = ts.fetch_events("LangGraph", limit=5)
    assert len(events) == 2
    assert events[0].event_date == datetime.date.today().isoformat()
    assert events[1].event_date == datetime.date.today().isoformat()


def test_theirstack_company_precedence_and_snippet_agency_distinction(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_data = {
        "data": [
            {
                "job_title": "Director of Engineering at Scale",
                "company_name": "Acme Analytics Inc.",
                "company_domain": "acmeanalytics.example.com",
                "snippet": "We are actively recruiting an AI systems architect.",
                "url": "https://example.com/jobs/1",
                "posted_at": "2026-10-04T10:00:00Z",
            },
            {
                "job_title": "AI Architect",
                "company_name": "Apex Staffing Agency",
                "company_domain": "apex.example.com",
                "snippet": "Role at client.",
                "url": "https://example.com/jobs/2",
                "posted_at": "2026-10-04T10:00:00Z",
            },
        ]
    }
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *args: _FakeOpener(json.dumps(fake_data).encode("utf-8")),
    )
    ts = TheirStackAccelerator()
    events = ts.fetch_events("LangGraph", limit=5)
    # The first item should prioritize company_name (Acme Analytics) and NOT be dropped by 'recruiting' in snippet
    assert len(events) == 1
    assert events[0].company_name == "Acme Analytics"
    assert events[0].company_domain == "acmeanalytics.example.com"


def test_theirstack_multiword_technology_slug_kebab_cased(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_data = {"data": []}
    fake_opener = _FakeOpener(json.dumps(fake_data).encode("utf-8"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    ts = TheirStackAccelerator()
    ts.fetch_events("Semantic Kernel", limit=3)
    req, _ = fake_opener.requests[0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["job_technology_slug_or"] == ["semantic-kernel"]


def test_theirstack_network_unreachable_fallback(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_opener = _FakeOpener(urllib.error.URLError("Connection refused"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    ts = TheirStackAccelerator()
    events = ts.fetch_events("LangGraph", limit=5)
    assert events == []
    assert ts.fallback_reason == "Network Timeout / Unreachable"


def test_theirstack_budget_cap_exceeded_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))

    prof = "test-tenant"
    p_dir = tmp_path / "profiles" / prof
    p_dir.mkdir(parents=True)
    (p_dir / "PROFILE.md").write_text("monthly_tool_budget_usd: 10.0\n", encoding="utf-8")

    c_dir = tmp_path / "content" / prof
    c_dir.mkdir(parents=True)
    # Write cost record exceeding $10.00 cap
    (c_dir / "costs.jsonl").write_text(
        json.dumps(
            {
                "ts": datetime.datetime.now(datetime.UTC).isoformat(),
                "tool": "theirstack",
                "op": "jobs_search",
                "units": 1,
                "unit_kind": "queries",
                "cost_usd": 15.00,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    fake_opener = _FakeOpener(b"{}")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    ts = TheirStackAccelerator()
    events = ts.fetch_events("LangGraph", limit=5, profile=prof)
    assert events == []
    assert ts.fallback_reason == "Monthly Budget Cap Exceeded"
    # Verify no HTTP call was made
    assert len(fake_opener.requests) == 0


def test_theirstack_schema_resilience_on_non_string_types(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_data = {
        "data": [
            {
                "job_title": 12345,
                "company_name": "Acme Robotics",
                "company_domain": 67890,
                "snippet": None,
                "url": 111,
                "posted_at": None,
            },
            None,
            "not a dict",
        ]
    }
    fake_opener = _FakeOpener(json.dumps(fake_data).encode("utf-8"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    ts = TheirStackAccelerator()
    events = ts.fetch_events("LangGraph", limit=5)
    assert len(events) == 1
    assert events[0].company_name == "Acme Robotics"
    assert events[0].company_domain is None
    assert events[0].meta["date_confidence"] == "inferred"


def test_theirstack_fallback_memoization_skips_http(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_opener = _FakeOpener(b"{}")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    ts = TheirStackAccelerator()
    ts.fallback_reason = "Prior Error"
    events = ts.fetch_events("LangGraph", limit=5)
    assert events == []
    assert len(fake_opener.requests) == 0


def test_theirstack_build_payload_clamping_and_normalization():
    # Token with hyphen should normalize to canonical technology slug
    p1 = TheirStackAccelerator._build_payload("semantic-kernel", limit=100)
    assert p1["job_technology_slug_or"] == ["semantic-kernel"]
    # Limit clamped to 50 max
    assert p1["limit"] == 50

    p2 = TheirStackAccelerator._build_payload("custom keyword", limit=0)
    assert p2["limit"] == 1


def test_theirstack_domain_sanitization_strips_scheme_and_path(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_data = {
        "data": [
            {
                "job_title": "AI Architect",
                "company_name": "Acme Robotics",
                "company_domain": "https://example.com/careers/jobs",
                "snippet": "LangGraph systems.",
                "url": "https://example.com/job/1",
                "posted_at": "2026-10-04T10:00:00Z",
            }
        ]
    }
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *args: _FakeOpener(json.dumps(fake_data).encode("utf-8")),
    )
    ts = TheirStackAccelerator()
    events = ts.fetch_events("LangGraph", limit=5)
    assert len(events) == 1
    assert events[0].company_domain == "example.com"


def test_theirstack_empty_token_returns_empty_without_egress(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "test-key-123")
    fake_opener = _FakeOpener(b"{}")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake_opener)

    ts = TheirStackAccelerator()
    assert ts.fetch_events("   ", limit=5) == []
    assert ts.fetch_events("", limit=5) == []
    assert len(fake_opener.requests) == 0

"""Unit tests for the Apollo worker MCP (``agent.mcp.apollo``) — the last paid
contact-resolution source in the prospect skill's waterfall (RocketReach -> Vibe ->
Apollo -> web), plus company search (incl. buying-intent filters) and job-posting
hiring signals.

Network-free: ``httpx.AsyncClient`` is monkeypatched. These pin the REST contract
(POST/GET paths, header auth), the shaped/compact result payloads, the
``[apollo-error]``/``[apollo-cap] …`` degradation contract, the count-based monthly
credit cap (mirroring RocketReach's lookup cap), and — critically — that phone
reveal is never requested (it would need an inbound webhook this deployment can't
receive).
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

import httpx  # noqa: E402

from agent.mcp.apollo import server  # noqa: E402

# ── fake httpx client ─────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, captured: dict, payload, status: int) -> None:
        self._captured = captured
        self._payload = payload
        self._status = status

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def get(self, url, params=None, headers=None):
        self._captured.update(method="GET", url=url, params=params, headers=headers)
        return _FakeResp(self._payload, self._status)

    async def post(self, url, json=None, params=None, headers=None):  # noqa: A002
        self._captured.update(method="POST", url=url, json=json, params=params, headers=headers)
        return _FakeResp(self._payload, self._status)


def _patch_client(monkeypatch, captured, payload, status=200) -> None:
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: _FakeClient(captured, payload, status)
    )


def _set_key(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_API_KEY", "kk")


# ── apollo_usage ───────────────────────────────────────────────────────────────


def test_usage_requires_key(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    out = asyncio.run(server._usage())
    assert out.startswith("[apollo-error]")
    assert "APOLLO_API_KEY" in out


def test_usage_reports_health_and_ledger_cap(monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.delenv("APOLLO_MONTHLY_CREDIT_CAP", raising=False)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"is_logged_in": True})
    out = json.loads(asyncio.run(server._usage()))
    assert out["is_logged_in"] is True
    assert out["monthly_credit_cap"] is None
    assert out["credits_remaining_this_month"] is None


def test_usage_preflight_leads_with_the_spec_documented_profile_endpoint(monkeypatch):
    """`/auth/health` does NOT appear anywhere in Apollo's published OpenAPI spec (71
    paths, zero auth/* or health/*), so hanging "is Apollo connected?" on it is the
    weakest available option. `/users/api_profile` IS in the spec, is credit-free, needs
    no master key, and is the same endpoint Apollo's hosted MCP uses for its preflight."""
    _set_key(monkeypatch)
    urls: list[str] = []

    class _RecordingClient(_FakeClient):
        async def get(self, url, params=None, headers=None):
            urls.append(url)
            return await super().get(url, params=params, headers=headers)

    monkeypatch.setattr(
        server.httpx,
        "AsyncClient",
        lambda *a, **k: _RecordingClient({}, {"is_logged_in": True}, 200),
    )
    asyncio.run(server._usage())
    assert urls == ["https://api.apollo.io/api/v1/users/api_profile"]


def test_usage_preflight_falls_back_through_both_health_paths_only_on_404(monkeypatch):
    """Apollo's docs carried both auth/health forms across the base-URL migration, and
    the endpoint is spec-undocumented either way — so the fallback tries both."""
    _set_key(monkeypatch)
    urls: list[str] = []

    class _NotFoundThenOk(_FakeClient):
        async def get(self, url, params=None, headers=None):
            urls.append(url)
            if url.endswith("/v1/auth/health") and "/api/v1/" not in url:
                return _FakeResp({"is_logged_in": True}, 200)
            return _FakeResp({}, 404)

        async def post(self, url, json=None, params=None, headers=None):  # noqa: A002
            return _FakeResp({}, 403)

    monkeypatch.setattr(server.httpx, "AsyncClient", lambda *a, **k: _NotFoundThenOk({}, {}, 200))
    out = json.loads(asyncio.run(server._usage()))
    assert urls == [
        "https://api.apollo.io/api/v1/users/api_profile",
        "https://api.apollo.io/api/v1/auth/health",
        "https://api.apollo.io/v1/auth/health",
    ]
    assert out["is_logged_in"] is True


def test_usage_health_does_not_retry_a_401_against_the_legacy_path(monkeypatch):
    """A 401/403 is a real auth answer about a real endpoint — retrying elsewhere would
    turn a clear "your key is bad" into a confusing second failure."""
    _set_key(monkeypatch)
    urls: list[str] = []

    class _Unauthorized(_FakeClient):
        async def get(self, url, params=None, headers=None):
            urls.append(url)
            return _FakeResp({}, 401)

    monkeypatch.setattr(server.httpx, "AsyncClient", lambda *a, **k: _Unauthorized({}, {}, 401))
    out = asyncio.run(server._usage())
    assert len(urls) == 1
    assert out.startswith("[apollo-error]")


def test_job_postings_page_size_is_not_clamped_to_the_search_limit(monkeypatch):
    """Job postings bill 1 credit PER PAGE regardless of size, and Apollo allows much
    larger pages here than on search. Clamping to the search limit of 100 would make a
    big employer's postings cost up to 100x more credits for identical data."""
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"organization_job_postings": []})
    asyncio.run(server.apollo_job_postings("org1", per_page=1000))
    assert captured["params"]["per_page"] == 1000


def test_usage_tolerates_non_master_key_on_rate_limits(monkeypatch):
    """The usage-stats endpoint is master-key-only. A 403 there must NOT fail the
    preflight — the key is demonstrably valid (health passed), it just lacks that
    one scope."""
    _set_key(monkeypatch)

    class _SplitClient(_FakeClient):
        async def get(self, url, params=None, headers=None):  # health: OK
            return _FakeResp({"is_logged_in": True}, 200)

        async def post(self, url, json=None, params=None, headers=None):  # noqa: A002 — usage stats: 403
            return _FakeResp({}, 403)

    monkeypatch.setattr(server.httpx, "AsyncClient", lambda *a, **k: _SplitClient({}, {}, 200))
    out = json.loads(asyncio.run(server._usage()))
    assert out["is_logged_in"] is True
    assert "master API key" in out["rate_limits"]


def test_usage_reports_live_rate_limits_with_master_key(monkeypatch):
    _set_key(monkeypatch)
    stats = {"usage_stats": {'["api/v1/people/match"]': {"day": {"limit": 6000, "consumed": 12}}}}

    class _SplitClient(_FakeClient):
        async def get(self, url, params=None, headers=None):
            return _FakeResp({"is_logged_in": True}, 200)

        async def post(self, url, json=None, params=None, headers=None):  # noqa: A002
            return _FakeResp(stats, 200)

    monkeypatch.setattr(server.httpx, "AsyncClient", lambda *a, **k: _SplitClient({}, {}, 200))
    out = json.loads(asyncio.run(server._usage()))
    assert out["rate_limits"] == stats["usage_stats"]


def test_usage_http_error_degrades(monkeypatch):
    _set_key(monkeypatch)
    _patch_client(monkeypatch, {}, {}, status=401)
    out = asyncio.run(server._usage())
    assert out == "[apollo-error] HTTP 401"


# ── apollo_person_search (free) ─────────────────────────────────────────────────


def test_person_search_requires_key(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    out = asyncio.run(server.apollo_person_search({"person_titles": ["CISO"]}))
    assert out.startswith("[apollo-error]")
    assert "APOLLO_API_KEY" in out


def test_person_search_rejects_empty_query(monkeypatch):
    _set_key(monkeypatch)
    out = asyncio.run(server.apollo_person_search({}))
    assert out.startswith("[apollo-error]")


def test_person_search_request_and_shape_matches_real_apollo_payload(monkeypatch):
    """Pins the ACTUAL documented People API Search response shape (verified
    2026-07-27): `total_entries` is TOP-LEVEL (not under `pagination`, unlike
    company search), surnames come back masked as `last_name_obfuscated`, and
    there is no email/phone/linkedin_url/domain in search results at all."""
    _set_key(monkeypatch)
    captured: dict = {}
    payload = {
        "total_entries": 232764882,  # top level — the company-search endpoint nests this
        "people": [
            {
                "id": "67bdafd0c3a4c50001bbd7c2",
                "first_name": "Andrew",
                "last_name_obfuscated": "Hu***n",
                "title": "Professor and Neuroscientist",
                "has_email": True,
                "has_direct_phone": "Yes",
                "organization": {"name": "Aster Media", "has_industry": True},
            }
        ],
    }
    _patch_client(monkeypatch, captured, payload)

    out = json.loads(asyncio.run(server.apollo_person_search({"person_titles": ["CISO"]})))

    assert captured["url"].endswith("/mixed_people/api_search")
    assert captured["json"]["person_titles"] == ["CISO"]
    assert captured["json"]["page"] == 1
    assert captured["headers"]["X-Api-Key"] == "kk"
    assert out["count"] == 1
    assert out["total"] == 232764882  # regression: was None when read from pagination
    person = out["people"][0]
    assert person["id"] == "67bdafd0c3a4c50001bbd7c2"
    assert person["first_name"] == "Andrew"
    assert person["last_name_obfuscated"] == "Hu***n"
    assert person["organization_name"] == "Aster Media"
    assert person["has_email"] is True
    # Search never carries contact data — enrichment is the metered call that does.
    assert "email" not in person and "phones" not in person and "linkedin_url" not in person


def test_person_search_never_leaks_contact_fields_even_if_api_returns_them(monkeypatch):
    """Defense in depth: even if a future Apollo response leaked an email into a
    search hit, the search shaper must not surface it — that would make a free
    search look like a substitute for the metered enrich."""
    _set_key(monkeypatch)
    payload = {
        "total_entries": 1,
        "people": [
            {
                "id": "p1",
                "first_name": "Jane",
                "last_name_obfuscated": "Do***e",
                "email": "leak@acme.com",
                "phone_numbers": [{"sanitized_number": "+15551234567"}],
                "organization": {"name": "Acme"},
            }
        ],
    }
    _patch_client(monkeypatch, {}, payload)
    out = json.loads(asyncio.run(server.apollo_person_search({"person_titles": ["CISO"]})))
    assert "leak@acme.com" not in json.dumps(out)
    assert "+15551234567" not in json.dumps(out)


def test_person_search_page_size_clamped(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"people": [], "pagination": {}})
    asyncio.run(server.apollo_person_search({"person_titles": ["CISO"]}, per_page=999))
    assert captured["json"]["per_page"] == server._SEARCH_MAX_PAGE_SIZE


def test_person_search_never_meters(monkeypatch):
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    _patch_client(monkeypatch, {}, {"people": [{"id": 1}], "pagination": {"total_entries": 1}})
    asyncio.run(server.apollo_person_search({"person_titles": ["CISO"]}))
    assert metered == []


# ── apollo_person_enrich (metered, phone reveal forbidden) ──────────────────────


def test_person_enrich_requires_key(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    out = asyncio.run(server.apollo_person_enrich(name="Jane Doe", organization_name="Acme"))
    assert out.startswith("[apollo-error]")


def test_person_enrich_requires_identifier(monkeypatch):
    _set_key(monkeypatch)
    out = asyncio.run(server.apollo_person_enrich())
    assert out.startswith("[apollo-error]")
    assert "identifying fields" in out


def test_person_enrich_accepts_apollo_id_for_search_chaining(monkeypatch):
    """The intended flow is apollo_person_search (free, returns an `id`) →
    apollo_person_enrich(id=...). Without `id` support that chain is impossible,
    since search deliberately masks the surname."""
    _set_key(monkeypatch)
    captured: dict = {}
    payload = {"person": {"id": "64a7ff0c", "name": "Alex Rivera", "email": "alex@apollo.io"}}
    _patch_client(monkeypatch, captured, payload)
    out = json.loads(asyncio.run(server.apollo_person_enrich(id="64a7ff0c")))
    assert captured["json"]["id"] == "64a7ff0c"
    assert captured["json"]["reveal_phone_number"] is False
    assert out["email"] == "alex@apollo.io"


def test_bulk_enrich_passes_apollo_id_through_to_details(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"matches": [{"id": "x", "email": "a@b.com"}]})
    asyncio.run(server.apollo_bulk_person_enrich([{"id": "64a7ff0c"}]))
    assert captured["json"]["details"][0]["id"] == "64a7ff0c"


def test_person_enrich_never_requests_phone_reveal(monkeypatch):
    """The core guardrail: reveal_phone_number must always be sent as False —
    Apollo's phone reveal resolves async via a caller webhook this headless
    deployment cannot receive, and costs 8 extra credits per match."""
    _set_key(monkeypatch)
    captured: dict = {}
    payload = {
        "person": {
            "id": "p1",
            "name": "Jane Doe",
            "email": "jane@acme.com",
            "email_status": "verified",
        }
    }
    _patch_client(monkeypatch, captured, payload)
    asyncio.run(server.apollo_person_enrich(name="Jane Doe", organization_name="Acme"))
    assert captured["json"]["reveal_phone_number"] is False
    assert "webhook_url" not in captured["json"]


def test_person_enrich_meters_only_on_email_match(monkeypatch):
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    payload = {
        "person": {
            "id": "p1",
            "name": "Jane Doe",
            "email": "jane@acme.com",
            "email_status": "verified",
        }
    }
    _patch_client(monkeypatch, {}, payload)
    out = json.loads(
        asyncio.run(server.apollo_person_enrich(name="Jane Doe", organization_name="Acme"))
    )
    assert out["email"] == "jane@acme.com"
    assert out["phones"] == []
    assert metered == [(1, "person_enrich")]


def test_person_enrich_no_match_not_metered(monkeypatch):
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    _patch_client(monkeypatch, {}, {"person": {}})
    out = asyncio.run(server.apollo_person_enrich(name="Nobody", organization_name="Acme"))
    assert out.startswith("[apollo-error]")
    assert metered == []


def test_person_enrich_hard_stops_at_cap_and_makes_no_http(monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.setattr(server, "_allowance_remaining", lambda: 0)

    def _boom(*a, **k):
        raise AssertionError("no HTTP call must be made once the allowance is exhausted")

    monkeypatch.setattr(server.httpx, "AsyncClient", _boom)
    out = asyncio.run(server.apollo_person_enrich(name="Jane Doe", organization_name="Acme"))
    assert out.startswith("[apollo-cap]")


# ── apollo_bulk_person_enrich ────────────────────────────────────────────────────


def test_bulk_enrich_rejects_over_batch_cap(monkeypatch):
    _set_key(monkeypatch)
    people = [{"name": f"P{i}", "organization_name": "Acme"} for i in range(11)]
    out = asyncio.run(server.apollo_bulk_person_enrich(people))
    assert out.startswith("[apollo-error]")
    assert "10" in out


def test_bulk_enrich_rejects_batch_over_remaining_allowance(monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.setattr(server, "_allowance_remaining", lambda: 2)
    people = [{"name": f"P{i}", "organization_name": "Acme"} for i in range(5)]
    out = asyncio.run(server.apollo_bulk_person_enrich(people))
    assert out.startswith("[apollo-cap]")


def test_bulk_enrich_never_requests_phone_reveal(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    payload = {"matches": [{"id": "p1", "name": "Jane Doe", "email": "jane@acme.com"}]}
    _patch_client(monkeypatch, captured, payload)
    asyncio.run(
        server.apollo_bulk_person_enrich([{"name": "Jane Doe", "organization_name": "Acme"}])
    )
    assert captured["json"]["reveal_phone_number"] is False
    for detail in captured["json"]["details"]:
        assert detail["reveal_phone_number"] is False


def test_bulk_enrich_meters_only_resolved(monkeypatch):
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    payload = {
        "matches": [
            {"id": "p1", "name": "Jane Doe", "email": "jane@acme.com"},
            {"id": "p2", "name": "No Match"},  # no email ⇒ not resolved
        ]
    }
    _patch_client(monkeypatch, {}, payload)
    people = [
        {"name": "Jane Doe", "organization_name": "Acme"},
        {"name": "No Match", "organization_name": "Acme"},
    ]
    out = json.loads(asyncio.run(server.apollo_bulk_person_enrich(people)))
    assert out["resolved"] == 1
    assert out["count"] == 2
    # No credits_consumed in this payload ⇒ fall back to the resolved count.
    assert metered == [(1, "bulk_person_enrich")]


def test_bulk_enrich_meters_apollos_reported_credits_not_the_email_count(monkeypatch):
    """Apollo charges for demographics-only matches that return NO email.

    Counting emails under-meters real spend, so the monthly credit cap drifts low
    and silently over-spends. Apollo reports `credits_consumed`; prefer it.
    """
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    payload = {
        "status": "success",
        "total_requested_enrichments": 3,
        "unique_enriched_records": 3,
        "missing_records": 0,
        "credits_consumed": 3,  # Apollo charged 3 …
        "matches": [
            {"id": "p1", "name": "A", "email": "a@acme.com"},
            {"id": "p2", "name": "B", "email": "b@acme.com"},
            {"id": "p3", "name": "C"},  # … but only 2 carry an email
        ],
    }
    _patch_client(monkeypatch, {}, payload)
    people = [{"name": n, "organization_name": "Acme"} for n in ("A", "B", "C")]
    out = json.loads(asyncio.run(server.apollo_bulk_person_enrich(people)))
    assert out["resolved"] == 2
    assert out["credits_consumed"] == 3
    assert metered == [(3, "bulk_person_enrich")]
    assert out["requested"] == 3 and out["missing_records"] == 0


def test_bulk_enrich_does_not_guess_the_query_when_apollo_drops_records(monkeypatch):
    """If Apollo returns fewer matches than queries, index-mapping mislabels people."""
    _set_key(monkeypatch)
    payload = {
        "credits_consumed": 1,
        "missing_records": 1,
        "matches": [{"id": "p2", "name": "Second Person"}],  # 1 match for 2 queries
    }
    _patch_client(monkeypatch, {}, payload)
    people = [
        {"name": "First Person", "organization_name": "Acme"},
        {"name": "Second Person", "organization_name": "Acme"},
    ]
    out = json.loads(asyncio.run(server.apollo_bulk_person_enrich(people)))
    assert out["query_mapping_reliable"] is False
    assert out["results"][0]["query"] is None  # not mislabelled as "First Person"


def test_bulk_enrich_resolves_org_name_from_employment_history(monkeypatch):
    """`/people/bulk_match` matches carry employment_history but NO nested organization.

    Without the fallback every bulk-enriched contact came back with a null
    organization_name — a resolved contact the skill cannot attribute to an account.
    """
    _set_key(monkeypatch)
    payload = {
        "matches": [
            {
                "id": "64a7ff0cc4dfae00013df1a5",
                "name": "Alex Rivera",
                "email": "alex@apollo.io",
                "organization_id": "5e66b6381e05b4008c8331b8",
                "employment_history": [
                    {"current": False, "organization_name": "Braingenie", "title": "Founder"},
                    {"current": True, "organization_name": "Apollo", "title": "Founder & CEO"},
                ],
            }
        ]
    }
    _patch_client(monkeypatch, {}, payload)
    out = json.loads(
        asyncio.run(server.apollo_bulk_person_enrich([{"id": "64a7ff0cc4dfae00013df1a5"}]))
    )
    assert out["results"][0]["organization_name"] == "Apollo"  # the CURRENT job, not the first


def test_enrich_sends_identifiers_and_reveal_flags_as_query_params(monkeypatch):
    """Apollo's OpenAPI spec declares `/people/match` params as `in: query` with NO
    request body. Sending identifiers only in the body bets the whole call on
    undocumented body-merge behaviour; sending the reveal flags only in the body would
    leave the never-reveal-a-phone guarantee resting on Apollo's default."""
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"person": {"id": "p1", "email": "a@acme.com"}})
    asyncio.run(server.apollo_person_enrich(name="Jane Doe", organization_name="Acme"))
    query = dict(captured["params"])
    assert query["reveal_phone_number"] == "false"
    assert query["reveal_personal_emails"] == "false"
    assert query["name"] == "Jane Doe"
    assert query["organization_name"] == "Acme"


# ── query encoding: Apollo's documented `[]` / `[min]` param spelling ──────────


def test_list_filters_are_encoded_with_apollos_bracket_suffix():
    """Rails parses `a[]=1&a[]=2` into a list but `a=1&a=2` into the single value "2".

    Apollo's spec names these params literally `person_titles[]`, so an un-suffixed
    repeated key would silently narrow a multi-value filter to its LAST element — a
    wrong-results bug with no error to notice.
    """
    encoded = server._query_params({"person_titles": ["VP Engineering", "CTO"]})
    assert encoded == [("person_titles[]", "VP Engineering"), ("person_titles[]", "CTO")]


def test_query_encoding_accepts_apollos_literal_spelling_too():
    """The skill documents `person_titles`; Apollo spells it `person_titles[]`. Both work."""
    assert server._query_params({"person_titles[]": ["CTO"]}) == [("person_titles[]", "CTO")]


def test_range_filters_are_encoded_as_bracketed_subkeys():
    assert sorted(server._query_params({"revenue_range": {"min": 1, "max": 9}})) == [
        ("revenue_range[max]", "9"),
        ("revenue_range[min]", "1"),
    ]


def test_booleans_are_encoded_as_lowercase_json_style():
    assert server._query_params({"include_similar_titles": False}) == [
        ("include_similar_titles", "false")
    ]


def test_person_search_sends_filters_as_query_params_not_only_body(monkeypatch):
    """If Apollo reads only query params (as the spec says), a body-only search returns
    the UNFILTERED universe — free but useless here, and 1 wasted credit on company
    search, with no error either way."""
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"total_entries": 0, "people": []})
    asyncio.run(server.apollo_person_search({"person_titles": ["CTO"], "q_keywords": "fintech"}))
    query = captured["params"]
    assert ("person_titles[]", "CTO") in query
    assert ("q_keywords", "fintech") in query
    # The body is kept too, carrying identical values, so the two can never disagree.
    assert captured["json"]["person_titles"] == ["CTO"]


def test_company_search_sends_filters_as_query_params(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"organizations": [], "pagination": {}})
    asyncio.run(server.apollo_company_search({"organization_num_employees_ranges": ["250,1000"]}))
    assert ("organization_num_employees_ranges[]", "250,1000") in captured["params"]


def test_search_page_is_clamped_to_apollos_500_page_display_ceiling(monkeypatch):
    """Past page 500 Apollo returns nothing — and company search still bills the call."""
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"organizations": [], "pagination": {}})
    asyncio.run(server.apollo_company_search({"q_organization_name": "Acme"}, page=9999))
    assert captured["json"]["page"] == 500


# ── locked/placeholder emails (the wrong-contact failure mode) ────────────────


@pytest.mark.parametrize(
    "placeholder",
    [
        "email_not_unlocked@domain.com",
        "email_not_unlocked@apollo.io",
        "EMAIL_NOT_UNLOCKED@Domain.com",
    ],
)
def test_placeholder_email_is_never_treated_as_a_resolved_address(monkeypatch, placeholder):
    """Apollo returns `email_not_unlocked@domain.com` when a record isn't unlocked.

    Every other defect in this worker costs a MISSING contact that falls through to the
    web path. This one manufactures a WRONG one: a syntactically valid, undeliverable
    address that would pass the consolidator, reach ready-to-load.csv, and be enrollable
    in a sequence.
    """
    _set_key(monkeypatch)
    _patch_client(monkeypatch, {}, {"person": {"id": "p1", "name": "Jane", "email": placeholder}})
    out = asyncio.run(server.apollo_person_enrich(name="Jane Doe", organization_name="Acme"))
    assert out.startswith("[apollo-error]")
    assert "locked placeholder" in out
    assert placeholder not in out


def test_placeholder_email_does_not_consume_a_credit(monkeypatch):
    """A locked record resolved nothing, so it must not be metered as a resolution."""
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    _patch_client(
        monkeypatch, {}, {"person": {"id": "p1", "email": "email_not_unlocked@domain.com"}}
    )
    asyncio.run(server.apollo_person_enrich(name="Jane Doe", organization_name="Acme"))
    assert metered == []


def test_bulk_enrich_does_not_count_placeholder_emails_as_resolved(monkeypatch):
    _set_key(monkeypatch)
    payload = {
        "matches": [
            {"id": "p1", "name": "Real", "email": "real@acme.com"},
            {"id": "p2", "name": "Locked", "email": "email_not_unlocked@domain.com"},
        ]
    }
    _patch_client(monkeypatch, {}, payload)
    out = json.loads(
        asyncio.run(server.apollo_bulk_person_enrich([{"name": "Real"}, {"name": "Locked"}]))
    )
    assert out["resolved"] == 1
    assert out["results"][1].get("error")


def test_revealed_for_current_team_is_surfaced(monkeypatch):
    """Apollo's own signal for whether a record is unlocked for this team."""
    _set_key(monkeypatch)
    _patch_client(
        monkeypatch,
        {},
        {"person": {"id": "p1", "email": "a@acme.com", "revealed_for_current_team": True}},
    )
    out = json.loads(asyncio.run(server.apollo_person_enrich(name="Jane", domain="acme.com")))
    assert out["revealed_for_current_team"] is True


# ── apollo_company_search (metered per page/call, incl. intent filters) ─────────


def test_company_search_requires_key(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    out = asyncio.run(server.apollo_company_search({"q_organization_name": "Acme"}))
    assert out.startswith("[apollo-error]")


def test_company_search_request_and_shape_matches_real_apollo_payload(monkeypatch):
    """Pinned to Apollo's DOCUMENTED organization-search response.

    The search endpoint returns identity/social fields plus the intent payload, and
    NOT industry / estimated_num_employees / city / state / country — those exist
    only on the enrichment endpoints. Shaping a search hit with the enrichment
    shaper silently produced nulls for every firmographic field.
    """
    _set_key(monkeypatch)
    captured: dict = {}
    payload = {
        "pagination": {"page": 1, "per_page": 2, "total_entries": 1184, "total_pages": 592},
        "accounts": [],
        "organizations": [
            {
                "id": "615d029256de500001bdb460",
                "name": "Asia Business Review",
                "website_url": "http://www.asiabizreview.example",
                "linkedin_url": "http://www.linkedin.com/company/asiabizreview",
                "primary_domain": "asiabizreview.example",
                "founded_year": 1994,
                "phone": "+81 3-555-0123",
                "sanitized_phone": "+8135550123",
                "alexa_ranking": 1583,
                "intent_strength": None,
                "show_intent": True,
                "has_intent_signal_account": False,
            }
        ],
    }
    _patch_client(monkeypatch, captured, payload)

    out = json.loads(
        asyncio.run(server.apollo_company_search({"q_organization_name": "Asia Business"}))
    )

    assert captured["url"].endswith("/mixed_companies/search")
    # Company search nests total_entries under `pagination` (people search does NOT).
    assert out["count"] == 1 and out["total"] == 1184
    company = out["companies"][0]
    assert company["domain"] == "asiabizreview.example"
    assert company["founded_year"] == 1994
    assert company["phone"] == "+8135550123"  # prefers sanitized_phone
    # Firmographics are NOT available on search — asserted absent so a future edit
    # can't quietly reintroduce always-null fields.
    assert "industry" not in company
    assert "employees" not in company
    assert "location" not in company


def test_company_search_preserves_the_buying_intent_fields(monkeypatch):
    """The intent payload is the ENTIRE reason this integration calls company search.

    `intent_strength` / `show_intent` are the third intent feed (LeadSift). Dropping
    them from the shaped result would make the feed structurally unable to report
    intent, while still charging a credit per call.
    """
    _set_key(monkeypatch)
    payload = {
        "organizations": [
            {
                "id": "org1",
                "name": "Acme",
                "primary_domain": "acme.com",
                "intent_strength": "high",
                "show_intent": True,
                "has_intent_signal_account": True,
            }
        ],
        "pagination": {"total_entries": 1},
    }
    _patch_client(monkeypatch, {}, payload)
    out = json.loads(asyncio.run(server.apollo_company_search({"q_organization_name": "Acme"})))
    company = out["companies"][0]
    assert company["intent_strength"] == "high"
    assert company["show_intent"] is True
    assert company["has_intent_signal_account"] is True


def test_company_enrich_keeps_firmographics_search_does_not_return(monkeypatch):
    """The enrichment shaper is the one that DOES surface industry/employees/location."""
    _set_key(monkeypatch)
    payload = {
        "organization": {
            "id": "5e66b6381e05b4008c8331b8",
            "name": "Apollo.io",
            "primary_domain": "apollo.io",
            "industry": "information technology & services",
            "estimated_num_employees": 1600,
            "city": "San Francisco",
            "state": "California",
            "country": "United States",
            "founded_year": 2015,
        }
    }
    _patch_client(monkeypatch, {}, payload)
    out = json.loads(asyncio.run(server.apollo_company_enrich(domain="apollo.io")))
    assert out["industry"] == "information technology & services"
    assert out["employees"] == 1600
    assert out["location"] == "San Francisco, California, United States"


def test_company_search_meters_one_credit_per_call_even_on_zero_results(monkeypatch):
    """Apollo bills company search per page/call, not per match — confirm a
    zero-result search still spends 1 credit (unlike person enrichment, which is
    free on a miss)."""
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    _patch_client(monkeypatch, {}, {"organizations": [], "pagination": {"total_entries": 0}})
    asyncio.run(server.apollo_company_search({"q_organization_name": "Nobody"}))
    assert metered == [(1, "company_search")]


def test_company_search_hard_stops_at_cap(monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.setattr(server, "_allowance_remaining", lambda: 0)

    def _boom(*a, **k):
        raise AssertionError("no HTTP call must be made once the allowance is exhausted")

    monkeypatch.setattr(server.httpx, "AsyncClient", _boom)
    out = asyncio.run(server.apollo_company_search({"q_organization_name": "Acme"}))
    assert out.startswith("[apollo-cap]")


# ── apollo_company_enrich ────────────────────────────────────────────────────────


def test_company_enrich_requires_identifier(monkeypatch):
    _set_key(monkeypatch)
    out = asyncio.run(server.apollo_company_enrich())
    assert out.startswith("[apollo-error]")


def test_company_enrich_request_and_meter(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    payload = {"organization": {"id": "org1", "name": "Acme", "primary_domain": "acme.com"}}
    _patch_client(monkeypatch, captured, payload)
    out = json.loads(asyncio.run(server.apollo_company_enrich(domain="acme.com")))
    assert captured["url"].endswith("/organizations/enrich")
    assert captured["params"]["domain"] == "acme.com"
    assert out["domain"] == "acme.com"
    assert metered == [(1, "company_enrich")]


def test_company_enrich_no_match_not_metered(monkeypatch):
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    _patch_client(monkeypatch, {}, {"organization": {}})
    out = asyncio.run(server.apollo_company_enrich(domain="nowhere.example"))
    assert out.startswith("[apollo-error]")
    assert metered == []


# ── apollo_job_postings ───────────────────────────────────────────────────────────


def test_job_postings_requires_organization_id(monkeypatch):
    _set_key(monkeypatch)
    out = asyncio.run(server.apollo_job_postings(""))
    assert out.startswith("[apollo-error]")


def test_job_postings_request_and_meter(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n, op: metered.append((n, op)))
    payload = {
        "organization_job_postings": [{"title": "ML Engineer", "posted_at": "2026-07-01"}],
        "pagination": {"page": 1, "per_page": 25},
    }
    _patch_client(monkeypatch, captured, payload)
    out = json.loads(asyncio.run(server.apollo_job_postings("org1")))
    assert captured["url"].endswith("/organizations/org1/job_postings")
    assert out["count"] == 1
    assert metered == [(1, "job_postings")]


# ── count-based monthly credit allowance guard ──────────────────────────────────
# Apollo credits are a monthly plan allocation (cost_usd 0 by design), so the
# dollar-based §R2 guard can never trip. The count guard reads the month's logged
# `units.credits` against APOLLO_MONTHLY_CREDIT_CAP and hard-stops BEFORE any HTTP.


def test_monthly_credit_cap_parsing(monkeypatch):
    monkeypatch.delenv("APOLLO_MONTHLY_CREDIT_CAP", raising=False)
    assert server._monthly_credit_cap() is None  # unset ⇒ uncapped
    monkeypatch.setenv("APOLLO_MONTHLY_CREDIT_CAP", "0")
    assert server._monthly_credit_cap() is None  # 0/negative ⇒ uncapped
    monkeypatch.setenv("APOLLO_MONTHLY_CREDIT_CAP", "not-a-number")
    assert server._monthly_credit_cap() is None  # unparseable ⇒ uncapped
    monkeypatch.setenv("APOLLO_MONTHLY_CREDIT_CAP", "500")
    assert server._monthly_credit_cap() == 500


def test_allowance_remaining_uncapped_when_env_unset(monkeypatch):
    monkeypatch.delenv("APOLLO_MONTHLY_CREDIT_CAP", raising=False)
    assert server._allowance_remaining() is None

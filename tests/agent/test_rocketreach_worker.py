"""Unit tests for the RocketReach worker MCP (``agent.mcp.rocketreach``) — the
credit-free search half added for the prospect skill's intent layer.

Network-free: ``httpx.AsyncClient`` is monkeypatched. These pin the search REST
contract (POST person/search + searchCompany, list-wrapped facet values, page-size
clamp), the shaped/compact result payloads (identity fields only — never contacts),
the ``[rocketreach-error] …`` degradation contract, and the invariant that searches
are NEVER metered (searches are credit-free; only resolving lookups spend the
finite unit).
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

import httpx  # noqa: E402

from agent.mcp.rocketreach import resilience, server  # noqa: E402

# ── fake httpx client ─────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.headers: dict[str, str] = {}  # real responses have these; the guard reads Retry-After

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

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self._captured.update(method="POST", url=url, json=json, headers=headers)
        return _FakeResp(self._payload, self._status)

    async def request(self, method, url, json=None, headers=None, params=None):  # noqa: A002
        """The `httpx.AsyncClient.request` form `resilience.request` calls through.

        This double previously implemented only `post`, so it stopped standing in for
        httpx the moment the worker routed its calls through the rate-limit guard.
        Delegating keeps the captured shape identical either way.
        """
        if str(method).upper() == "POST":
            return await self.post(url, json=json, headers=headers)
        self._captured.update(method="GET", url=url, params=params, headers=headers)
        return _FakeResp(self._payload, self._status)


@pytest.fixture(autouse=True)
def _reset_rocketreach_breaker():
    """The rate-limit breaker is process-shared; keep error-status tests from leaking
    an open circuit into later tests in this file."""
    resilience.reset()
    yield
    resilience.reset()


def _patch_client(monkeypatch, captured, payload, status=200) -> None:
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: _FakeClient(captured, payload, status)
    )


def _set_key(monkeypatch) -> None:
    monkeypatch.setenv("ROCKETREACH_API_KEY", "kk")


def _person_search(query, **kw) -> str:
    return asyncio.run(
        server._search(
            "person",
            "profiles",
            server._shape_search_person,
            query,
            kw.get("start", 1),
            kw.get("page_size", 10),
            kw.get("order_by"),
        )
    )


def _company_search(query, **kw) -> str:
    return asyncio.run(
        server._search(
            "company",
            "companies",
            server._shape_search_company,
            query,
            kw.get("start", 1),
            kw.get("page_size", 10),
        )
    )


# ── _normalize_query ──────────────────────────────────────────────────────────


def test_normalize_query_wraps_scalars_and_drops_blanks():
    q = server._normalize_query(
        {
            "current_title": "CISO",
            "news_signal": ["Funding::three_months", "  "],
            "keyword": "",
            "location": None,
            "company_size": 1000,
        }
    )
    assert q == {
        "current_title": ["CISO"],
        "news_signal": ["Funding::three_months"],
        "company_size": ["1000"],
    }


def test_normalize_query_empty_input():
    assert server._normalize_query({}) == {}
    assert server._normalize_query(None) == {}


# ── degradation contract ──────────────────────────────────────────────────────


def test_search_requires_key(monkeypatch):
    monkeypatch.delenv("ROCKETREACH_API_KEY", raising=False)
    out = _person_search({"current_title": "CISO"})
    assert out.startswith("[rocketreach-error]")
    assert "ROCKETREACH_API_KEY" in out


def test_search_rejects_empty_query(monkeypatch):
    _set_key(monkeypatch)
    out = _person_search({"keyword": "   "})
    assert out.startswith("[rocketreach-error]")
    assert "facet" in out


def test_search_http_error_degrades(monkeypatch):
    """A plain HTTP error still degrades to a one-line message. 500 rather than 429:
    429 is no longer a plain error, it is the rate-limit path covered below."""
    _set_key(monkeypatch)
    _patch_client(monkeypatch, {}, {}, status=500)
    out = _company_search({"intent": "AI Security"})
    assert out == "[rocketreach-error] HTTP 500"


def test_search_rate_limit_is_named_not_generic(monkeypatch):
    """429 must come back SAYING it was rate-limited. The old behaviour surfaced it as
    an anonymous 'HTTP 429' with no retry, which is how a run walked into the wall."""
    _set_key(monkeypatch)

    async def _no_sleep(_seconds):  # the guard backs off; don't spend it in the suite
        return None

    monkeypatch.setattr(resilience.asyncio, "sleep", _no_sleep)
    _patch_client(monkeypatch, {}, {}, status=429)
    out = _company_search({"intent": "AI Security"})
    assert "rate-limited" in out, out
    assert "do not retry in a loop" in out


# ── person search contract ────────────────────────────────────────────────────


def test_person_search_request_and_shape(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    payload = {
        "profiles": [
            {
                "id": 7,
                "name": "Jane Doe",
                "current_title": "CISO",
                "current_employer": "Acme",
                "city": "Singapore",
                "region": None,
                "linkedin_url": "https://linkedin.com/in/jane",
                "emails": [{"email": "leak@acme.com"}],  # must NOT survive shaping
                "teaser": {"phones": ["+65"]},
            }
        ],
        "pagination": {"start": 1, "next": 2, "total": 57},
    }
    _patch_client(monkeypatch, captured, payload)

    out = json.loads(
        _person_search(
            {
                "current_title": "CISO",
                "job_change_signal": "Company Change::three_months",
            }
        )
    )

    assert captured["url"].endswith("/person/search")
    assert captured["json"]["query"] == {
        "current_title": ["CISO"],
        "job_change_signal": ["Company Change::three_months"],
    }
    assert captured["json"]["start"] == 1 and captured["json"]["page_size"] == 10
    assert captured["headers"]["Api-Key"] == "kk"
    assert out["count"] == 1 and out["total"] == 57 and out["next_start"] == 2
    assert out["profiles"] == [
        {
            "id": 7,
            "name": "Jane Doe",
            "current_title": "CISO",
            "current_employer": "Acme",
            "location": "Singapore",
            "linkedin_url": "https://linkedin.com/in/jane",
        }
    ]


def test_person_search_page_size_clamped(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"profiles": [], "pagination": {}})
    _person_search({"current_title": "CISO"}, page_size=999, start=0)
    assert captured["json"]["page_size"] == server._SEARCH_MAX_PAGE_SIZE
    assert captured["json"]["start"] == 1


# ── company search contract ───────────────────────────────────────────────────


def test_company_search_request_and_shape(monkeypatch):
    _set_key(monkeypatch)
    captured: dict = {}
    payload = {
        "companies": [
            {
                "id": 42,
                "name": "Acme",
                "domain": "acme.com",
                "primary_industry": "Software",
                "num_employees": 5000,
                "city": "Austin",
                "state": "TX",
                "country": "US",
                "linkedin_url": "https://linkedin.com/company/acme",
                "revenue": "should-be-dropped",
            }
        ],
        "pagination": {"start": 1, "next": None, "total": 1},
    }
    _patch_client(monkeypatch, captured, payload)

    out = json.loads(
        _company_search({"news_signal": ["Executive Hire::three_months"], "employees": "1000+"})
    )

    assert captured["url"].endswith("/searchCompany")
    assert captured["json"]["query"] == {
        "news_signal": ["Executive Hire::three_months"],
        "employees": ["1000+"],
    }
    assert out["count"] == 1 and out["total"] == 1
    assert out["companies"] == [
        {
            "id": 42,
            "name": "Acme",
            "domain": "acme.com",
            "industry": "Software",
            "employees": 5000,
            "location": "Austin, TX, US",
            "linkedin_url": "https://linkedin.com/company/acme",
        }
    ]


def test_company_search_tolerates_bare_list_body(monkeypatch):
    _set_key(monkeypatch)
    _patch_client(monkeypatch, {}, [{"id": 1, "name": "Acme"}])
    out = json.loads(_company_search({"domain": "acme.com"}))
    assert out["count"] == 1
    assert out["companies"][0]["name"] == "Acme"
    assert out["total"] is None


# ── credit-free invariant ─────────────────────────────────────────────────────


def test_search_never_meters(monkeypatch):
    """Searches are credit-free — the worker must never write a cost record for one."""
    _set_key(monkeypatch)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n: metered.append(n))
    _patch_client(monkeypatch, {}, {"profiles": [{"id": 1}], "pagination": {"total": 1}})
    _person_search({"company_intent": "AI Security"})
    assert metered == []


# ── Universal search endpoint indirection (design-for-it, off by default) ──────


def test_search_url_defaults_to_classic():
    """No env override -> today's exact classic v2 endpoints, unchanged."""
    assert server._SEARCH_API_MODE == "classic"
    assert server._search_url("person") == "https://api.rocketreach.co/api/v2/person/search"
    assert server._search_url("company") == "https://api.rocketreach.co/api/v2/searchCompany"


def test_search_url_switches_on_universal_mode(monkeypatch):
    monkeypatch.setattr(server, "_SEARCH_API_MODE", "universal")
    assert server._search_url("person") == "https://api.rocketreach.co/v1/search/person"
    assert server._search_url("company") == "https://api.rocketreach.co/v1/search/company"


def test_order_by_dropped_in_classic_mode(monkeypatch):
    """order_by is Universal-only — classic mode must not send it, so the classic-mode
    payload shape stays byte-identical to before this flag existed."""
    _set_key(monkeypatch)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"profiles": [], "pagination": {}})
    _person_search({"current_title": "CISO"}, order_by="score")
    assert "order_by" not in captured["json"]


def test_order_by_included_in_universal_mode(monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.setattr(server, "_SEARCH_API_MODE", "universal")
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"profiles": [], "pagination": {}})
    _person_search({"current_title": "CISO"}, order_by="score")
    assert captured["json"]["order_by"] == "score"
    assert captured["url"] == "https://api.rocketreach.co/v1/search/person"


# ── count-based lookup allowance guard (self-assessment §6.14) ────────────────
# RocketReach is a flat subscription (cost_usd 0), so the dollar-based §R2 guard can never trip.
# The count guard reads the month's logged `units.lookups` against ROCKETREACH_MONTHLY_LOOKUP_CAP
# and hard-stops BEFORE any HTTP once the allowance is spent.


def test_monthly_lookup_cap_parsing(monkeypatch):
    monkeypatch.delenv("ROCKETREACH_MONTHLY_LOOKUP_CAP", raising=False)
    assert server._monthly_lookup_cap() is None  # unset ⇒ uncapped
    monkeypatch.setenv("ROCKETREACH_MONTHLY_LOOKUP_CAP", "0")
    assert server._monthly_lookup_cap() is None  # 0/negative ⇒ uncapped (no accidental hard-zero)
    monkeypatch.setenv("ROCKETREACH_MONTHLY_LOOKUP_CAP", "not-a-number")
    assert server._monthly_lookup_cap() is None  # unparseable ⇒ uncapped
    monkeypatch.setenv("ROCKETREACH_MONTHLY_LOOKUP_CAP", "250")
    assert server._monthly_lookup_cap() == 250


def test_allowance_remaining_uncapped_when_env_unset(monkeypatch):
    monkeypatch.delenv("ROCKETREACH_MONTHLY_LOOKUP_CAP", raising=False)
    assert server._allowance_remaining() is None


def test_lookup_hard_stops_at_cap_and_makes_no_http(monkeypatch):
    _set_key(monkeypatch)
    # Pretend the allowance is spent; the HTTP client would raise if touched.
    monkeypatch.setattr(server, "_allowance_remaining", lambda: 0)

    def _boom(*a, **k):
        raise AssertionError("no HTTP call must be made once the allowance is exhausted")

    monkeypatch.setattr(server.httpx, "AsyncClient", _boom)
    out = asyncio.run(server._lookup({"name": "Jane", "current_employer": "Acme"}))
    assert out.startswith("[rocketreach-cap]")


def test_lookup_proceeds_under_cap(monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.setattr(server, "_allowance_remaining", lambda: 5)

    async def _fake_lookup_one(client, key, query):
        return {"name": "Jane", "emails": [{"email": "j@acme.com"}], "phones": []}

    monkeypatch.setattr(server, "_lookup_one", _fake_lookup_one)
    metered: list = []
    monkeypatch.setattr(server, "_meter", lambda n: metered.append(n))
    _patch_client(monkeypatch, {}, {})  # client is entered but _lookup_one is faked
    out = asyncio.run(server._lookup({"name": "Jane", "current_employer": "Acme"}))
    assert not out.startswith("[rocketreach-cap]")
    assert metered == [1]  # a resolved contact was metered


def test_bulk_rejects_batch_over_remaining_allowance(monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.setattr(server, "_allowance_remaining", lambda: 2)
    people = [{"name": f"P{i}", "current_employer": "Acme"} for i in range(5)]
    out = asyncio.run(server._bulk(people))
    assert out.startswith("[rocketreach-cap]")
    assert "resubmit ≤2" in out


def test_bulk_uncapped_when_allowance_none(monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.setattr(server, "_allowance_remaining", lambda: None)

    async def _fake_lookup_one(client, key, query):
        return {"name": query.get("name"), "emails": [{"email": "x@y.com"}], "phones": []}

    monkeypatch.setattr(server, "_lookup_one", _fake_lookup_one)
    monkeypatch.setattr(server, "_meter", lambda n: None)
    _patch_client(monkeypatch, {}, {})
    people = [{"name": f"P{i}", "current_employer": "Acme"} for i in range(3)]
    out = json.loads(asyncio.run(server._bulk(people)))
    assert out["resolved"] == 3

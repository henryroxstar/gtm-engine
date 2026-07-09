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

from agent.mcp.rocketreach import server  # noqa: E402

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

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self._captured.update(method="POST", url=url, json=json, headers=headers)
        return _FakeResp(self._payload, self._status)


def _patch_client(monkeypatch, captured, payload, status=200) -> None:
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: _FakeClient(captured, payload, status)
    )


def _set_key(monkeypatch) -> None:
    monkeypatch.setenv("ROCKETREACH_API_KEY", "kk")


def _person_search(query, **kw) -> str:
    return asyncio.run(
        server._search(
            "/person/search",
            "profiles",
            server._shape_search_person,
            query,
            kw.get("start", 1),
            kw.get("page_size", 10),
        )
    )


def _company_search(query, **kw) -> str:
    return asyncio.run(
        server._search(
            "/searchCompany",
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
    _set_key(monkeypatch)
    _patch_client(monkeypatch, {}, {}, status=429)
    out = _company_search({"intent": "AI Security"})
    assert out == "[rocketreach-error] HTTP 429"


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

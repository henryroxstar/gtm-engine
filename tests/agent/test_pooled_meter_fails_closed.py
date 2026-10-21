"""MM5: a pooled worker whose meter write fails must stop spending, not carry on unmetered.

A worker on the platform's key (``<PROVIDER>_KEY_SOURCE=house``) spends the operator's money, and the cost
record it appends is the only thing that bills the wallet and counts against the workspace's share. The
old ``_meter`` swallowed any failure — right for a worker whose spend is free to the wallet, wrong here: a
ledger that cannot be written would let every further lookup go through uncounted.

So on a pooled worker a failed write is recorded (``denials.jsonl``) and every further METERED call is
refused before any HTTP request. The legacy postures keep the swallow: the single-tenant VPS (key source
unset) and a tenant on their own vendor account (``byok``), where a metering hiccup must not take a lookup away.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

from agent.mcp.apollo import server as apollo_server  # noqa: E402
from agent.mcp.rocketreach import resilience  # noqa: E402
from agent.mcp.rocketreach import server as rr_server
from gtm_core import provider_registry  # noqa: E402

WORKERS = {
    "rocketreach": {
        "server": rr_server,
        "key_env": "ROCKETREACH_API_KEY",
        "source_env": "ROCKETREACH_KEY_SOURCE",
        "cap_env": "ROCKETREACH_MONTHLY_LOOKUP_CAP",
        "meter": lambda s: s._meter(1),
        "call": lambda s: s.rocketreach_lookup(name="Jane Roe", current_employer="Example Co"),
    },
    "apollo": {
        "server": apollo_server,
        "key_env": "APOLLO_API_KEY",
        "source_env": "APOLLO_KEY_SOURCE",
        "cap_env": "APOLLO_MONTHLY_CREDIT_CAP",
        "meter": lambda s: s._meter(1, "people_match"),
        "call": lambda s: s.apollo_person_enrich(
            first_name="Jane", last_name="Roe", organization_name="Example Co"
        ),
    },
}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    provider_registry.reset_metering_failures()
    resilience.reset()
    root = tmp_path / "content"
    (root / "acme").mkdir(parents=True)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    monkeypatch.setenv("GTM_PROFILE", "acme")
    yield
    provider_registry.reset_metering_failures()


@pytest.fixture(params=sorted(WORKERS))
def worker(request, monkeypatch):
    w = WORKERS[request.param]
    monkeypatch.setenv(w["key_env"], "kk")
    monkeypatch.setenv(w["cap_env"], "50")
    return request.param, w


def _break_the_ledger(monkeypatch):
    from agent.ledgers import Ledgers

    def _boom(self, record):  # noqa: ARG001
        raise OSError("disk full")

    monkeypatch.setattr(Ledgers, "append_cost", _boom)


def _http_counter(monkeypatch, server) -> list[str]:
    calls: list[str] = []

    def _client(*_a, **_k):
        calls.append("http")
        raise AssertionError("no HTTP request may follow a failed meter write on a pooled worker")

    monkeypatch.setattr(server.httpx, "AsyncClient", _client)
    return calls


def _denials(tmp_path) -> list[dict]:
    path = tmp_path / "content" / "acme" / "denials.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_pooled_worker_refuses_every_metered_call_after_a_meter_write_fails(
    worker, monkeypatch, tmp_path
):
    name, w = worker
    server = w["server"]
    monkeypatch.setenv(w["source_env"], "house")
    _break_the_ledger(monkeypatch)

    w["meter"](server)  # the paid call has happened; recording it fails

    calls = _http_counter(monkeypatch, server)
    out = asyncio.run(w["call"](server))
    assert out == f"[{name}-error] provider_metering_unavailable: {name}"
    assert calls == []
    reasons = [d["detail"] for d in _denials(tmp_path)]
    assert any(r.startswith("provider_metering_failed") for r in reasons), reasons
    assert any(r.startswith("provider_metering_unavailable") for r in reasons), reasons


def test_the_refusal_is_for_that_provider_only(worker, monkeypatch):
    name, w = worker
    other = next(n for n in WORKERS if n != name)
    monkeypatch.setenv(w["source_env"], "house")
    _break_the_ledger(monkeypatch)
    w["meter"](w["server"])
    assert provider_registry.metering_failed(name) is True
    assert provider_registry.metering_failed(other) is False


def test_a_pooled_worker_with_no_profile_cannot_record_so_it_stops_too(worker, monkeypatch):
    """Absence refuses: a house worker that has nowhere to write the record is not metered at all."""
    name, w = worker
    monkeypatch.setenv(w["source_env"], "house")
    monkeypatch.delenv("GTM_PROFILE")
    w["meter"](w["server"])
    assert provider_registry.metering_failed(name) is True


def test_a_pooled_worker_that_records_fine_keeps_working(worker, monkeypatch, tmp_path):
    """The control for the refusals above: the stop is caused by the failed write and nothing else."""
    name, w = worker
    monkeypatch.setenv(w["source_env"], "house")
    w["meter"](w["server"])
    assert provider_registry.metering_failed(name) is False
    ledger = tmp_path / "content" / "acme" / "costs.jsonl"
    assert len(ledger.read_text().splitlines()) == 1


def test_nothing_to_record_is_not_a_failure(worker, monkeypatch):
    name, w = worker
    monkeypatch.setenv(w["source_env"], "house")
    _break_the_ledger(monkeypatch)
    if name == "rocketreach":
        w["server"]._meter(0)
    else:
        w["server"]._meter(0, "people_match")
    assert provider_registry.metering_failed(name) is False


@pytest.mark.parametrize("source", [None, "byok"])
def test_the_vps_and_a_tenants_own_account_keep_the_legacy_swallow(worker, monkeypatch, source):
    """Neither is spending the pooled key, so neither is stopped by a failed write."""
    name, w = worker
    if source is None:
        monkeypatch.delenv(w["source_env"], raising=False)
    else:
        monkeypatch.setenv(w["source_env"], source)
    _break_the_ledger(monkeypatch)
    w["meter"](w["server"])
    assert provider_registry.metering_failed(name) is False


def test_a_mangled_key_source_lands_on_the_strict_side(worker, monkeypatch):
    name, w = worker
    monkeypatch.setenv(
        w["source_env"], "HOUSE "
    )  # not exactly "house": unreadable, so treated as pooled
    _break_the_ledger(monkeypatch)
    w["meter"](w["server"])
    assert provider_registry.metering_failed(name) is True


def test_credit_free_search_is_still_served_after_a_meter_failure(monkeypatch):
    """Searches spend nothing, so there is nothing to stop; the failure takes away paid calls only."""
    monkeypatch.setenv("ROCKETREACH_API_KEY", "kk")
    monkeypatch.setenv("ROCKETREACH_KEY_SOURCE", "house")
    _break_the_ledger(monkeypatch)
    rr_server._meter(1)
    assert provider_registry.metering_failed("rocketreach") is True

    class _Resp:
        status_code = 200
        headers: dict = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"profiles": []}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def request(self, *_a, **_k):
            return _Resp()

        async def post(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(rr_server.httpx, "AsyncClient", lambda *a, **k: _Client())
    out = json.loads(
        asyncio.run(
            rr_server._search(
                "person",
                "profiles",
                rr_server._shape_search_person,
                {"current_title": "CTO"},
                1,
                10,
                None,
            )
        )
    )
    assert out["count"] == 0

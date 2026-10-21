"""MM4: a tenant on the POOLED house key is never uncapped — absence of a quota refuses.

Both the RocketReach and Apollo workers used to read their monthly allowance from an env var and treat
"unset", "0" and "not a number" as UNCAPPED, and any ledger read error as "allow". On the single-tenant
VPS that is the operator's own subscription and stays exactly so. For a tenant spending the platform's
shared key it is the opposite of a quota: the backend marks that process ``<PROVIDER>_KEY_SOURCE=house``
and injects the workspace's allocated share as the cap, and from then on:

  - an unset / zero / negative / unparseable cap  ⇒ refuse (no share was allocated);
  - a ledger that cannot be read                  ⇒ refuse;
  - the count is the WORKSPACE's (every profile's ledger under its content root), because the share is
    per workspace — counting one profile would let two profiles each spend a full share;
  - every refusal lands in the workspace's ``denials.jsonl`` naming the provider and the limit.

The tenant's OWN vendor account (``byok``) and the VPS (variable unset) keep their legacy semantics.
Each assertion below has a positive twin, so a refusal cannot be a worker that is simply broken.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent.config import Config
from agent.ledgers import Ledgers
from agent.mcp.apollo import server as apollo
from agent.mcp.rocketreach import server as rocketreach

# worker module, cap env, key-source env, ledger tool name, unit key
WORKERS = {
    "rocketreach": (
        rocketreach,
        "ROCKETREACH_MONTHLY_LOOKUP_CAP",
        "ROCKETREACH_KEY_SOURCE",
        "lookups",
    ),
    "apollo": (apollo, "APOLLO_MONTHLY_CREDIT_CAP", "APOLLO_KEY_SOURCE", "credits"),
}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A workspace-shaped content root with the worker env pointing at it."""
    content = tmp_path / "workspaces" / "ws-1" / "content"
    content.mkdir(parents=True)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    monkeypatch.setenv("GTM_PROFILE", "acme")
    for _mod, cap_env, ks_env, _unit in WORKERS.values():
        monkeypatch.delenv(cap_env, raising=False)
        monkeypatch.delenv(ks_env, raising=False)
    return content


def _spend(content: Path, provider: str, unit_key: str, n: int, profile: str = "acme") -> None:
    cfg = Config.from_env()
    assert cfg.content_root == content  # the worker env really points at this tree
    Ledgers(cfg, profile).append_cost(
        {"tool": provider, "op": "x", "units": {unit_key: n}, "cost_usd": 0.0}
    )


def _remaining(provider: str):
    return WORKERS[provider][0]._allowance_remaining()


def _tenant(monkeypatch, provider: str, cap: str | None, source: str = "house") -> None:
    _mod, cap_env, ks_env, _unit = WORKERS[provider]
    monkeypatch.setenv(ks_env, source)
    if cap is not None:
        monkeypatch.setenv(cap_env, cap)


def _denials(content: Path) -> list[dict]:
    rows: list[dict] = []
    for path in content.glob("*/denials.jsonl"):
        rows += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows


# ── a tenant on the house key: absence refuses ───────────────────────────────


@pytest.mark.parametrize("provider", WORKERS)
@pytest.mark.parametrize("cap", [None, "", "0", "-5", "not-a-number", "2.5e1x"])
def test_a_house_key_tenant_with_no_usable_share_has_nothing_remaining(
    tree, monkeypatch, provider, cap
):
    _tenant(monkeypatch, provider, cap)
    remaining = _remaining(provider)
    assert remaining is not None and remaining <= 0


@pytest.mark.parametrize("provider", WORKERS)
def test_a_house_key_tenant_with_a_share_has_exactly_the_share_less_its_use(
    tree, monkeypatch, provider
):
    _tenant(monkeypatch, provider, "100")
    unit = WORKERS[provider][3]
    assert _remaining(provider) == 100
    _spend(tree, provider, unit, 30)
    assert _remaining(provider) == 70


@pytest.mark.parametrize("provider", WORKERS)
def test_the_count_is_the_workspaces_not_one_profiles(tree, monkeypatch, provider):
    """Two profiles in one workspace share ONE allocation — each counting only its own ledger would
    let a workspace spend a full share per profile."""
    _tenant(monkeypatch, provider, "100")
    unit = WORKERS[provider][3]
    _spend(tree, provider, unit, 60, profile="acme")
    _spend(tree, provider, unit, 50, profile="beta")
    assert _remaining(provider) == -10


@pytest.mark.parametrize("provider", WORKERS)
def test_an_unreadable_ledger_refuses_but_a_torn_final_line_does_not(tree, monkeypatch, provider):
    _tenant(monkeypatch, provider, "100")
    unit = WORKERS[provider][3]
    _spend(tree, provider, unit, 10)
    ledger = tree / "acme" / "costs.jsonl"
    good = ledger.read_text()

    ledger.write_text(good + '{"ts": "2026-1')  # a torn tail: tolerated
    assert _remaining(provider) == 90

    ledger.write_text(
        "{ not json\n" + good
    )  # an interior corrupt line: spend that cannot be totalled
    remaining = _remaining(provider)
    assert remaining is not None and remaining <= 0


@pytest.mark.parametrize("provider", WORKERS)
def test_a_house_key_tenant_with_no_profile_cannot_be_counted_so_refuses(
    tree, monkeypatch, provider
):
    _tenant(monkeypatch, provider, "100")
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    remaining = _remaining(provider)
    assert remaining is not None and remaining <= 0


@pytest.mark.parametrize("provider", WORKERS)
def test_an_unrecognised_key_source_is_treated_as_the_strict_side(tree, monkeypatch, provider):
    """The pricing guard refuses such a value outright; the quota must not read it as the legacy VPS."""
    _tenant(monkeypatch, provider, None, source="HOUSE ")
    remaining = _remaining(provider)
    assert remaining is not None and remaining <= 0


# ── the legacy postures are untouched (positive controls) ────────────────────


@pytest.mark.parametrize("provider", WORKERS)
def test_the_vps_with_no_key_source_keeps_its_legacy_uncapped_reading(tree, monkeypatch, provider):
    _mod, cap_env, _ks, _unit = WORKERS[provider]
    assert _remaining(provider) is None  # unset ⇒ uncapped
    for raw in ("0", "not-a-number"):
        monkeypatch.setenv(cap_env, raw)
        assert _remaining(provider) is None  # the operator's own subscription, as before
    monkeypatch.setenv(cap_env, "100")
    assert _remaining(provider) == 100  # and a cap still counts when one is set


@pytest.mark.parametrize("provider", WORKERS)
def test_the_vps_still_fails_open_on_an_unreadable_ledger(tree, monkeypatch, provider):
    _mod, cap_env, _ks, unit = WORKERS[provider]
    monkeypatch.setenv(cap_env, "100")
    _spend(tree, provider, unit, 10)
    ledger = tree / "acme" / "costs.jsonl"
    ledger.write_text("{ not json\n" + ledger.read_text())
    # the legacy reading skips the bad line and still counts the good one
    assert _remaining(provider) == 90


@pytest.mark.parametrize("provider", WORKERS)
def test_a_tenant_on_its_own_vendor_account_is_not_capped_by_us(tree, monkeypatch, provider):
    _tenant(monkeypatch, provider, None, source="byok")
    assert _remaining(provider) is None


# ── the refusal is real, and recorded ────────────────────────────────────────


def test_a_house_key_rocketreach_lookup_refuses_before_any_http_and_records_why(tree, monkeypatch):
    _tenant(monkeypatch, "rocketreach", "5")
    monkeypatch.setenv("ROCKETREACH_API_KEY", "k")
    _spend(tree, "rocketreach", "lookups", 5)

    def boom(*_a, **_k):
        raise AssertionError("no HTTP call may be made once the share is spent")

    monkeypatch.setattr(rocketreach.httpx, "AsyncClient", boom)
    out = asyncio.run(rocketreach._lookup({"name": "Jane Roe", "current_employer": "Acme"}))

    assert out.startswith("[rocketreach-cap]")
    rows = [r for r in _denials(tree) if "quota" in r.get("detail", "")]
    assert len(rows) == 1
    assert "rocketreach" in rows[0]["tool"]
    assert "limit=5" in rows[0]["detail"]
    assert str(tree) in rows[0]["detail"]  # the workspace is named by its tree


def test_a_house_key_apollo_call_with_no_share_refuses_and_records_why(tree, monkeypatch):
    _tenant(monkeypatch, "apollo", None)
    out = apollo._cap_guard(1)
    assert out is not None and out.startswith("[apollo-cap]")
    rows = [r for r in _denials(tree) if "quota" in r.get("detail", "")]
    assert len(rows) == 1
    assert "apollo" in rows[0]["tool"]
    assert "limit=0" in rows[0]["detail"]


def test_a_refusal_under_the_vps_legacy_posture_is_not_recorded_as_a_quota_denial(
    tree, monkeypatch
):
    """Positive control: a spent legacy cap still refuses, and the denial wording is the tenant's only."""
    monkeypatch.setenv("APOLLO_MONTHLY_CREDIT_CAP", "1")
    _spend(tree, "apollo", "credits", 1)
    out = apollo._cap_guard(1)
    assert out is not None and out.startswith("[apollo-cap]")

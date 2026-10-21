"""MM4 wiring: the share the backend allocates is the cap the worker is handed — for the SAME workspace.

Three links, each pinned here:

  1. the registry says how big a default share is per entitlement, and an entitlement it does not
     name gets nothing (absence refuses);
  2. ``resolve_provider_shares`` turns a workspace + its entitlement into allocated units, and a
     database that cannot answer is a share of ZERO, never a guess;
  3. ``tenant_run_config`` builds the run's worker config from the workspace's own content tree AND
     resolves the share for that same workspace id in one place — so the cap a worker enforces and the
     ledger it counts against can only ever be one workspace's. A worker carries no workspace id (its
     identity is its ledger path), which is exactly why this has to be one function and not two
     arguments a caller could pair wrongly.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest

from agent import mcp_config
from agent.config import Config
from backend import session as backend_session
from backend.services import provider_pool
from gtm_core import provider_registry as reg

WS_A = "11111111-1111-1111-1111-111111111111"
WS_B = "22222222-2222-2222-2222-222222222222"


# ── 1. the registry's default shares ─────────────────────────────────────────

_GOOD = """
[provider.rocketreach]
class = "data"
unit = "lookup"
usd_per_unit = 0.5
status = "estimate"
source = "test"
[provider.rocketreach.default_share]
free = 0
pro = 50
pro_plus = 200
"""


def _registry(tmp_path: Path, text: str = _GOOD) -> Path:
    path = tmp_path / "provider_costs.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_default_share_is_read_per_entitlement(tmp_path):
    path = _registry(tmp_path)
    assert reg.default_share("rocketreach", "pro", registry_path=path) == 50
    assert reg.default_share("rocketreach", "pro_plus", registry_path=path) == 200
    assert reg.default_share("rocketreach", "free", registry_path=path) == 0


@pytest.mark.parametrize("entitlement", [None, "", "enterprise", "PRO", "pro "])
def test_an_entitlement_the_registry_does_not_name_gets_nothing(tmp_path, entitlement):
    assert reg.default_share("rocketreach", entitlement, registry_path=_registry(tmp_path)) == 0


def test_a_provider_with_no_default_share_table_gets_nothing(tmp_path):
    text = _GOOD.split("[provider.rocketreach.default_share]")[0]
    assert reg.default_share("rocketreach", "pro", registry_path=_registry(tmp_path, text)) == 0


def test_an_absent_provider_gets_nothing(tmp_path):
    assert reg.default_share("apollo", "pro", registry_path=_registry(tmp_path)) == 0


@pytest.mark.parametrize(
    "bad",
    [
        "pro = -1",
        "pro = 1.5",
        'pro = "50"',
        "pro = true",
        "enterprise = 5",  # an entitlement the schema does not have
    ],
)
def test_a_malformed_default_share_fails_the_load(tmp_path, bad):
    text = _GOOD.replace("pro = 50", bad)
    with pytest.raises(reg.ProviderRegistryError):
        reg.load_provider_costs(_registry(tmp_path, text))


def test_the_shipped_registry_declares_a_share_for_every_pooled_provider():
    costs = reg.load_provider_costs()
    for provider in ("rocketreach", "apollo"):
        assert costs[provider].default_share, provider
        assert costs[provider].default_share["free"] == 0  # a free workspace never spends the pool


# ── 2. resolving a workspace's shares ────────────────────────────────────────


class _Conn:
    def __init__(self, entitlement, granted=None, fail=False):
        self.entitlement = entitlement
        self.granted = granted or {}
        self.fail = fail
        self.calls: list[tuple] = []

    async def fetchval(self, sql, *args):
        if self.fail:
            raise RuntimeError("database down")
        if "FROM subscriptions" in sql:
            return self.entitlement
        assert "allocate_provider_share" in sql, sql
        self.calls.append(args)
        return self.granted.get(args[1], args[2])  # unscripted: the pool grants what was asked


def _pool_for(monkeypatch, conn):
    class _Scope:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(provider_pool, "workspace_scope", lambda pool, ws: _Scope())
    return object()


def test_a_workspace_asks_for_its_entitlements_default_share_and_gets_what_was_granted(monkeypatch):
    conn = _Conn("pro", granted={"rocketreach": 50, "apollo": 20})
    out = asyncio.run(
        provider_pool.resolve_provider_shares(
            _pool_for(monkeypatch, conn), WS_A, ("rocketreach", "apollo")
        )
    )
    assert out == {"rocketreach": 50, "apollo": 20}
    asked = {c[1]: c[2] for c in conn.calls}
    assert asked == {
        "rocketreach": reg.default_share("rocketreach", "pro"),
        "apollo": reg.default_share("apollo", "pro"),
    }
    assert all(c[0] == WS_A for c in conn.calls)  # always the workspace in scope


def test_a_database_that_cannot_answer_is_a_share_of_zero(monkeypatch):
    conn = _Conn("pro", fail=True)
    out = asyncio.run(
        provider_pool.resolve_provider_shares(_pool_for(monkeypatch, conn), WS_A, ("rocketreach",))
    )
    assert out == {"rocketreach": 0}


def test_an_unrecognised_entitlement_asks_for_nothing_and_so_gets_nothing(monkeypatch):
    conn = _Conn("enterprise")
    out = asyncio.run(
        provider_pool.resolve_provider_shares(_pool_for(monkeypatch, conn), WS_A, ("rocketreach",))
    )
    assert out == {"rocketreach": 0}
    assert conn.calls and conn.calls[0][2] == 0  # it asked for zero, not for a guess


# ── 3. one workspace, one tree, one share ────────────────────────────────────


@pytest.fixture
def base_cfg(tmp_path, monkeypatch):
    """The platform's config with its own provider keys; the pooling switches are ON (MM5), so a tenant
    with no key of its own gets the house key, marked house, through the REAL config builder."""
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("GTM_POOL_ROCKETREACH", "true")
    monkeypatch.setenv("GTM_POOL_APOLLO", "true")
    cfg = Config.from_env(repo_root=tmp_path)
    return dataclasses.replace(
        cfg, rocketreach_api_key="platform-key", apollo_api_key="platform-key"
    )


def _worker_env(cfg, provider):
    return mcp_config.build_mcp_servers(cfg, "acme")[provider]["env"]


def test_the_share_and_the_ledger_tree_come_from_the_same_workspace(
    base_cfg, tmp_path, monkeypatch
):
    shares_by_ws = {WS_A: {"rocketreach": 60, "apollo": 7}, WS_B: {"rocketreach": 40, "apollo": 9}}
    resolved_for: list[str] = []

    async def fake_resolve(pool, workspace_id, providers):
        resolved_for.append(workspace_id)
        return {p: shares_by_ws[workspace_id][p] for p in providers}

    monkeypatch.setattr(provider_pool, "resolve_provider_shares", fake_resolve)

    for ws, other in ((WS_A, WS_B), (WS_B, WS_A)):
        cfg = asyncio.run(
            backend_session.tenant_run_config(object(), base_cfg, ws, tmp_path, credentials={})
        )
        rr = _worker_env(cfg, "rocketreach")
        assert ws in rr["GTM_CONTENT_ROOT"] and other not in rr["GTM_CONTENT_ROOT"]
        assert rr["ROCKETREACH_MONTHLY_LOOKUP_CAP"] == str(shares_by_ws[ws]["rocketreach"])
        assert _worker_env(cfg, "apollo")["APOLLO_MONTHLY_CREDIT_CAP"] == str(
            shares_by_ws[ws]["apollo"]
        )
    assert resolved_for == [WS_A, WS_B]  # each run asked for ITS workspace's share, and no other


def test_a_tenant_on_its_own_key_resolves_nothing_and_carries_no_cap(
    base_cfg, tmp_path, monkeypatch
):
    async def never(*_a, **_k):
        raise AssertionError("a BYOK-only tenant must not touch the pool")

    monkeypatch.setattr(provider_pool, "resolve_provider_shares", never)
    cfg = asyncio.run(
        backend_session.tenant_run_config(
            object(),
            base_cfg,
            WS_A,
            tmp_path,
            credentials={"rocketreach": "their-own-key", "apollo": "their-own-apollo-key"},
        )
    )
    env = _worker_env(cfg, "rocketreach")
    assert env["ROCKETREACH_KEY_SOURCE"] == "byok"
    assert "ROCKETREACH_MONTHLY_LOOKUP_CAP" not in env


def test_a_house_provider_with_no_share_carries_no_cap_so_its_worker_refuses(
    base_cfg, tmp_path, monkeypatch
):
    async def none_granted(pool, workspace_id, providers):
        return dict.fromkeys(providers, 0)

    monkeypatch.setattr(provider_pool, "resolve_provider_shares", none_granted)
    cfg = asyncio.run(
        backend_session.tenant_run_config(object(), base_cfg, WS_A, tmp_path, credentials={})
    )
    for provider, source_env, cap_env in (
        ("rocketreach", "ROCKETREACH_KEY_SOURCE", "ROCKETREACH_MONTHLY_LOOKUP_CAP"),
        ("apollo", "APOLLO_KEY_SOURCE", "APOLLO_MONTHLY_CREDIT_CAP"),
    ):
        env = _worker_env(cfg, provider)
        assert env[source_env] == "house"
        # a zero share is not "uncapped": the cap env is simply absent, which a house worker reads as zero
        assert cap_env not in env, provider


def test_the_vps_config_never_carries_a_cap(base_cfg):
    cfg = dataclasses.replace(base_cfg, rocketreach_api_key="k", apollo_api_key="k")
    assert "ROCKETREACH_MONTHLY_LOOKUP_CAP" not in _worker_env(cfg, "rocketreach")
    assert "APOLLO_MONTHLY_CREDIT_CAP" not in _worker_env(cfg, "apollo")


def test_a_platform_cap_never_rides_into_a_tenant_config(base_cfg, tmp_path):
    """The platform's own config may carry caps; a tenant's is always rebuilt from its own share."""
    platform = dataclasses.replace(base_cfg, rocketreach_monthly_cap=999, apollo_monthly_cap=999)
    scoped = backend_session._workspace_scoped_config(platform, WS_A, tmp_path, credentials={})
    assert scoped.rocketreach_monthly_cap is None and scoped.apollo_monthly_cap is None

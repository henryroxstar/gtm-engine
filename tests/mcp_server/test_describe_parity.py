"""Surface agreement tests for MCP describe() & dispatch() vs REST GET /v1/packs (T8, T9)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp import Context

from backend.callers.principal import Principal
from backend.callers.rest import require_principal
from backend.routers import packs as packs_router
from gtm_core.capabilities import Entitlement
from gtm_core.paths import workspace_profiles_root
from mcp_server.tasks import describe_task, dispatch_task

_WS_ID = "11111111-1111-1111-1111-111111111111"
_KEY_ID = "22222222-2222-2222-2222-222222222222"
_AGENT_ID = "33333333-3333-3333-3333-333333333333"

_PROFILE_MD = """# PROFILE
company: Acme Robotics
brand_name: Acme Robotics
language: en
"""

_KNOWLEDGE = {
    "voice": "# Voice\nDirect and technical.\n",
    "icp-personas": "# ICP\nEngineering leaders.\n",
    "angles": '[angles]\nangles = ["speed", "safety"]\n',
}


def _run(coro):
    return asyncio.run(coro)


def _make_http_ctx(auth_header: str = "Bearer sk-test") -> Context:
    ctx = MagicMock(spec=Context)
    req = MagicMock()
    req.headers = {"authorization": auth_header}
    ctx.request_context = MagicMock()
    ctx.request_context.request = req
    return ctx


class _Cfg:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root


def _provision_profile(
    profiles_root: Path,
    profile_name: str = "acme",
    *,
    packs_toml: str | None = None,
    with_profile_md: bool = True,
):
    pdir = profiles_root / profile_name
    (pdir / "knowledge").mkdir(parents=True, exist_ok=True)
    if with_profile_md:
        (pdir / "PROFILE.md").write_text(_PROFILE_MD, encoding="utf-8")
        for topic, content in _KNOWLEDGE.items():
            ext = ".toml" if topic == "angles" else ".md"
            (pdir / "knowledge" / f"{topic}{ext}").write_text(content, encoding="utf-8")
    if packs_toml is not None:
        (pdir / "packs.toml").write_text(packs_toml, encoding="utf-8")


def _make_pool_mock(configured_providers: set[str], profile_name: str = "acme"):
    conn = MagicMock()
    conn.execute = AsyncMock()

    async def _fetchrow(q, *args):
        if "FROM profiles" in q:
            return {"profile_name": profile_name}
        if "FROM agents" in q:
            return {
                "profile_name": profile_name,
                "packs": None,
                "read_scope": "own",
                "status": "active",
            }
        if "api_keys" in q:
            return {
                "key_id": _KEY_ID,
                "workspace_id": _WS_ID,
                "entitlement": "pro",
                "agent_id": None,
            }
        return None

    async def _fetch(q, *args):
        if "FROM encrypted_credentials" in q:
            return [{"provider": p} for p in configured_providers]
        return []

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.fetchval = AsyncMock(return_value="pro")
    conn.fetch = AsyncMock(side_effect=_fetch)

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def test_t8_mcp_describe_and_dispatch_byok_readiness_parity(tmp_path, monkeypatch):
    """T8: MCP describe() & dispatch() vs REST GET /v1/packs with BYOK credentials in encrypted_credentials."""
    repo_root = Path.cwd()
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    profiles_root = workspace_profiles_root(_WS_ID)
    packs_toml = 'active = ["prospecting"]\n'
    _provision_profile(profiles_root, "acme", packs_toml=packs_toml, with_profile_md=True)

    principal = Principal(
        kind="service",
        subject="key-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="api_key",
        agent_id=_AGENT_ID,
    )

    # Set VAULT_KEK so get_workspace_configured_providers works
    monkeypatch.setenv("VAULT_KEK", "00" * 32)

    # 1. When saleshandy is unconfigured in encrypted_credentials
    pool_blocked = _make_pool_mock(configured_providers=set())

    # Build FastAPI test client for REST GET /v1/packs
    app = FastAPI()
    app.include_router(packs_router.router, prefix="/v1")
    app.dependency_overrides[require_principal] = lambda: principal
    app.state.pool = pool_blocked
    app.state.cfg = _Cfg(repo_root)
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal",
        AsyncMock(return_value=principal),
    )

    # REST check: saleshandy is blocked
    client = TestClient(app)
    rest_resp = client.get("/v1/packs")
    assert rest_resp.status_code == 200
    rest_packs = rest_resp.json()
    assert len(rest_packs) >= 1
    prospect_pack_rest = next(p for p in rest_packs if p["pack"] == "prospecting")
    assert prospect_pack_rest["readiness"]["status"] == "blocked"
    saleshandy_items = [
        item for item in prospect_pack_rest["readiness"]["items"] if item["name"] == "saleshandy"
    ]
    assert len(saleshandy_items) == 1
    assert saleshandy_items[0]["kind"] == "integration"

    # MCP describe check: saleshandy is blocked
    ctx = _make_http_ctx()
    cfg = _Cfg(repo_root)
    mcp_desc = _run(describe_task(pack="prospecting", ctx=ctx, pool=pool_blocked, cfg=cfg))
    assert len(mcp_desc["variants"]) == 1
    prospect_pack_mcp = mcp_desc["variants"][0]
    assert prospect_pack_mcp["readiness"]["status"] == "blocked"
    mcp_sh_items = [
        item for item in prospect_pack_mcp["readiness"]["items"] if item["name"] == "saleshandy"
    ]
    assert len(mcp_sh_items) == 1

    # Exact parity on readiness block
    assert prospect_pack_mcp["readiness"] == prospect_pack_rest["readiness"]

    # MCP dispatch refuses with pack_not_ready
    with pytest.raises(ValueError, match="pack_not_ready"):
        _run(
            dispatch_task(
                pack="prospecting",
                variant="prospect-outreach",
                inputs={"market": "SaaS"},
                ctx=ctx,
                pool=pool_blocked,
                cfg=cfg,
            )
        )

    # 2. When saleshandy IS configured in encrypted_credentials
    pool_ready = _make_pool_mock(configured_providers={"saleshandy"})
    app.state.pool = pool_ready

    # REST check: saleshandy is omitted from blocked items
    rest_resp2 = client.get("/v1/packs")
    assert rest_resp2.status_code == 200
    rest_packs2 = rest_resp2.json()
    prospect_pack_rest2 = next(p for p in rest_packs2 if p["pack"] == "prospecting")
    assert not any(
        item["name"] == "saleshandy" for item in prospect_pack_rest2["readiness"].get("items", [])
    )

    # MCP describe check: saleshandy is omitted from blocked items
    mcp_desc2 = _run(describe_task(pack="prospecting", ctx=ctx, pool=pool_ready, cfg=cfg))
    prospect_pack_mcp2 = mcp_desc2["variants"][0]
    assert not any(
        item["name"] == "saleshandy" for item in prospect_pack_mcp2["readiness"].get("items", [])
    )

    # Exact parity on readiness block
    assert prospect_pack_mcp2["readiness"] == prospect_pack_rest2["readiness"]


def test_t9_mcp_describe_missing_profile_md_parity(tmp_path, monkeypatch):
    """T9: MCP describe() vs REST GET /v1/packs missing-PROFILE.md parity."""
    repo_root = Path.cwd()
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    profiles_root = workspace_profiles_root(_WS_ID)
    packs_toml = 'active = ["prospecting"]\n'
    # Provision packs.toml WITHOUT PROFILE.md
    _provision_profile(profiles_root, "acme", packs_toml=packs_toml, with_profile_md=False)

    principal = Principal(
        kind="user",
        subject="user-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="jwt",
    )

    pool = _make_pool_mock(configured_providers=set())

    app = FastAPI()
    app.include_router(packs_router.router, prefix="/v1")
    app.dependency_overrides[require_principal] = lambda: principal
    app.state.pool = pool
    app.state.cfg = _Cfg(repo_root)

    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal",
        AsyncMock(return_value=principal),
    )

    expected_readiness = {
        "status": "blocked",
        "items": [
            {
                "kind": "setting",
                "name": "profile",
                "status": "blocked",
                "reason": "profile files are not provisioned yet — run onboarding",
            }
        ],
    }

    # REST check
    client = TestClient(app)
    rest_resp = client.get("/v1/packs")
    assert rest_resp.status_code == 200
    rest_packs = rest_resp.json()
    assert len(rest_packs) >= 1
    assert rest_packs[0]["readiness"] == expected_readiness

    # MCP check
    ctx = _make_http_ctx()
    cfg = _Cfg(repo_root)
    mcp_desc = _run(describe_task(ctx=ctx, pool=pool, cfg=cfg))
    assert len(mcp_desc["packs"]) >= 1
    assert mcp_desc["packs"][0]["readiness"] == expected_readiness

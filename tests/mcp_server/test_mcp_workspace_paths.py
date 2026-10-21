"""Tests for MCP workspace volume wiring and workspace-first path resolution (T1, T2, T3)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gtm_core.capabilities import Entitlement
from gtm_core.paths import PathConfig
from mcp_server.auth import ApiKeyCtx

REPO = Path(__file__).resolve().parents[2]
WS_A = "aaaaaaaa-1111-2222-3333-444444444444"
WS_B = "bbbbbbbb-1111-2222-3333-444444444444"


def _run(coro):
    return asyncio.run(coro)


def test_t1_mcp_compose_mounts_workspaces_and_packs_and_dockerfile_copies_packs():
    """T1 (R1): deploy/docker-compose.yml mounts workspaces and packs on mcp, and Dockerfile.mcp copies packs."""
    compose = REPO / "deploy" / "docker-compose.yml"
    dockerfile_mcp = REPO / "deploy" / "Dockerfile.mcp"
    if not compose.exists():
        pytest.skip("deploy/ not present in public carve")

    compose_text = compose.read_text(encoding="utf-8")
    # Extract mcp service block up to cloudflared:
    assert "\n  mcp:\n" in compose_text
    mcp_block = compose_text.split("\n  mcp:\n", 1)[1].split("\n  cloudflared:\n", 1)[0]
    assert "workspaces:/app/data/workspaces:ro" in mcp_block
    assert "./packs:/app/packs:ro" in mcp_block
    assert "GTM_WORKSPACES_ROOT:" in mcp_block and "/app/data/workspaces" in mcp_block

    df_text = dockerfile_mcp.read_text(encoding="utf-8")
    assert "COPY packs/" in df_text


def test_t2_radar_check_and_profile_context_read_workspace_roots_first(tmp_path, monkeypatch):
    """T2 (R2, R3): radar_check, profile_context, and describe_task read from the caller's workspace tree."""
    from mcp_server.paths import resolve_mcp_roots
    from mcp_server.server import profile_context, radar_check

    ws_root = tmp_path / "workspaces"
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(ws_root))

    # Global fallback has DIFFERENT data for "acme" to prove workspace tree wins
    global_profiles = tmp_path / "global_profiles"
    global_content = tmp_path / "global_content"
    (global_profiles / "acme" / "knowledge").mkdir(parents=True)
    (global_profiles / "acme" / "knowledge" / "icp.md").write_text(
        "GLOBAL ICP SHOULD NOT BE READ", encoding="utf-8"
    )
    (global_content / "acme").mkdir(parents=True)
    (global_content / "acme" / "history.jsonl").write_text(
        json.dumps({"url": "https://global.example/post", "id": "global-1"}) + "\n",
        encoding="utf-8",
    )

    # Workspace A's own tree
    ws_a_prof = ws_root / WS_A / "profiles" / "acme" / "knowledge"
    ws_a_cont = ws_root / WS_A / "content" / "acme"
    ws_a_prof.mkdir(parents=True)
    ws_a_cont.mkdir(parents=True)
    (ws_a_prof / "icp.md").write_text("WORKSPACE A TENANT ICP", encoding="utf-8")
    (ws_a_cont / "history.jsonl").write_text(
        json.dumps({"url": "https://ws-a.example/post", "id": "ws-a-story-1"}) + "\n",
        encoding="utf-8",
    )

    paths = PathConfig(
        content_root=global_content,
        profiles_root=global_profiles,
        default_profile="acme",
    )
    prof_root, cont_root = resolve_mcp_roots(WS_A, "acme", paths, repo_root=tmp_path)
    assert prof_root == ws_root / WS_A / "profiles"
    assert cont_root == ws_root / WS_A / "content"

    fake_ctx = MagicMock()
    key_ctx = ApiKeyCtx(workspace_id=WS_A, entitlement=Entitlement.PRO, key_id="k1")
    with (
        patch("mcp_server.server._require_profile", AsyncMock(return_value=key_ctx)),
        patch("mcp_server.server.PathConfig.from_env", return_value=paths),
    ):
        icp_text = _run(profile_context(profile="acme", filename="icp.md", ctx=fake_ctx))
        assert icp_text == "WORKSPACE A TENANT ICP"

        seen_ws = _run(
            radar_check(
                url="https://ws-a.example/post", title="Story", profile="acme", ctx=fake_ctx
            )
        )
        assert seen_ws == {"seen": True, "story_id": "ws-a-story-1"}

        seen_global = _run(
            radar_check(
                url="https://global.example/post", title="Story", profile="acme", ctx=fake_ctx
            )
        )
        assert seen_global == {"seen": False, "story_id": None}


def test_t3_fallback_and_cross_tenant_isolation(tmp_path, monkeypatch):
    """T3 (R2, R3):
    (a) Unprovisioned workspace (single-tenant / legacy mode) falls back to global root if global profile exists.
    (b) Provisioned multi-tenant workspace NEVER leaks another workspace's profile or global content for an unprovisioned profile slug.
    """
    from mcp_server.paths import resolve_mcp_roots
    from mcp_server.server import profile_context, radar_check

    ws_root = tmp_path / "workspaces"
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(ws_root))

    global_profiles = tmp_path / "global_profiles"
    global_content = tmp_path / "global_content"
    (global_profiles / "acme" / "knowledge").mkdir(parents=True)
    (global_profiles / "acme" / "knowledge" / "icp.md").write_text(
        "LEGACY GLOBAL ICP", encoding="utf-8"
    )
    (global_profiles / "globex" / "knowledge").mkdir(parents=True)
    (global_profiles / "globex" / "knowledge" / "icp.md").write_text(
        "OPERATOR GLOBAL GLOBEX", encoding="utf-8"
    )
    (global_content / "globex").mkdir(parents=True)
    (global_content / "globex" / "history.jsonl").write_text(
        json.dumps({"url": "https://globex.example/secret", "id": "secret-1"}) + "\n",
        encoding="utf-8",
    )

    paths = PathConfig(
        content_root=global_content,
        profiles_root=global_profiles,
        default_profile="acme",
    )

    # (a) WS_B has no directory under workspaces/ at all -> legacy fallback to global_profiles/acme
    prof_b, cont_b = resolve_mcp_roots(WS_B, "acme", paths, repo_root=tmp_path)
    assert prof_b == global_profiles
    assert cont_b == global_content

    # (b) WS_A IS a provisioned multi-tenant workspace (has profiles/acme on disk).
    # Querying "globex" (which exists in global_profiles, NOT in WS_A) must NOT fall back to global_content!
    (ws_root / WS_A / "profiles" / "acme" / "knowledge").mkdir(parents=True)
    (ws_root / WS_A / "content" / "acme").mkdir(parents=True)

    prof_a_globex, cont_a_globex = resolve_mcp_roots(WS_A, "globex", paths, repo_root=tmp_path)
    assert prof_a_globex == ws_root / WS_A / "profiles"
    assert cont_a_globex == ws_root / WS_A / "content"

    fake_ctx = MagicMock()
    key_ctx = ApiKeyCtx(workspace_id=WS_A, entitlement=Entitlement.PRO, key_id="k1")
    with (
        patch("mcp_server.server._require_profile", AsyncMock(return_value=key_ctx)),
        patch("mcp_server.server.PathConfig.from_env", return_value=paths),
    ):
        assert _run(profile_context(profile="globex", filename="icp.md", ctx=fake_ctx)) == ""
        assert _run(
            radar_check(
                url="https://globex.example/secret",
                title="Secret",
                profile="globex",
                ctx=fake_ctx,
            )
        ) == {"seen": False, "story_id": None}

"""MM6: Vibe Prospecting is not available to a backend tenant run.

Vibe is a hosted OAuth connector on ONE host identity for every caller (``plugin/.mcp.json``): pooled by
construction, with no way for us to say whose lookup it was. PRD §MM6 (decided 2026-10-14, option b): it is
off for backend tenants and stays on for the single-tenant VPS, where the operator is the tenant.

It reaches a run through the PLUGIN's ``.mcp.json``, not through ``mcp_servers`` — so leaving it out of
``build_mcp_servers`` removes nothing. Two layers, pinned here separately because either alone is the wrong
evidence:

* the SOURCE: a tenant run is started with ``strict_mcp_config``, so the CLI ignores every MCP server except
  the ones we hand it. Proven through the real bundled CLI (the init message lists what actually started),
  with the single-tenant run as the positive control — an empty list means nothing if the same probe also
  finds nothing on the VPS.
* the CALL: the tenant's ``can_use_tool`` refuses any Vibe tool by name, with a row in ``denials.jsonl``, for
  the case where the server arrives some other way (a user-level connector under an opaque server id).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("claude_agent_sdk")

from agent.config import Config  # noqa: E402
from agent.path_confinement import NOT_FOR_TENANTS, Confinement  # noqa: E402
from agent.permissions import classify_tool, make_headless_can_use_tool  # noqa: E402
from agent.session import build_agent_options  # noqa: E402
from tests.agent.test_tenant_transcripts import (  # noqa: E402
    WS,
    _bundled_cli,
    _confined_callback,
    _Messages,
    _tenant_cfg,
)

REPO = Path(__file__).resolve().parents[2]

#: The tool names a Vibe connector shows up under, by how it was registered.
VIBE_TOOLS = [
    "mcp__plugin_gtm-engine_Vibe_Prospecting__match-business",  # via the plugin's .mcp.json
    "mcp__Vibe_Prospecting__fetch-entities",  # a user-level connector named for the vendor
    "mcp__00000000-0000-4000-8000-000000000001__enrich-prospects",  # claude.ai: opaque server id
    # A tool the vendor adds next year: named for Vibe, leaf unknown to us — the server name catches it.
    "mcp__plugin_gtm-engine_Vibe_Prospecting__a-tool-added-later",
]


def _vibe_names(servers) -> list[str]:
    return [s["name"] for s in (servers or []) if "vibe" in s["name"].lower()]


# ── the plugin really does ship Vibe (so "absent" below means something) ──────────────────────────


def test_the_plugin_really_declares_vibe():
    declared = json.loads((REPO / "plugin" / ".mcp.json").read_text())["mcpServers"]
    assert any("vibe" in name.lower() for name in declared)


# ── the source: options ─────────────────────────────────────────────────────────────────────────


def test_a_tenant_run_is_started_with_strict_mcp_config(tmp_path, monkeypatch):
    options = build_agent_options(
        _tenant_cfg(tmp_path, monkeypatch), "acme", can_use_tool=_confined_callback()
    )
    assert options.strict_mcp_config is True
    assert options.plugins  # the skills still load; only the plugin's MCP servers are dropped


def test_the_single_tenant_run_keeps_the_plugins_mcp_servers(tmp_path):
    """Positive control, options level: the VPS config is NOT strict, so Vibe still resolves there."""
    cfg = dataclasses.replace(Config.from_env(repo_root=REPO), content_root=tmp_path / "content")
    options = build_agent_options(cfg, "acme")
    assert options.strict_mcp_config is False
    assert options.plugins


# ── the source: through the real CLI ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def stub_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Messages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


async def _init(options, port: int) -> dict:
    from claude_agent_sdk import ClaudeSDKClient, SystemMessage

    options.env = {
        **(options.env or {}),
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_API_KEY": "sk-stub",
        "CLAUDE_CONFIG_DIR": tempfile.mkdtemp(prefix="cfg_"),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }
    options.cwd = tempfile.mkdtemp(prefix="cwd_")
    options.max_budget_usd = None
    seen: dict = {}
    async with ClaudeSDKClient(options=options) as client:
        await client.query("say pong")
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                seen = message.data
    return seen


@pytest.mark.skipif(_bundled_cli() is None, reason="the SDK's bundled CLI is not installed")
def test_the_cli_starts_no_vibe_for_a_tenant_run(tmp_path, monkeypatch, stub_api):
    options = build_agent_options(
        _tenant_cfg(tmp_path, monkeypatch), "acme", can_use_tool=_confined_callback()
    )
    init = asyncio.run(asyncio.wait_for(_init(options, stub_api), 120))
    servers = init["mcp_servers"]
    assert _vibe_names(servers) == []
    assert not [
        s for s in servers if s["name"].startswith("plugin:")
    ]  # none of the plugin's servers
    # What strict mode must NOT take with it: the plugin's skills still load.
    # (a newer CLI may also list plugins of its own, so assert ours is present rather than alone)
    assert "gtm-engine" in [p["name"] for p in init["plugins"]]
    assert init["skills"], "a tenant run lost its skills"


@pytest.mark.skipif(_bundled_cli() is None, reason="the SDK's bundled CLI is not installed")
def test_the_same_probe_does_find_vibe_on_the_single_tenant_path(tmp_path, stub_api):
    """The positive control. Without it, the empty list above is just a CLI that lists nothing."""
    cfg = dataclasses.replace(Config.from_env(repo_root=REPO), content_root=tmp_path / "content")
    options = build_agent_options(cfg, "acme")
    servers = asyncio.run(asyncio.wait_for(_init(options, stub_api), 120))["mcp_servers"]
    assert _vibe_names(servers), servers


# ── the call: can_use_tool ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", VIBE_TOOLS)
def test_a_vibe_call_is_refused_for_a_tenant_and_still_allowed_unconfined(tool):
    assert classify_tool(tool, {}) == "allow"  # the VPS / cockpit decision, unchanged
    assert classify_tool(tool, {}, confine=Confinement.for_workspace(WS, REPO)) == "deny"


@pytest.mark.parametrize("tool", VIBE_TOOLS)
def test_the_refusal_is_a_denials_row_naming_the_tool_and_the_workspace(tool, tmp_path):
    from agent.denial_log import make_denial_sink

    cfg = SimpleNamespace(content_root=tmp_path / "content")
    (tmp_path / "content" / "acme").mkdir(parents=True)
    callback = make_headless_can_use_tool(
        on_deny=make_denial_sink(cfg, "acme", "backend"),
        confine=Confinement.for_workspace(WS, REPO),
    )
    result = asyncio.run(callback(tool, {"query": "x"}, None))
    assert type(result).__name__ == "PermissionResultDeny"
    rows = [
        json.loads(line)
        for line in (tmp_path / "content" / "acme" / "denials.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["tool"] == tool and rows[0]["decision"] == "deny"
    assert NOT_FOR_TENANTS in rows[0]["detail"] and WS in rows[0]["detail"]


def test_the_tools_a_tenant_does_use_are_not_caught_by_the_rule():
    confine = Confinement.for_workspace(WS, REPO)
    for tool in (
        "mcp__rocketreach__rocketreach_lookup",
        "mcp__apollo__apollo_person_enrich",
        "mcp__firecrawl__firecrawl_scrape",
        "mcp__judge__score_emails",
    ):
        assert classify_tool(tool, {}, confine=confine) == "allow", tool


# ── beyond Vibe: a tenant may call only the servers it was started with ─────────────────────────────


#: Servers a run could be handed that are NOT ours: the plugin's own copies (strict mode drops them), a
#: project's ``.mcp.json``, a connector added by hand, and a tool name with no server at all.
FOREIGN = [
    "mcp__plugin_gtm-engine_firecrawl__firecrawl_scrape",
    "mcp__project_declared_server__anything",
    "mcp__openseo__keyword_research",
    "mcp__some_new_connector__list_things",
    "mcp__no_server_segment",
]


@pytest.mark.parametrize("tool", FOREIGN)
def test_a_server_that_is_not_one_of_ours_is_refused_for_a_tenant_and_not_unconfined(tool):
    assert classify_tool(tool, {}) == "allow"  # the VPS / cockpit decision, unchanged
    assert classify_tool(tool, {}, confine=Confinement.for_workspace(WS, REPO)) == "deny"


def test_every_server_the_brain_can_be_handed_is_one_a_tenant_may_call():
    """The other half of the allowlist: nothing we ourselves start may be refused by it."""
    from agent.mcp_config import build_mcp_servers
    from agent.path_confinement import refusal

    cfg = dataclasses.replace(
        Config.from_env(repo_root=REPO),
        news_db_dsn="postgres://ro@db/news",
        deepseek_api_key="k",
        anthropic_api_key="k",
        google_oauth={"client_id": "a", "client_secret": "b", "refresh_token": "c"},
        gemini_api_key="k",
        higgsfield_api_key="k",
        higgsfield_api_secret="k",
        deck_renderer_url="http://deck:3000",
        firecrawl_api_key="k",
        rocketreach_api_key="k",
        apollo_api_key="k",
        saleshandy_api_key="k",
        syften_api_key="k",
        elevenlabs_api_key="k",
    )
    servers = build_mcp_servers(cfg, "acme")
    assert len(servers) >= 12
    confine = Confinement.for_workspace(WS, REPO)
    for name in servers:
        assert refusal(f"mcp__{name}__some_tool", {}, confine) is None, name

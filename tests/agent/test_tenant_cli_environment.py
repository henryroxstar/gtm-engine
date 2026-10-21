"""What the CLI a TENANT run is started with can see: no platform secret in its environment, and no MCP
server it was not handed.

Two ways the platform's own secrets and servers could reach a tenant's run without going through the config
object the rest of MM5/MM6 is about, both found by review of Phase 3 and proven here through the REAL bundled
CLI (a stand-in Messages API, a throwaway project directory):

* the ENVIRONMENT. The SDK starts the CLI with ``os.environ`` plus the run's overrides, so every secret the
  backend container holds (the house provider keys, the billing secret, the JWT key, the vault key, database
  URLs) is in the environment of a process whose shell the tenant's run can use. Blanking the provider keys on
  the config object protected only the config object. A tenant run now blanks every secret-shaped variable it
  inherits, except the ones the CLI itself needs to talk to the model (``ANTHROPIC_*``, ``CLAUDE_*``).
* the PROJECT. Real runs start in the repo checkout with ``setting_sources=["project"]``, which loads the
  repo's own ``.mcp.json``; strict mode has to drop that too, not only the plugin's.

The probe for each is a pair: the tenant run shows nothing, the single-tenant run shows the thing. Without the
pair, "nothing" is just a CLI that shows nothing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")

from agent.config import Config  # noqa: E402
from agent.session import _tenant_env_scrub, build_agent_options  # noqa: E402
from tests.agent.test_tenant_transcripts import (  # noqa: E402
    _bundled_cli,
    _confined_callback,
    _Messages,
    _tenant_cfg,
)

REPO = Path(__file__).resolve().parents[2]
SECRET = "probe-secret-value"  # nosec B105 -- throwaway test value


# ── the scrub itself ────────────────────────────────────────────────────────────────────────────


def test_secret_shaped_names_are_blanked_and_the_clis_own_are_not():
    scrubbed = _tenant_env_scrub(
        {
            "ROCKETREACH_API_KEY": "a",
            "APOLLO_API_KEY": "a",
            "SALESHANDY_API_KEY": "a",
            "BILLING_SYNC_SECRET": "a",
            "BACKEND_JWT_SECRET": "a",
            "VAULT_KEK": "a",
            "TELEGRAM_BOT_TOKEN": "a",
            "NEWS_DB_DSN": "a",
            "DATABASE_URL": "a",
            "REDIS_URL": "a",
            "GOOGLE_OAUTH_REFRESH_TOKEN": "a",
            "SOME_FUTURE_VENDOR_PASSWORD": "a",
            # a URL that carries a password is a secret whatever its name says
            "POSTGRES_MIGRATION_URL": "postgres://owner:s3cret@postgres:5432/db",
            "SOME_FUTURE_CACHE_ADDR": "rediss://default:s3cret@cache.example.test:6380/0",
            # a webhook URL is a bearer credential in its path
            "PUBLISH_LINKEDIN_WEBHOOK_URL": "https://hooks.example.test/services/T0/B0/xyz",
            # what the CLI needs to reach the model, and things that are not secrets
            "ANTHROPIC_API_KEY": "a",
            "ANTHROPIC_AUTH_TOKEN": "a",
            "CLAUDE_CODE_OAUTH_TOKEN": "a",
            "CLAUDE_CONFIG_DIR": "/x",
            "PATH": "/usr/bin",
            "HOME": "/home/u",
            "GTM_CONTENT_ROOT": "/x",
            "SETUP_URL": "https://portal.example.test",
            "BILLING_URL": "https://billing.example.test",
            # a URL with a user but no password, and a plain one, are not secrets
            "SOME_REPO_URL": "https://git@code.example.test/repo.git",
            "HERMES_PUBLISH_URL": "https://publish.example.test/v1",
        }
    )
    assert set(scrubbed) == {
        "ROCKETREACH_API_KEY",
        "APOLLO_API_KEY",
        "SALESHANDY_API_KEY",
        "BILLING_SYNC_SECRET",
        "BACKEND_JWT_SECRET",
        "VAULT_KEK",
        "TELEGRAM_BOT_TOKEN",
        "NEWS_DB_DSN",
        "DATABASE_URL",
        "REDIS_URL",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
        "SOME_FUTURE_VENDOR_PASSWORD",
        "POSTGRES_MIGRATION_URL",
        "SOME_FUTURE_CACHE_ADDR",
        "PUBLISH_LINKEDIN_WEBHOOK_URL",
    }
    assert set(scrubbed.values()) == {""}


def test_a_tenant_run_blanks_what_it_inherits_and_a_single_tenant_run_does_not(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SALESHANDY_API_KEY", SECRET)
    monkeypatch.setenv("BILLING_SYNC_SECRET", SECRET)
    tenant = build_agent_options(
        _tenant_cfg(tmp_path, monkeypatch), "acme", can_use_tool=_confined_callback()
    )
    assert tenant.env["SALESHANDY_API_KEY"] == "" and tenant.env["BILLING_SYNC_SECRET"] == ""
    assert tenant.env.get("ANTHROPIC_API_KEY", "x") != ""  # the model credential is left alone

    cfg = dataclasses.replace(Config.from_env(repo_root=REPO), content_root=tmp_path / "content")
    single = build_agent_options(cfg, "acme")
    assert "SALESHANDY_API_KEY" not in single.env and "BILLING_SYNC_SECRET" not in single.env


# ── through the real CLI ────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def stub_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Messages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _project(tmp: Path, seen_file: Path) -> Path:
    """A throwaway project: a hook that records the secret as the CLI's own process sees it, and an
    ``.mcp.json`` that declares a server the run was never handed."""
    project = tmp / "project"
    (project / ".claude").mkdir(parents=True)
    hook = {"type": "command", "command": f"printenv SALESHANDY_API_KEY > {seen_file}; true"}
    (project / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [hook]}]}})
    )
    (project / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"project_declared_server": {"command": "sleep", "args": ["30"]}}}
        )
    )
    return project


async def _run_once(options, port: int, project: Path) -> dict:
    from claude_agent_sdk import ClaudeSDKClient, SystemMessage

    options.env = {
        **(options.env or {}),
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_API_KEY": "sk-stub",
        "CLAUDE_CONFIG_DIR": tempfile.mkdtemp(prefix="cfg_"),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }
    options.cwd = str(project)
    options.max_budget_usd = None
    init: dict = {}
    async with ClaudeSDKClient(options=options) as client:
        await client.query("say pong")
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                init = message.data
    return init


@pytest.mark.skipif(_bundled_cli() is None, reason="the SDK's bundled CLI is not installed")
def test_a_tenant_run_cannot_read_a_platform_secret_and_starts_no_project_server(
    tmp_path, monkeypatch, stub_api
):
    monkeypatch.setenv("SALESHANDY_API_KEY", SECRET)
    seen = tmp_path / "seen.txt"
    project = _project(tmp_path, seen)
    options = build_agent_options(
        _tenant_cfg(tmp_path, monkeypatch), "acme", can_use_tool=_confined_callback()
    )
    init = asyncio.run(asyncio.wait_for(_run_once(options, stub_api, project), 120))
    assert seen.read_text().strip() == "", "the platform secret reached the tenant's CLI"
    assert "project_declared_server" not in [s["name"] for s in init["mcp_servers"]]


@pytest.mark.skipif(_bundled_cli() is None, reason="the SDK's bundled CLI is not installed")
def test_the_same_probe_does_see_both_on_the_single_tenant_path(tmp_path, monkeypatch, stub_api):
    """The positive control: the hook runs, the secret is there to be read, and the project's server
    starts — so the tenant result above is a result and not a probe that sees nothing."""
    monkeypatch.setenv("SALESHANDY_API_KEY", SECRET)
    seen = tmp_path / "seen.txt"
    project = _project(tmp_path, seen)
    cfg = dataclasses.replace(Config.from_env(repo_root=REPO), content_root=tmp_path / "content")
    options = build_agent_options(cfg, "acme")
    init = asyncio.run(asyncio.wait_for(_run_once(options, stub_api, project), 120))
    assert seen.read_text().strip() == SECRET
    assert "project_declared_server" in [s["name"] for s in init["mcp_servers"]]

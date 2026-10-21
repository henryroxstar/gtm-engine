"""What a tenant pack run's `prospect` step actually starts with (client issue #337, run 82e84540).

Admission saw the workspace's RocketReach key, the server was in the run's MCP config, and the step still
stopped to ask because "RocketReach is absent from this session". Two causes, each proven here through the
REAL backend run path — ``backend.session.tenant_run_config`` → ``agent.packs.make_executor_from_pack`` →
the real bundled CLI, against a stand-in Messages API that records what each model request carried:

* nothing on the backend path set ``GTM_WORKSPACE_ID``, so ``gtm_core.preflight``'s workspace mode (the
  #361 change that lets web discovery proceed degraded) never turned on in a tenant run;
* the CLI does not wait for its MCP servers before the first turn. On a loaded host the turn starts with
  RocketReach still ``pending`` and its tools missing from the request. A contact step now waits for the
  run's contact servers, and fails ``contacts_unavailable`` — before any model request — if one never
  connects.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")

from agent import mcp_config  # noqa: E402
from agent.config import Config  # noqa: E402
from agent.packs import make_executor_from_pack  # noqa: E402
from agent.path_confinement import Confinement  # noqa: E402
from agent.pipeline import FAILED, OK  # noqa: E402
from gtm_core.packs.loader import PackGraph, PackNode  # noqa: E402
from tests.agent.test_tenant_transcripts import _bundled_cli, _Messages  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
WS = "0b1d5e3a-7c2f-4e9a-9d41-5a6b7c8d9e0f"

_needs_cli = pytest.mark.skipif(
    _bundled_cli() is None, reason="the SDK's bundled CLI is not installed"
)


class _Recording(_Messages):
    """The stand-in Messages API, keeping the tool names each model request offered."""

    requests: list[list[str]] = []

    def do_POST(self):
        if self.path.startswith("/v1/messages") and not self.path.startswith(
            "/v1/messages/count_tokens"
        ):
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            body = json.loads(raw or b"{}")
            _Recording.requests.append([t.get("name") for t in body.get("tools") or []])
            self.rfile = io.BytesIO(raw)  # hand the stand-in the body this already consumed
        return super().do_POST()


@pytest.fixture
def stub_api():
    _Recording.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recording)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _project(tmp: Path, seen: Path) -> Path:
    """The run's working directory: a hook that records GTM_WORKSPACE_ID as the CLI's own process sees it."""
    project = tmp / "project"
    (project / ".claude").mkdir(parents=True)
    hook = {"type": "command", "command": f"printenv GTM_WORKSPACE_ID > {seen}; true"}
    (project / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [hook]}]}})
    )
    return project


def _run_prospect_step(tmp_path, monkeypatch, port: int, credentials: dict[str, str]):
    """One `prospect` node through the backend's tenant config and the pack executor, on the real CLI."""
    from backend.session import tenant_run_config

    for name, value in {
        "GTM_WORKSPACES_ROOT": str(tmp_path / "workspaces"),
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_API_KEY": "sk-stub",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("WORKSPACE_ID", "GTM_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)
    seen = tmp_path / "seen.txt"
    base = dataclasses.replace(Config.from_env(repo_root=REPO), repo_root=_project(tmp_path, seen))
    cfg = asyncio.run(tenant_run_config(None, base, WS, REPO, credentials=credentials))
    cfg = dataclasses.replace(cfg, per_run_cap_usd=None)
    graph = PackGraph(
        pack="prospecting",
        variant="probe",
        nodes=(PackNode(id="prospect", skill="prospect", prompt="say pong"),),
    )
    executor = make_executor_from_pack(
        cfg, "acme", graph, confine=Confinement.for_workspace(WS, REPO)
    )
    manifest = {"run_id": "run-probe", "stages": [{"name": "prospect", "status": "running"}]}
    outcome = asyncio.run(asyncio.wait_for(executor("prospect", manifest), 240))
    return outcome, seen


@_needs_cli
def test_the_workspace_id_reaches_the_cli_of_a_tenant_run(tmp_path, monkeypatch, stub_api):
    outcome, seen = _run_prospect_step(tmp_path, monkeypatch, stub_api, {})
    assert outcome.status == OK, outcome.error
    assert seen.read_text().strip() == WS


@_needs_cli
def test_a_configured_rocketreach_key_puts_its_tools_in_the_first_turn(
    tmp_path, monkeypatch, stub_api
):
    outcome, _ = _run_prospect_step(tmp_path, monkeypatch, stub_api, {"rocketreach": "rr-test"})
    assert outcome.status == OK, outcome.error
    assert _Recording.requests, "the stage made no model request"
    first = _Recording.requests[0]
    assert "mcp__rocketreach__rocketreach_lookup" in first, first


@_needs_cli
def test_a_rocketreach_server_that_never_starts_fails_closed_before_any_spend(
    tmp_path, monkeypatch, stub_api
):
    real = mcp_config.build_mcp_servers

    def broken(cfg, profile, *, run_id=None):
        servers = real(cfg, profile, run_id=run_id)
        servers["rocketreach"] = {
            **servers["rocketreach"],
            "command": str(tmp_path / "no-such-bin"),
        }
        return servers

    monkeypatch.setattr(mcp_config, "build_mcp_servers", broken)
    outcome, _ = _run_prospect_step(tmp_path, monkeypatch, stub_api, {"rocketreach": "rr-test"})
    assert outcome.status == FAILED
    assert outcome.error_code == "contacts_unavailable"
    assert "rocketreach" in (outcome.error or "")
    assert _Recording.requests == [], "a model request was made after the contact tool failed"


def test_a_tenant_cli_gets_longer_to_start_its_servers_than_this_step_waits_less_than(
    tmp_path, monkeypatch
):
    """The CLI fails a server that takes over 30 s by default; a tenant run gives it longer, and the
    contact wait outlasts that, so a slow server connects or is reported failed, never left pending."""
    from agent.session import MCP_READY_TIMEOUT_S, _tenant_env

    monkeypatch.delenv("MCP_TIMEOUT", raising=False)
    cfg = dataclasses.replace(
        Config.from_env(repo_root=REPO), content_root=tmp_path / "workspaces" / WS / "content"
    )
    env = _tenant_env({}, cfg)
    assert env["GTM_WORKSPACE_ID"] == WS
    assert 30_000 < int(env["MCP_TIMEOUT"]) < MCP_READY_TIMEOUT_S * 1000
    assert "MCP_TIMEOUT" not in _tenant_env({"MCP_TIMEOUT": "5000"}, cfg)  # an operator's own wins

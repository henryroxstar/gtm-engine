"""A7 — per-request output language: request > agent > profile precedence, RFC
5646 shape validation at the API boundary (422, never propagated), and delivery
to skills via the PINNED subprocess env var (never shared os.environ).
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from pydantic import ValidationError  # noqa: E402

from backend.schemas import RunRequest  # noqa: E402

PROFILE = "example-ws-profile"


def _body(**over) -> dict:
    body = {"profile_name": PROFILE, "prompt": "run market-scan"}
    body.update(over)
    return body


# ── boundary validation (shape only — never a registry lookup) ────────────────


@pytest.mark.parametrize("tag", ["en", "de", "ja", "pt-BR", "zh-Hans-CN", "en-US"])
def test_valid_bcp47_tags_accepted(tag):
    assert RunRequest(**_body(language=tag)).language == tag


@pytest.mark.parametrize(
    "tag",
    [
        "",  # empty
        "e",  # too short
        "englishlanguage",  # primary subtag > 8 chars
        "en-toolongsubtag",  # secondary subtag > 8 chars
        "en_US",  # underscore is not BCP-47
        "en-",  # trailing separator
        "../../etc/passwd",  # path traversal shaped
        "en; rm -rf /",  # shell-injection shaped
        "en\nGTM_CONTENT_ROOT=/etc",  # env-injection shaped (newline)
        "x" * 40,  # over the 35-char cap
    ],
)
def test_malformed_tags_rejected_at_the_boundary(tag):
    """A malformed tag must never reach the session env — it 422s at the schema."""
    with pytest.raises(ValidationError):
        RunRequest(**_body(language=tag))


def test_language_is_optional():
    assert RunRequest(**_body()).language is None


# ── delivery: pinned in the SDK subprocess env, not os.environ ────────────────


def test_language_pinned_into_subprocess_env_only():
    """build_agent_options carries the tag as GTM_RUN_LANGUAGE alongside the
    workspace-scoping vars, so concurrent runs in different languages can't race
    through a shared os.environ."""
    from pathlib import Path

    from agent.session import build_agent_options

    cfg = MagicMock()
    cfg.content_root = Path("/tmp/content")
    cfg.profiles_root = Path("/tmp/profiles")
    cfg.repo_root = Path("/repo")

    before = os.environ.get("GTM_RUN_LANGUAGE")
    with patch("agent.session.resolve_model") as resolve:
        resolve.return_value = MagicMock(
            provider="anthropic", model="claude-x", supports={}, api_key=lambda: None
        )
        opts = build_agent_options(cfg, PROFILE, language="pt-BR")
        plain = build_agent_options(cfg, PROFILE)

    assert opts.env["GTM_RUN_LANGUAGE"] == "pt-BR"
    # Absent ⇒ the var is UNSET (not empty), so the profile's own setting governs.
    assert "GTM_RUN_LANGUAGE" not in plain.env
    # The process environment is never mutated.
    assert os.environ.get("GTM_RUN_LANGUAGE") == before


# ── precedence: request > agent > profile ─────────────────────────────────────


def test_precedence_request_beats_agent_beats_profile():
    """The resolution create_run performs, exercised directly on its inputs."""

    def resolve(request_language, agent_language):
        agent_row = {"language": agent_language} if agent_language is not None else None
        return request_language or (agent_row["language"] if agent_row is not None else None)

    assert resolve("de", "ja") == "de"  # request wins
    assert resolve(None, "ja") == "ja"  # agent wins over profile
    assert resolve(None, None) is None  # falls through to the profile


def test_create_run_threads_resolved_language(ws_env):
    """End to end through the route: the agent's tag is inherited when the request
    omits one, and an explicit request tag overrides it."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.deps import WorkspaceCtx, require_auth
    from backend.routers import runs as runs_router
    from tests.backend._protocol1 import REPO, SCOPE_MODULES, patch_everywhere
    from tests.backend.test_agents import AgentsDb, _scope
    from tests.backend.test_packs_api import _provision

    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    db = AgentsDb()
    _scope.db = db
    agent = db.add_agent(workspace_id=ws_env.ws_id, name="de-agent", language="de")

    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    app.state.sessions = MagicMock()
    app.state.cfg = MagicMock(repo_root=REPO)
    # pro_plus: this test is about language resolution, not entitlement — marketing is
    # now gated at pro_plus (gtm_core/gating.toml).
    ctx = WorkspaceCtx(user_id=str(uuid.uuid4()), workspace_id=ws_env.ws_id, entitlement="pro_plus")
    app.dependency_overrides[require_auth] = lambda: ctx

    body = {
        "pack": "marketing",
        "variant": "linkedin-post",
        "inputs": {"brand_name": "ExampleCo"},
    }
    with (
        patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
        TestClient(app) as client,
    ):
        inherited = client.post("/v1/runs", json={**body, "agent_id": agent["agent_id"]})
        overridden = client.post(
            "/v1/runs", json={**body, "agent_id": agent["agent_id"], "language": "ja"}
        )
        # A malformed tag never reaches the run path at all.
        rejected = client.post("/v1/runs", json={**body, "language": "en_US"})

    assert (inherited.status_code, overridden.status_code) == (202, 202)
    assert rejected.status_code == 422
    # A5: the request path enqueues, so the resolved tag is asserted where it now
    # travels — the queued row's payload, which is what the worker rebuilds the run
    # from. (The pre-queue audit line carried no language at all, which is exactly the
    # loss `payload` exists to close.)
    assert [json.loads(r[6])["language"] for r in db.runs] == ["de", "ja"]

"""Contract and integration tests for PRD-045:
BYOK credential verification, Staging MCP edge isolation, Gate 2 edit validation, and 409 diffs.
"""

from __future__ import annotations

import json
import tomllib
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.callers.principal import Principal
from backend.routers import runs as runs_router
from backend.services.runs import pack_executor


def test_t5_wrangler_staging_isolation():
    """T5: Wrangler TOML must define an isolated [env.staging] with its own KV namespace and all required vars."""
    wrangler_path = Path("deploy/mcp-gateway/wrangler.toml")
    if not wrangler_path.is_file():
        pytest.skip("deploy/ tree is withheld from open-source carve")
    assert wrangler_path.is_file()
    data = tomllib.loads(wrangler_path.read_text(encoding="utf-8"))

    # Verify root
    root_kv_id = data["kv_namespaces"][0]["id"]
    assert root_kv_id

    # Verify staging
    staging = data.get("env", {}).get("staging")
    assert len(staging["routes"]) > 0, "Missing routes in [env.staging]"
    assert staging["routes"][0].get("custom_domain") is True

    # KV Isolation (ST-01 / EA-1)
    staging_kv = staging.get("kv_namespaces", [])
    assert len(staging_kv) > 0, "Missing [[env.staging.kv_namespaces]]"
    staging_kv_id = staging_kv[0]["id"]
    assert staging_kv_id != root_kv_id, "Staging must not share production KV namespace ID"

    # Environment variables (EA-1)
    staging_vars = staging.get("vars", {})
    for required_var in [
        "BACKEND_API_URL",
        "ORIGIN_MCP_URL",
        "DEFAULT_CACHE_TTL_SECONDS",
        "BILLING_URL",
        "PRICING_URL",
    ]:
        assert required_var in staging_vars, f"Missing {required_var} in [env.staging.vars]"


def test_t13_preflight_spend_and_guidance():
    """T13: Preflight integration checks must return $0 spend failure with actionable guidance."""
    # 1. Missing saleshandy
    node_enroll = MagicMock(external_effect="email_enroll", skill="prospect")
    graph = MagicMock(nodes=(node_enroll,))
    cfg_empty = MagicMock(saleshandy_api_key=None, rocketreach_api_key=None)

    fail_email = pack_executor._check_pack_integrations_preflight(graph, cfg_empty)
    assert fail_email is not None
    assert fail_email.code == "email_not_configured"
    assert "Connect your Saleshandy account in Settings → Integrations" in fail_email.error

    # 2. Has saleshandy, missing contacts
    cfg_no_contacts = MagicMock(
        saleshandy_api_key="sh_key",
        rocketreach_api_key=None,
        apollo_api_key=None,
        vibe_api_key=None,
    )
    fail_contacts = pack_executor._check_pack_integrations_preflight(graph, cfg_no_contacts)
    assert fail_contacts is not None
    assert fail_contacts.code == "contacts_not_configured"
    assert "Connect a contact provider in Settings → Integrations" in fail_contacts.error


def test_t8_and_t10_gate_edit_validation_and_409_diff(monkeypatch):
    """T8 & T10: Invariant G4 service key refusal, 409 content diff, and synchronous enroll edit validation."""
    app = FastAPI()
    from backend.errors import register_error_handlers

    register_error_handlers(app)
    app.include_router(runs_router.router)

    ws_id = "11111111-1111-1111-1111-111111111111"
    user_principal = Principal(kind="user", subject="user-1", workspace_id=ws_id, entitlement="pro")
    service_principal = Principal(
        kind="service", subject="sk-1", workspace_id=ws_id, entitlement="pro"
    )

    current_principal = user_principal

    async def override_principal():
        return current_principal

    from backend.callers.rest import require_principal

    app.dependency_overrides[require_principal] = override_principal

    run_id = str(uuid.uuid4())
    gate_row = {
        "status": "awaiting_approval",
        "gate_kind": "enroll",
        "pending_content": '{"sequences": [{"name": "Alpha"}]}',
        "profile_name": "acme",
    }

    async def mock_fetch_open_gate(pool, ws, r_id):
        return gate_row

    from agent.config import Config

    monkeypatch.setattr(runs_router, "fetch_open_gate", mock_fetch_open_gate)
    app.state.pool = MagicMock()
    app.state.cfg = Config.from_env()

    client = TestClient(app)

    # 1. G4 invariant: service principal is refused with 403 human_approval_required
    current_principal = service_principal
    res_service = client.post(
        f"/runs/{run_id}/gate",
        json={"decision": "approve", "content_sha": "any_hash"},
    )
    assert res_service.status_code == 403
    assert res_service.json()["detail"]["code"] == "human_approval_required"

    # Switch back to user principal
    current_principal = user_principal

    # 2. T10: Mismatched content_sha returns 409 with current_content and current_content_sha
    res_mismatch = client.post(
        f"/runs/{run_id}/gate",
        json={"decision": "approve", "content_sha": "wrong_sha"},
    )
    assert res_mismatch.status_code == 409
    err = res_mismatch.json()["detail"]
    assert err["code"] == "content_sha_mismatch"
    assert err["current_content"] == '{"sequences": [{"name": "Alpha"}]}'
    assert "current_content_sha" in err

    # 3. T10: Malformed JSON on decision="edit" returns synchronous 422 invalid_gate_edit
    from backend.services.runs.gates import _content_sha

    correct_sha = _content_sha(gate_row["pending_content"])

    res_bad_edit = client.post(
        f"/runs/{run_id}/gate",
        json={
            "decision": "edit",
            "content_sha": correct_sha,
            "edited_content": "not valid json {",
        },
    )
    assert res_bad_edit.status_code == 422
    assert res_bad_edit.json()["detail"]["code"] == "invalid_gate_edit"
    assert "valid JSON" in res_bad_edit.json()["detail"]["next_step"]

    # 4. T10.3: Valid JSON enrollment draft passes validation and records decision
    valid_edit_content = json.dumps(
        {
            "tool": "import_prospects_to_sequence",
            "sequence_id": "seq-123",
            "step_id": "step-456",
            "prospect_list": [{"Email": "prospect@acme.example", "Name": "Alice"}],
            "steps": [
                {
                    "step_id": "step-456",
                    "variants": [
                        {
                            "subject": "Hello",
                            "content": "<p>Hi Alice</p>",
                            "preheader": "",
                        }
                    ],
                }
            ],
        }
    )
    recorded_decisions = []

    async def mock_record_decision(pool, ws, r_id, decision, edited_content):
        recorded_decisions.append((decision, edited_content))
        return {"status": "ok"}

    monkeypatch.setattr(runs_router, "record_decision", mock_record_decision)
    monkeypatch.setattr(runs_router, "publish_gate_resolved", lambda *args, **kwargs: None)

    res_good_edit = client.post(
        f"/runs/{run_id}/gate",
        json={
            "decision": "edit",
            "content_sha": correct_sha,
            "edited_content": valid_edit_content,
        },
    )
    assert res_good_edit.status_code == 200
    assert len(recorded_decisions) == 1
    assert recorded_decisions[0] == ("edit", valid_edit_content)

"""Unit tests for RT-15: Populate known fields on POST /v1/runs.

Verifies that POST /v1/runs 202 Accepted response returns `pack`, `variant`,
and a timezone-aware ISO-8601 timezone.utc `created_at` timestamp for both pack-mode
and prompt-mode runs, avoiding unnecessary follow-up GET requests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from tests.backend.test_cost_guard import (
    PROFILE,
    _Conn,
    _create_run_app,
    _post_run,
    _provision,
)


def test_create_run_pack_mode_populates_pack_variant_created_at_on_202(ws_env):
    """RT-15: In pack mode, POST /v1/runs returns pack, variant, and created_at on 202."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    conn = _Conn()
    app = _create_run_app(ws_env.ws_id)
    resp = _post_run(
        app,
        conn,
        {
            "profile_name": PROFILE,
            "pack": "marketing",
            "variant": "linkedin-post",
            "inputs": {"brand_name": "AcmeCorp"},
        },
        workspace_ok=True,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    assert data["profile_name"] == PROFILE
    assert data["pack"] == "marketing"
    assert data["variant"] == "linkedin-post"
    assert data["created_at"] is not None

    # Verify created_at parses as ISO-8601 with timezone info
    parsed_dt = datetime.fromisoformat(data["created_at"])
    assert parsed_dt.tzinfo is not None
    now_utc = datetime.now(UTC)
    assert abs((now_utc - parsed_dt).total_seconds()) < 10.0


def test_replaying_client_request_id_preserves_created_at_and_pack_variant(ws_env):
    """RT-15: Replaying an idempotent client_request_id returns the persisted created_at, pack, and variant."""
    from tests.backend.test_cost_guard import _IdempotentConn

    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    conn = _IdempotentConn()
    app = _create_run_app(ws_env.ws_id)
    body = {
        "profile_name": PROFILE,
        "pack": "marketing",
        "variant": "linkedin-post",
        "inputs": {"brand_name": "AcmeCorp"},
        "client_request_id": "req-rt15-1",
    }

    first = _post_run(app, conn, body, workspace_ok=True)
    assert first.status_code == 202
    first_data = first.json()
    assert first_data["pack"] == "marketing"
    assert first_data["variant"] == "linkedin-post"
    assert first_data["created_at"] is not None

    # Set row's created_at in the mock so the replayed fetch_run_detail returns it
    run_id = first_data["run_id"]
    row_created_at = "2026-09-19T02:00:00+00:00"
    conn.rows[run_id]["created_at"] = row_created_at

    second = _post_run(app, conn, body, workspace_ok=True)
    assert second.status_code == 202
    second_data = second.json()
    assert second_data["run_id"] == run_id
    assert second_data["pack"] == "marketing"
    assert second_data["variant"] == "linkedin-post"
    assert second_data["created_at"] == row_created_at


def test_create_run_prompt_mode_preserves_none_pack_and_valid_created_at():
    """RT-15: In prompt mode, pack and variant are None while created_at is valid ISO-8601."""
    ws_id = str(uuid.uuid4())
    conn = _Conn()
    app = _create_run_app(ws_id)
    resp = _post_run(
        app,
        conn,
        {"profile_name": "any-profile", "prompt": "scan news and generate summary"},
        workspace_ok=True,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    assert data["profile_name"] == "any-profile"
    assert data["pack"] is None
    assert data["variant"] is None
    assert data["created_at"] is not None

    parsed_dt = datetime.fromisoformat(data["created_at"])
    assert parsed_dt.tzinfo is not None
    now_utc = datetime.now(UTC)
    assert abs((now_utc - parsed_dt).total_seconds()) < 10.0

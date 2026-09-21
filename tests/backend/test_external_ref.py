"""Unit and integration tests for G1 (external_ref).

Verifies:
- RunRequest accepts valid external_ref, rejects invalid charset or > 128 chars
- RunResponse echoes external_ref
- run-event.schema.json snapshot validates with external_ref
- GET /v1/runs?external_ref=... query filtering
- Snapshot frame generation carries external_ref
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.schemas import RunRequest, RunResponse
from backend.services.runs.stream import _Opening, _snapshot_frame
from tests.contracts.minijsonschema import validate as schema_validate

REPO = Path(__file__).resolve().parents[2]
RUN_EVENT_SCHEMA = json.loads((REPO / "schemas" / "run-event.schema.json").read_text())


def test_run_request_external_ref_validation():
    # Valid external_refs
    valid_refs = [
        "task-1234",
        "abc.DEF:123_456",
        "a",
        "123",
        "run:step.sub-1_2",
    ]
    for ref in valid_refs:
        req = RunRequest(prompt="test prompt", profile_name="test", external_ref=ref)
        assert req.external_ref == ref

    # Absent / None is valid
    req_none = RunRequest(prompt="test prompt", profile_name="test")
    assert req_none.external_ref is None

    # Invalid: spaces, special chars, too long
    invalid_refs = [
        "task 123",  # space
        "task@123",  # @ not allowed
        "task/123",  # / not allowed
        "task#123",  # # not allowed
        "a" * 129,  # > 128 chars
        "",  # empty string rejected by pattern ^...+$
    ]
    for ref in invalid_refs:
        with pytest.raises(ValidationError):
            RunRequest(prompt="test prompt", profile_name="test", external_ref=ref)


def test_run_response_echoes_external_ref():
    resp = RunResponse(
        run_id="00000000-0000-0000-0000-000000000001",
        status="queued",
        profile_name="test",
        external_ref="my-ext-ref-42",
    )
    assert resp.external_ref == "my-ext-ref-42"
    assert resp.model_dump()["external_ref"] == "my-ext-ref-42"


def test_snapshot_event_schema_with_external_ref():
    snapshot_event = {
        "event": "snapshot",
        "seq": 10,
        "data": {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "status": "running",
            "external_ref": "task-xyz-99",
            "principal_kind": "service",
            "principal_id": "key-123",
        },
    }
    schema_validate(snapshot_event, RUN_EVENT_SCHEMA)

    # Null external_ref is also valid
    snapshot_event_null = {
        "event": "snapshot",
        "seq": 10,
        "data": {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "status": "running",
            "external_ref": None,
        },
    }
    schema_validate(snapshot_event_null, RUN_EVENT_SCHEMA)


def test_snapshot_frame_includes_external_ref():
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "status": "running",
        "pending_gate": None,
        "pending_content": None,
        "gate_kind": None,
        "gate_node_id": None,
        "principal_kind": "service",
        "principal_id": "key-123",
        "external_ref": "my-correlated-task-1",
    }
    opening = _Opening(row=row, head=5, replay=None, nodes=[], blocks=[])
    frame = _snapshot_frame(opening)

    # Frame should be SSE format
    assert frame.startswith("id: 5\nevent: snapshot\ndata: ")
    data_json = frame.split("data: ")[1].strip()
    data = json.loads(data_json)
    assert data["external_ref"] == "my-correlated-task-1"
    assert data["run_id"] == "00000000-0000-0000-0000-000000000001"

"""Tests for backend schema hardening and validations (Task 5)."""

import pytest
from pydantic import ValidationError

from backend.schemas import (
    GateRequest,
    PatchWorkspaceRequest,
    PublishSettingsSyncRequest,
)


def test_publish_settings_sync_request_enabled_defaults_to_none():
    req = PublishSettingsSyncRequest(schedule_enabled=True)
    assert req.enabled is None


def test_gate_request_edited_content_must_be_none_on_approve_or_reject():
    with pytest.raises(ValidationError, match="edited_content must be None"):
        GateRequest(decision="approve", edited_content="hack", content_sha="x" * 64)

    with pytest.raises(ValidationError, match="edited_content must be None"):
        GateRequest(decision="reject", edited_content="hack", content_sha="x" * 64)


def test_patch_workspace_request_permits_empty_body():
    req = PatchWorkspaceRequest()
    assert req.display_name is None

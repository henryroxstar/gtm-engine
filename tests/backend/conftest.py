"""Shared backend-suite fixtures (Track A).

``ws_env`` was born in test_packs_api.py and is shared by the A2/A11 suites
(test_stream_protocol1 / test_run_persistence / test_run_artifacts) — a conftest
fixture avoids cross-test-module fixture imports (F811 shadowing).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    """Workspace-scoped roots under tmp: GTM_WORKSPACES_ROOT drives
    workspace_profiles_root/workspace_content_root for a random workspace id."""
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    ws_id = str(uuid.uuid4())
    profiles_root = tmp_path / "workspaces" / ws_id / "profiles"
    content_root = tmp_path / "workspaces" / ws_id / "content"
    profiles_root.mkdir(parents=True)
    content_root.mkdir(parents=True)
    return SimpleNamespace(ws_id=ws_id, profiles_root=profiles_root, content_root=content_root)

"""Tests for cross-worker session eviction on WORKSPACE_EVICT_CHANNEL (T10, T11, T12)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from backend.broker import WORKSPACE_EVICT_CHANNEL
from backend.services.runs import events, state
from backend.session import BackendSessionStore
from gtm_core.paths import workspace_tree

_WS_ID = "11111111-1111-1111-1111-111111111111"
_WS_OTHER = "22222222-2222-2222-2222-222222222222"


def _run(coro):
    return asyncio.run(coro)


class _DummySession:
    def __init__(self):
        self.closed = False
        self.connected = True
        self.last_used = 1000.0

    async def close(self):
        self.closed = True


def test_t10_credentials_rotated_evicts_idle_preserves_in_use(tmp_path, monkeypatch):
    """T10: Non-destructive credentials_rotated eviction closes idle, preserves in-use, bumps epoch."""

    async def _test():
        monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
        ws_dir = workspace_tree(_WS_ID)
        ws_dir.mkdir(parents=True, exist_ok=True)
        sample_file = ws_dir / "keep_me.txt"
        sample_file.write_text("important", encoding="utf-8")

        store = BackendSessionStore(repo_root=tmp_path)
        state.register_session_store(store)

        # Seed an idle session and an in-use session
        idle_key = (_WS_ID, "acme", None)
        idle_sess = _DummySession()
        store._sessions[idle_key] = idle_sess

        in_use_key = (_WS_ID, "acme_active", None)
        in_use_sess = _DummySession()
        store._in_use[in_use_key] = {in_use_sess}

        # Other workspace session to verify isolation
        other_key = (_WS_OTHER, "other", None)
        other_sess = _DummySession()
        store._sessions[other_key] = other_sess

        initial_epoch = store._workspace_epoch(_WS_ID)

        # Mock cancel_workspace_tasks to assert it is NOT called
        mock_cancel = AsyncMock()
        monkeypatch.setattr(state, "cancel_workspace_tasks", mock_cancel)

        # Fire credentials_rotated event
        events.on_broker_message(
            WORKSPACE_EVICT_CHANNEL,
            {"workspace_id": _WS_ID, "reason": "credentials_rotated"},
        )
        # Yield control to let async _evict() run
        await asyncio.sleep(0.05)

        # Assertions
        # 1. Idle session was popped and closed
        assert idle_key not in store._sessions
        assert idle_sess.closed is True

        # 2. In-use session was PRESERVED (still open, not popped)
        assert in_use_key in store._in_use
        assert in_use_sess in store._in_use[in_use_key]
        assert in_use_sess.closed is False

        # 3. Epoch bumped for _WS_ID
        assert store._workspace_epoch(_WS_ID) == initial_epoch + 1

        # 4. Other workspace untouched
        assert other_key in store._sessions
        assert other_sess.closed is False

        # 5. cancel_workspace_tasks was NOT called
        mock_cancel.assert_not_called()

        # 6. Disk files intact
        assert sample_file.exists()
        assert sample_file.read_text(encoding="utf-8") == "important"

    _run(_test())


def test_t11_account_deleted_full_teardown_and_workspace_tree_import(tmp_path, monkeypatch):
    """T11: Full teardown on account_deleted (cancels tasks, closes all sessions, cleans disk)."""

    async def _test():
        monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
        ws_dir = workspace_tree(_WS_ID)
        ws_dir.mkdir(parents=True, exist_ok=True)
        sample_file = ws_dir / "delete_me.txt"
        sample_file.write_text("ephemeral", encoding="utf-8")

        store = BackendSessionStore(repo_root=tmp_path)
        state.register_session_store(store)

        idle_key = (_WS_ID, "acme", None)
        idle_sess = _DummySession()
        store._sessions[idle_key] = idle_sess

        in_use_key = (_WS_ID, "acme_active", None)
        in_use_sess = _DummySession()
        store._in_use[in_use_key] = {in_use_sess}

        mock_cancel = AsyncMock()
        monkeypatch.setattr(state, "cancel_workspace_tasks", mock_cancel)

        # Fire account_deleted event (reason defaults to account_deleted if omitted)
        events.on_broker_message(
            WORKSPACE_EVICT_CHANNEL,
            {"workspace_id": _WS_ID, "reason": "account_deleted"},
        )
        await asyncio.sleep(0.05)

        # 1. Both idle and in-use sessions closed and evicted
        assert idle_key not in store._sessions
        assert idle_sess.closed is True
        assert in_use_key not in store._in_use
        assert in_use_sess.closed is True

        # 2. cancel_workspace_tasks was called
        mock_cancel.assert_awaited_once_with(_WS_ID)

        # 3. Disk cleaned up using workspace_tree
        assert not ws_dir.exists()

    _run(_test())


def test_t12_path_traversal_and_unknown_reason_refusal(tmp_path, monkeypatch, caplog):
    """T12: Path traversal and unknown reason payloads are rejected fail-closed."""

    async def _test():
        monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
        store = BackendSessionStore(repo_root=tmp_path)
        state.register_session_store(store)

        idle_key = (_WS_ID, "acme", None)
        idle_sess = _DummySession()
        store._sessions[idle_key] = idle_sess

        mock_cancel = AsyncMock()
        monkeypatch.setattr(state, "cancel_workspace_tasks", mock_cancel)

        # 1. Path traversal in workspace_id
        with caplog.at_level("WARNING"):
            events.on_broker_message(
                WORKSPACE_EVICT_CHANNEL,
                {"workspace_id": "../etc", "reason": "account_deleted"},
            )
            await asyncio.sleep(0.02)
        assert "invalid workspace_id" in caplog.text
        assert idle_key in store._sessions
        assert idle_sess.closed is False
        mock_cancel.assert_not_called()

        # 2. Unknown reason
        caplog.clear()
        with caplog.at_level("WARNING"):
            events.on_broker_message(
                WORKSPACE_EVICT_CHANNEL,
                {"workspace_id": _WS_ID, "reason": "unknown_mode"},
            )
            await asyncio.sleep(0.02)
        assert "unknown eviction reason" in caplog.text
        assert idle_key in store._sessions
        assert idle_sess.closed is False
        mock_cancel.assert_not_called()

    _run(_test())

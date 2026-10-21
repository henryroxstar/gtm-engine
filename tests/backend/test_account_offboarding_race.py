"""Tests for account offboarding / deletion race closure (T11, T12)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

WS_A = "aaaaaaaa-1111-2222-3333-444444444444"
WS_B = "bbbbbbbb-1111-2222-3333-444444444444"
USER_A = "11111111-2222-3333-4444-555555555555"


def _run(coro):
    return asyncio.run(coro)


def test_t11a_cancel_workspace_runs_uses_single_l_canceled_and_settles_reservations():
    """T11a (R10): cancel_workspace_runs sets status = 'canceled' (in _TERMINAL_STATUSES),
    closes open run_gates, wakes gate events, and calls _settle_run_reservations.
    """
    from backend.services.runs.decisions import cancel_workspace_runs
    from backend.services.runs.state import _TERMINAL_STATUSES, _cancelled_runs, _gate_events

    assert "canceled" in _TERMINAL_STATUSES
    assert "cancelled" not in _TERMINAL_STATUSES

    executed_queries: list[str] = []
    conn = MagicMock()

    async def _fetch(query, *args):
        executed_queries.append(query)
        return [
            {"id": "run-queued-1", "status": "queued"},
            {"id": "run-gated-1", "status": "awaiting_approval"},
        ]

    async def _execute(query, *args):
        executed_queries.append(query)
        return "UPDATE 2"

    conn.fetch = _fetch
    conn.execute = _execute
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    ev = asyncio.Event()
    _gate_events["run-gated-1"] = ev

    with (
        patch(
            "backend.services.runs.decisions._settle_run_reservations", AsyncMock()
        ) as mock_settle,
        patch("backend.services.runs.decisions.publish_gate_wake", AsyncMock()) as mock_wake,
    ):
        cancelled_ids = _run(cancel_workspace_runs(pool, WS_A))

    assert cancelled_ids == ["run-queued-1", "run-gated-1"]
    assert "run-queued-1" in _cancelled_runs
    assert "run-gated-1" in _cancelled_runs
    assert ev.is_set()
    assert any("status = 'canceled'" in q for q in executed_queries)
    assert not any("status = 'cancelled'" in q for q in executed_queries)
    assert any("UPDATE run_gates" in q for q in executed_queries)
    assert mock_settle.await_count == 2
    assert mock_wake.await_count >= 1


def test_t11b_evict_workspace_closes_both_idle_and_in_use_sessions_and_blocks_reinsert():
    """T11b (R10): BackendSessionStore.evict_workspace closes both cached idle sessions and
    active in-use sessions popped during run(), and prevents run()'s finally block from re-inserting them.
    """
    from backend.session import BackendSessionStore, _BackendSession

    store = BackendSessionStore(max_warm_sessions=8)

    def _fake_sess() -> _BackendSession:
        s = MagicMock(spec=_BackendSession)
        s.last_used = 100.0
        s.is_expired = MagicMock(return_value=False)
        s.touch = MagicMock()
        s.close = AsyncMock()
        return s

    async def _scenario():
        idle_a = _fake_sess()
        active_a = _fake_sess()
        idle_b = _fake_sess()

        # Seed idle session for WS_A and WS_B
        await store._return_session((WS_A, "acme"), idle_a, epoch=store._workspace_epoch(WS_A))
        await store._return_session((WS_B, "globex"), idle_b, epoch=store._workspace_epoch(WS_B))

        # Simulate active_a being checked out (in-use) during run()
        epoch_before = store._checkout_in_use((WS_A, "acme"), active_a)

        # Evict WS_A while active_a is mid-flight!
        evicted_count = await store.evict_workspace(WS_A)
        assert evicted_count == 2
        idle_a.close.assert_awaited_once()
        active_a.close.assert_awaited_once()
        idle_b.close.assert_not_awaited()

        # When active_a's interrupted run() exits its finally block with the old epoch,
        # _return_session MUST NOT put active_a back into store._sessions!
        await store._return_session((WS_A, "acme"), active_a, epoch=epoch_before)
        assert (WS_A, "acme") not in store._sessions
        assert (WS_B, "globex") in store._sessions

    _run(_scenario())


def test_t11c_d_e_cancel_workspace_tasks_broker_evict_and_release_identity(tmp_path):
    """T11c/d/e (R10, R11):
    (c) cancel_workspace_tasks cancels active pack/prompt asyncio.Tasks for WS_A without touching WS_B.
    (d) publish_workspace_evict publishes to gtm:ws:evict via get_broker() and on_broker_message triggers eviction.
    (e) release_identity removes WS_A from identities.json so the UID slot is reclaimed.
    """
    from agent.tenant_identity import identity_for, release_identity
    from backend import broker as bmod
    from backend.services.runs import events as emod
    from backend.services.runs import state as smod

    # (e) UID allocation and reclamation
    uid_a = identity_for(WS_A, tmp_path)
    uid_b = identity_for(WS_B, tmp_path)
    assert uid_a != uid_b
    assert release_identity(WS_A, tmp_path) is True
    assert release_identity(WS_A, tmp_path) is False
    # Re-allocating after release works cleanly and WS_B is untouched
    assert identity_for(WS_B, tmp_path) == uid_b

    # (c) & (d) Active workspace task cancellation + broker pub/sub
    async def _scenario():
        cancelled_a = False
        cancelled_b = False

        async def _task_a():
            nonlocal cancelled_a
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                cancelled_a = True
                raise

        async def _task_b():
            nonlocal cancelled_b
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                cancelled_b = True
                raise

        ta = asyncio.create_task(_task_a())
        tb = asyncio.create_task(_task_b())
        smod.register_workspace_task(WS_A, ta)
        smod.register_workspace_task(WS_B, tb)

        fake_broker = MagicMock()
        fake_broker.publish = AsyncMock()
        bmod.set_broker(fake_broker)
        try:
            await bmod.publish_workspace_evict(WS_A)
            fake_broker.publish.assert_awaited_once_with(
                bmod.WORKSPACE_EVICT_CHANNEL,
                {"workspace_id": WS_A, "reason": "account_deleted"},
            )
        finally:
            bmod.set_broker(None)

        # Trigger on_broker_message for gtm:ws:evict
        emod.on_broker_message(bmod.WORKSPACE_EVICT_CHANNEL, {"workspace_id": WS_A})
        # Let scheduled eviction task run
        for _ in range(20):
            if cancelled_a:
                break
            await asyncio.sleep(0.01)

        assert cancelled_a is True
        assert cancelled_b is False
        tb.cancel()
        await asyncio.gather(ta, tb, return_exceptions=True)

    _run(_scenario())


def test_t12_delete_account_cancels_runs_evicts_sessions_deletes_disk_before_db_and_reclaims_uid(
    tmp_path, monkeypatch
):
    """T12 (R11): DELETE /v1/account cancels runs & tasks, evicts sessions, broadcasts publish_workspace_evict,
    deletes disk tree BEFORE DELETE FROM users (preserving retryability), and reclaims UID.
    """
    from agent.tenant_identity import identity_for
    from backend.deps import WorkspaceCtx
    from backend.routers.account import delete_account
    from backend.schemas import DeleteAccountRequest

    ws_root = tmp_path / "workspaces"
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(ws_root))
    tree = ws_root / WS_A
    (tree / "profiles" / "acme").mkdir(parents=True)
    (tree / "content" / "acme").mkdir(parents=True)
    identity_for(WS_A, ws_root)

    call_order: list[str] = []

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"password_hash": "$2b$12$fakehash"})

    async def _execute(query, *args):
        if "DELETE FROM users" in query:
            # Disk tree must ALREADY be removed before DELETE FROM users runs!
            assert not tree.exists(), "Disk tree must be removed BEFORE DELETE FROM users"
            call_order.append("db_delete_user")
        return "DELETE 1"

    conn.execute = _execute
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    fake_sessions = MagicMock()

    async def _evict_ws(ws_id):
        call_order.append(f"evict_sessions:{ws_id}")
        return 1

    fake_sessions.evict_workspace = _evict_ws

    req = MagicMock()
    req.app.state.pool = pool
    req.app.state.cfg.repo_root = tmp_path
    req.app.state.sessions = fake_sessions

    from gtm_core.capabilities import Entitlement

    ws_ctx = WorkspaceCtx(
        user_id=USER_A,
        workspace_id=WS_A,
        entitlement=Entitlement.PRO,
    )

    async def _cancel_runs(_pool, ws_id):
        call_order.append(f"cancel_runs:{ws_id}")
        return ["run-1"]

    async def _cancel_tasks(ws_id):
        call_order.append(f"cancel_tasks:{ws_id}")
        return 1

    async def _pub_evict(ws_id):
        call_order.append(f"publish_evict:{ws_id}")

    with (
        patch("backend.routers.account._auth.verify_password", return_value=True),
        patch("backend.routers.account.cancel_workspace_runs", side_effect=_cancel_runs),
        patch("backend.routers.account.cancel_workspace_tasks", side_effect=_cancel_tasks),
        patch("backend.routers.account.publish_workspace_evict", side_effect=_pub_evict),
    ):
        resp = _run(
            delete_account(
                body=DeleteAccountRequest(current_password="secret-password"),
                ws=ws_ctx,
                request=req,
            )
        )

    assert resp == {"status": "deleted"}
    assert not tree.exists()
    assert call_order == [
        f"cancel_runs:{WS_A}",
        f"cancel_tasks:{WS_A}",
        f"evict_sessions:{WS_A}",
        f"publish_evict:{WS_A}",
        "db_delete_user",
    ]
    # UID in identities.json was reclaimed
    import json

    identities = json.loads((ws_root / "identities.json").read_text(encoding="utf-8"))
    assert WS_A not in identities

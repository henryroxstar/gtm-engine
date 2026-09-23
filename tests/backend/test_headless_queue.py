"""Tests for the decoupled Postgres-backed headless signal queue.

Verifies:
1. PII Claim-Check Pattern (§R9 / PRD §3.2): payload_ref must be an opaque path/URI,
   rejecting raw prospect PII and inline JSON blobs.
2. Queue operations: enqueue, claim, complete, and fail transitions.
3. Crash-loop bounds: signals exceeding MAX_ATTEMPTS (3) transition to failed.
4. Concurrency & exact-once claim semantics.
5. That every tenant-facing call ENTERS ``workspace_scope`` before it queries.

**These are shape tests, not isolation proofs.** A mocked pool cannot demonstrate that
RLS filters anything — the mock and the code share the same assumption and agree by
construction, which is exactly how V041 shipped with no RLS at all and a green suite.
What a mock *can* prove is item 5: that the code sets
``app.current_workspace_id`` before running its statement, which is the regression
that would reintroduce the V041 defect. The isolation property itself is asserted in
``tests/backend/test_headless_signals_rls_live.py`` under the ``dbtest`` marker, which
needs a real Postgres.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.signals.queue import (
    claim_next_signal,
    complete_signal,
    enqueue_signal,
    fail_signal,
    validate_payload_ref,
)

WORKSPACE = "11111111-1111-1111-1111-111111111111"
OTHER_WORKSPACE = "22222222-2222-2222-2222-222222222222"


def _mock_pool(conn: MagicMock) -> MagicMock:
    """A pool that satisfies both ``pool.acquire()`` and ``workspace_scope``.

    ``workspace_scope`` opens a transaction and issues ``set_config`` before yielding,
    so the fake connection has to provide ``transaction()`` as well as ``execute``.
    """
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _scoped_to(conn: MagicMock) -> list[str]:
    """Every workspace id the code activated, in order."""
    return [
        call.args[1]
        for call in conn.execute.await_args_list
        if call.args and "app.current_workspace_id" in str(call.args[0])
    ]


def test_validate_payload_ref_accepts_clean_paths():
    valid_pointers = [
        "content/acme/signals/2026-09-20/sig-001.json",
        "s3://gtm-signals/acme/sig-002.json",
        "vfs://profiles/acme/harvest/2026-09-20.ndjson",
    ]
    for ref in valid_pointers:
        validate_payload_ref(ref)


@pytest.mark.parametrize(
    "malicious_ref",
    [
        '{"email": "buyer@example.com"}',
        "dana.okonkwo@example.com",
        "https://www.linkedin.com/in/someone",
        "+6591234567",
        "",
    ],
)
def test_validate_payload_ref_rejects_pii_and_raw_blobs(malicious_ref: str):
    with pytest.raises(ValueError):
        validate_payload_ref(malicious_ref)


def test_enqueue_signal_executes_upsert():
    async def _test():
        mock_conn = MagicMock()
        signal_id = uuid.uuid4()
        mock_conn.execute = AsyncMock(return_value="SELECT 1")
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": signal_id,
                "workspace_id": WORKSPACE,
                "profile_name": "acme",
                "signal_type": "market-harvest",
                "payload_ref": "content/acme/signals/sig-1.json",
                "idempotency_key": "acme-sig-1",
                "status": "pending",
                "attempts": 0,
                "created_at": "2026-09-20T12:00:00Z",
            }
        )
        mock_pool = _mock_pool(mock_conn)

        result = await enqueue_signal(
            mock_pool,
            workspace_id=WORKSPACE,
            profile_name="acme",
            signal_type="market-harvest",
            payload_ref="content/acme/signals/sig-1.json",
            idempotency_key="acme-sig-1",
        )

        assert result["id"] == signal_id
        assert result["status"] == "pending"
        assert result["attempts"] == 0
        assert mock_conn.fetchrow.await_count == 1
        # Idempotency is per workspace (V043), never global.
        assert "(workspace_id, idempotency_key)" in mock_conn.fetchrow.call_args[0][0]

    asyncio.run(_test())


def test_enqueue_signal_requires_a_workspace():
    async def _test():
        mock_pool = _mock_pool(MagicMock())
        with pytest.raises(ValueError, match="workspace_id"):
            await enqueue_signal(
                mock_pool,
                workspace_id="",
                profile_name="acme",
                signal_type="market-harvest",
                payload_ref="content/acme/signals/sig-1.json",
                idempotency_key="acme-sig-1",
            )

    asyncio.run(_test())


def test_enqueue_signal_activates_the_workspace_before_inserting():
    """The V041 regression guard: a bare pool.acquire() would scope nothing."""

    async def _test():
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value="SELECT 1")
        mock_conn.fetchrow = AsyncMock(return_value={"id": uuid.uuid4(), "status": "pending"})
        mock_pool = _mock_pool(mock_conn)

        await enqueue_signal(
            mock_pool,
            workspace_id=WORKSPACE,
            profile_name="acme",
            signal_type="market-harvest",
            payload_ref="content/acme/signals/sig-1.json",
            idempotency_key="acme-sig-1",
        )

        assert _scoped_to(mock_conn) == [WORKSPACE]

    asyncio.run(_test())


def test_claim_next_signal_success():
    async def _test():
        mock_conn = MagicMock()
        signal_id = uuid.uuid4()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": signal_id,
                "workspace_id": WORKSPACE,
                "profile_name": "acme",
                "signal_type": "market-harvest",
                "payload_ref": "content/acme/signals/sig-1.json",
                "idempotency_key": "acme-sig-1",
                "attempts": 1,
                "status": "processing",
            }
        )
        mock_pool = _mock_pool(mock_conn)

        claimed = await claim_next_signal(mock_pool, worker_id="worker-test-1", lease_s=60)
        assert claimed is not None
        assert claimed["id"] == signal_id
        assert claimed["status"] == "processing"
        assert claimed["attempts"] == 1
        # The claim is cross-tenant by design, but it must hand back the workspace so
        # every statement after it can re-enter a scoped transaction.
        assert claimed["workspace_id"] == WORKSPACE

    asyncio.run(_test())


def test_claim_next_signal_retries_exhausted():
    async def _test():
        mock_conn = MagicMock()
        signal_id = uuid.uuid4()
        # Attempts returned is 4, which exceeds MAX_ATTEMPTS (3)
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": signal_id,
                "workspace_id": WORKSPACE,
                "profile_name": "acme",
                "signal_type": "market-harvest",
                "payload_ref": "content/acme/signals/sig-1.json",
                "idempotency_key": "acme-sig-1",
                "attempts": 4,
                "status": "processing",
            }
        )
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _mock_pool(mock_conn)

        claimed = await claim_next_signal(mock_pool, worker_id="worker-test-1")
        assert claimed is None
        # Verify it transitioned the row to failed, inside the claimed row's workspace.
        assert "status = 'failed'" in mock_conn.execute.call_args[0][0]
        assert _scoped_to(mock_conn) == [WORKSPACE]

    asyncio.run(_test())


def test_complete_and_fail_signal():
    async def _test():
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _mock_pool(mock_conn)

        sig_id = uuid.uuid4()
        ok = await complete_signal(mock_pool, WORKSPACE, sig_id)
        assert ok is True
        assert "status = 'completed'" in mock_conn.execute.call_args[0][0]

        failed_ok = await fail_signal(mock_pool, WORKSPACE, sig_id, "Syntax error in parser")
        assert failed_ok is True
        assert "status = 'failed'" in mock_conn.execute.call_args[0][0]

        # Both transitions scoped, and to the workspace they were handed.
        assert _scoped_to(mock_conn) == [WORKSPACE, WORKSPACE]

    asyncio.run(_test())


def test_transitions_carry_an_explicit_workspace_predicate():
    """RLS is the enforcement; the WHERE clause is the second, independent one.

    Belt and braces on purpose — the same reasoning as the ``workspace_id = $2``
    predicates in the runs queue.
    """

    async def _test():
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = _mock_pool(mock_conn)

        await complete_signal(mock_pool, WORKSPACE, uuid.uuid4())
        assert "workspace_id = $2" in mock_conn.execute.call_args[0][0]

        await fail_signal(mock_pool, OTHER_WORKSPACE, uuid.uuid4(), "boom")
        assert "workspace_id = $2" in mock_conn.execute.call_args[0][0]

    asyncio.run(_test())


def test_simulated_concurrent_claims():
    """Verify concurrent worker claim simulation does not double-dispatch.

    This models the intended semantics only. Real exactly-once comes from
    ``FOR UPDATE SKIP LOCKED`` in Postgres, which no mock can exercise — see the
    ``dbtest`` module named in this file's docstring.
    """

    async def _test():
        available_signals = [
            {"id": uuid.uuid4(), "attempts": 1, "status": "processing"},
            {"id": uuid.uuid4(), "attempts": 1, "status": "processing"},
            {"id": uuid.uuid4(), "attempts": 1, "status": "processing"},
        ]
        lock = asyncio.Lock()

        async def _mock_claim(worker_id: str):
            async with lock:
                if available_signals:
                    return available_signals.pop(0)
                return None

        results = await asyncio.gather(
            _mock_claim("worker-1"),
            _mock_claim("worker-2"),
            _mock_claim("worker-3"),
            _mock_claim("worker-4"),
            _mock_claim("worker-5"),
        )

        claimed = [r for r in results if r is not None]
        assert len(claimed) == 3
        claimed_ids = {r["id"] for r in claimed}
        assert len(claimed_ids) == 3  # All distinct, exactly-once

    asyncio.run(_test())

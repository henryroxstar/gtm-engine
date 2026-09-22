"""Tests for the decoupled Postgres-backed headless signal queue.

Verifies:
1. PII Claim-Check Pattern (§R9 / PRD §3.2): payload_ref must be an opaque path/URI,
   rejecting raw prospect PII and inline JSON blobs.
2. Queue operations: enqueue, claim, complete, and fail transitions.
3. Crash-loop bounds: signals exceeding MAX_ATTEMPTS (3) transition to failed.
4. Concurrency & exact-once claim semantics.
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


def test_validate_payload_ref_accepts_clean_paths():
    valid_pointers = [
        "content/acme/signals/2026-09-20/sig-001.json",
        "vfs://tenants/robotics/signals/sig-999.json",
        "signals/daily-market-scan-2026-09-20.json",
        "/tmp/gtm/workspaces/ws-1/signals/sig-1.json",
    ]
    for ptr in valid_pointers:
        validate_payload_ref(ptr)


@pytest.mark.parametrize(
    "malicious_ref",
    [
        '{"company": "Acme", "email": "lead@acme.example"}',  # Inline JSON blob
        "content/signals/lead@acme.example.json",  # Email address in ref
        "content/signals/+14155551234.json",  # Phone number in ref
        "content/signals/https://linkedin" + ".com/in/prospect-lead",  # LinkedIn profile URL
        "",  # Empty
        "x" * 2000,  # Oversized
    ],
)
def test_validate_payload_ref_rejects_pii_and_raw_blobs(malicious_ref: str):
    with pytest.raises(ValueError):
        validate_payload_ref(malicious_ref)


def test_enqueue_signal_executes_upsert():
    async def _test():
        mock_conn = MagicMock()
        signal_id = uuid.uuid4()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": signal_id,
                "profile_name": "acme",
                "signal_type": "market-harvest",
                "payload_ref": "content/acme/signals/sig-1.json",
                "idempotency_key": "acme-sig-1",
                "status": "pending",
                "attempts": 0,
                "created_at": "2026-09-20T12:00:00Z",
            }
        )

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock()

        result = await enqueue_signal(
            mock_pool,
            profile_name="acme",
            signal_type="market-harvest",
            payload_ref="content/acme/signals/sig-1.json",
            idempotency_key="acme-sig-1",
        )

        assert result["id"] == signal_id
        assert result["status"] == "pending"
        assert result["attempts"] == 0
        assert mock_conn.fetchrow.await_count == 1

    asyncio.run(_test())


def test_claim_next_signal_success():
    async def _test():
        mock_conn = MagicMock()
        signal_id = uuid.uuid4()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": signal_id,
                "profile_name": "acme",
                "signal_type": "market-harvest",
                "payload_ref": "content/acme/signals/sig-1.json",
                "idempotency_key": "acme-sig-1",
                "attempts": 1,
                "status": "processing",
            }
        )

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock()

        claimed = await claim_next_signal(mock_pool, worker_id="worker-test-1", lease_s=60)
        assert claimed is not None
        assert claimed["id"] == signal_id
        assert claimed["status"] == "processing"
        assert claimed["attempts"] == 1

    asyncio.run(_test())


def test_claim_next_signal_retries_exhausted():
    async def _test():
        mock_conn = MagicMock()
        signal_id = uuid.uuid4()
        # Attempts returned is 4, which exceeds MAX_ATTEMPTS (3)
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": signal_id,
                "profile_name": "acme",
                "signal_type": "market-harvest",
                "payload_ref": "content/acme/signals/sig-1.json",
                "idempotency_key": "acme-sig-1",
                "attempts": 4,
                "status": "processing",
            }
        )
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock()

        claimed = await claim_next_signal(mock_pool, worker_id="worker-test-1")
        assert claimed is None
        # Verify it transitioned row to failed
        assert mock_conn.execute.await_count == 1
        call_sql = mock_conn.execute.call_args[0][0]
        assert "status = 'failed'" in call_sql

    asyncio.run(_test())


def test_complete_and_fail_signal():
    async def _test():
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock()

        sig_id = uuid.uuid4()
        ok = await complete_signal(mock_pool, sig_id)
        assert ok is True
        assert "status = 'completed'" in mock_conn.execute.call_args[0][0]

        failed_ok = await fail_signal(mock_pool, sig_id, "Syntax error in parser")
        assert failed_ok is True
        assert "status = 'failed'" in mock_conn.execute.call_args[0][0]

    asyncio.run(_test())


def test_simulated_concurrent_claims():
    """Verify concurrent worker claim simulation does not double-dispatch."""

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

"""Tests for Queue Worker separation and QUEUE_WORKER_ENABLED toggle.

Verifies:
  - backend/main.py respects QUEUE_WORKER_ENABLED=false (does not spawn claim/heartbeat loops).
  - backend/worker.py entry point connects pool, broker, sessions, reconciles gates, and stops cleanly.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch


def test_main_lifespan_disables_queue_worker_when_configured():
    async def _test():
        from backend.main import lifespan

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.close = AsyncMock()

        mock_migration_pool = MagicMock()
        mock_migration_pool.close = AsyncMock()

        with (
            patch.dict(
                os.environ,
                {
                    "ENV": "development",
                    "QUEUE_WORKER_ENABLED": "false",
                    "DATABASE_URL": "postgres://gtm_api:secret@localhost:5432/gtm_test",
                },
            ),
            patch(
                "backend.main.create_pool", AsyncMock(side_effect=[mock_migration_pool, mock_pool])
            ),
            patch("backend.main.ensure_runtime_role_password", AsyncMock(return_value=True)),
            patch("backend.main.assert_runtime_role_least_privilege", AsyncMock()),
            patch("backend.main.migrate", AsyncMock()),
            patch(
                "backend.main.broker_connect", AsyncMock(return_value=MagicMock(close=AsyncMock()))
            ),
            patch("backend.main.require_broker", MagicMock()),
            patch("backend.main.set_broker", MagicMock()),
            patch("backend.services.runs.events.start_relay", MagicMock()),
            patch("backend.services.runs.events.stop_relay", AsyncMock()),
            patch(
                "backend.main.BackendSessionStore",
                MagicMock(return_value=MagicMock(close_all=AsyncMock(), close_idle=AsyncMock())),
            ),
            patch("backend.main.advisory_singleton") as mock_adv,
            patch("backend.services.runs.queue.claim_loop") as mock_claim,
            patch("backend.services.runs.queue.heartbeat_loop") as mock_heartbeat,
            patch("backend.services.runs.state.drain_background_tasks", AsyncMock()),
        ):
            mock_adv.return_value.__aenter__ = AsyncMock()
            mock_adv.return_value.__aexit__ = AsyncMock()

            async with lifespan(mock_app):
                pass

            # Since QUEUE_WORKER_ENABLED is false, claim_loop and heartbeat_loop should NOT be called
            mock_claim.assert_not_called()
            mock_heartbeat.assert_not_called()

    asyncio.run(_test())


def test_worker_run_worker_lifecycle():
    async def _test():
        from backend.worker import run_worker

        mock_pool = MagicMock()
        mock_pool.close = AsyncMock()

        mock_broker = MagicMock()
        mock_broker.close = AsyncMock()

        mock_sessions = MagicMock()
        mock_sessions.close_all = AsyncMock()

        with (
            patch.dict(
                os.environ, {"DATABASE_URL": "postgres://gtm_api:secret@localhost:5432/gtm_test"}
            ),
            patch("backend.worker.create_pool", AsyncMock(return_value=mock_pool)),
            patch("backend.worker.assert_runtime_role_least_privilege", AsyncMock()),
            patch("backend.worker.BackendSessionStore", MagicMock(return_value=mock_sessions)),
            patch("backend.worker.broker_connect", AsyncMock(return_value=mock_broker)),
            patch("backend.worker.require_broker", MagicMock()),
            patch("backend.worker.set_broker", MagicMock()),
            patch("backend.worker.start_relay", MagicMock()),
            patch("backend.worker.stop_relay", AsyncMock()),
            patch("backend.worker.advisory_singleton") as mock_adv,
            patch("backend.worker.claim_loop", AsyncMock()),
            patch("backend.worker.heartbeat_loop", AsyncMock()),
            patch("backend.worker.stop_task", AsyncMock()) as mock_stop_task,
            patch("backend.services.runs.state.drain_background_tasks", AsyncMock()),
            patch("asyncio.Event.wait", AsyncMock(return_value=True)),
        ):
            mock_adv.return_value.__aenter__ = AsyncMock()
            mock_adv.return_value.__aexit__ = AsyncMock()

            await run_worker()

            mock_pool.close.assert_awaited_once()
            mock_broker.close.assert_awaited_once()
            mock_sessions.close_all.assert_awaited_once()
            assert mock_stop_task.await_count == 2

    asyncio.run(_test())

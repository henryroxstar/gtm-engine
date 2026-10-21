"""Unit tests and parity assertions for business data services.

Tests schema correctness, status transition constraints, and dual-runtime data parity.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from backend.routers.admin_sync import AdminSyncPayload, AdminSyncResponse
from backend.services import (
    accounts as accounts_svc,
)
from backend.services import (
    contacts as contacts_svc,
)
from backend.services import (
    content_items as content_svc,
)
from backend.services import (
    outcomes as outcomes_svc,
)
from backend.services import (
    people as people_svc,
)
from backend.services import (
    suppression as suppression_svc,
)
from gtm_core.slugify import slug

DUMMY_WS = "00000000-0000-0000-0000-000000000001"


def test_slug_generation_parity():
    """Verify that company name slugification produces deterministic account slugs."""
    assert slug("Acme Corp, Inc.") == "acme-corp-inc"
    assert slug("DeepMind Technologies") == "deepmind-technologies"
    assert slug("Alpha Beta 123") == "alpha-beta-123"


def test_admin_sync_payload_validation():
    """Verify AdminSyncPayload correctly validates incoming structures."""
    payload = AdminSyncPayload(
        accounts=[
            {
                "account_id": "a-1234567890",
                "company_name": "Acme Corp",
                "slug": "acme-corp",
                "status": "new",
                "tier": "A",
            }
        ],
        contacts=[
            {
                "pool_row_id": "r-000001",
                "account_id": "a-1234567890",
                "email": "test@acme.example.com",
                "first_name": "Jane",
                "last_name": "Doe",
            }
        ],
        suppressions=[
            {
                "email": "optout@example.com",
                "reason": "dnc-optout",
            }
        ],
        people=[
            {
                "person_id": "jane-doe-acme",
                "name": "Jane Doe",
                "status": "lead",
            }
        ],
        outcomes=[
            {
                "channel": "email",
                "outcome": "sent",
                "ref": "step-1",
            }
        ],
        content_items=[
            {
                "item_id": "item-1",
                "platform": "linkedin",
                "status": "planned",
            }
        ],
    )
    assert len(payload.accounts) == 1
    assert len(payload.contacts) == 1
    assert len(payload.suppressions) == 1
    assert len(payload.people) == 1
    assert len(payload.outcomes) == 1
    assert len(payload.content_items) == 1

    resp = AdminSyncResponse(
        status="ok",
        accounts_synced=1,
        contacts_synced=1,
        suppressions_synced=1,
        people_synced=1,
        outcomes_synced=1,
        content_items_synced=1,
    )
    assert resp.status == "ok"
    assert resp.accounts_synced == 1


import asyncio


def test_accounts_upsert_service_mock():
    """Verify accounts_svc.upsert_account correctly prepares SQL parameters."""

    async def _run():
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": "uuid-1",
            "workspace_id": DUMMY_WS,
            "account_id": "a-1234567890",
            "company_name": "Acme Corp",
            "slug": "acme-corp",
            "status": "new",
        }

        mock_pool = MagicMock()

        with patch("backend.services.accounts.workspace_scope") as mock_scope:
            mock_scope.return_value.__aenter__.return_value = mock_conn

            res = await accounts_svc.upsert_account(
                mock_pool,
                DUMMY_WS,
                {
                    "account_id": "a-1234567890",
                    "company_name": "Acme Corp",
                    "slug": "acme-corp",
                    "status": "new",
                    "signals": {"intent": "high"},
                },
            )

            assert res["account_id"] == "a-1234567890"
            assert mock_conn.fetchrow.called
            args, kwargs = mock_conn.fetchrow.call_args
            # Verify JSON serialization of signals
            assert json.loads(args[13]) == {"intent": "high"}

    asyncio.run(_run())


def test_contacts_upsert_service_mock():
    """Verify contacts_svc.upsert_contact maps first/first_name aliases."""

    async def _run():
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "pool_row_id": "r-001",
            "email": "alice@example.com",
        }

        mock_pool = MagicMock()

        with patch("backend.services.contacts.workspace_scope") as mock_scope:
            mock_scope.return_value.__aenter__.return_value = mock_conn

            res = await contacts_svc.upsert_contact(
                mock_pool,
                DUMMY_WS,
                {
                    "pool_row_id": "r-001",
                    "first": "Alice",
                    "last": "Smith",
                    "email": "alice@example.com",
                },
            )
            assert res["pool_row_id"] == "r-001"
            args, _ = mock_conn.fetchrow.call_args
            # args[5] is first_name
            assert args[5] == "Alice"
            assert args[6] == "Smith"

    asyncio.run(_run())


def test_suppression_service_mock():
    """Verify suppression_svc query checks both email and name/domain."""

    async def _run():
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"reason": "dnc-optout"}

        mock_pool = MagicMock()

        with patch("backend.services.suppression.workspace_scope") as mock_scope:
            mock_scope.return_value.__aenter__.return_value = mock_conn

            suppressed, reason = await suppression_svc.is_suppressed(
                mock_pool,
                DUMMY_WS,
                email="test@example.com",
                name="Bob Smith",
                company_domain="example.com",
            )
            assert suppressed is True
            assert reason == "dnc-optout"
            query, *params = mock_conn.fetchrow.call_args[0]
            assert "lower(email) =" in query
            assert "lower(name) =" in query
            assert "test@example.com" in params
            assert "bob smith" in params

    asyncio.run(_run())


def test_people_service_non_downgrade():
    """Verify people_svc preserves higher status when incoming status is lower."""

    async def _run():
        mock_conn = AsyncMock()
        mock_tx = MagicMock()
        mock_tx.__aenter__ = AsyncMock(return_value=None)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=mock_tx)
        # Simulated existing record with status 'replied'
        mock_conn.fetchrow.side_effect = [
            {"status": "replied"},  # existing lookup
            {"person_id": "p-1", "status": "replied"},  # update result
        ]

        mock_pool = MagicMock()

        with patch("backend.services.people.workspace_scope") as mock_scope:
            mock_scope.return_value.__aenter__.return_value = mock_conn

            # Incoming update with lower status 'lead'
            res = await people_svc.upsert_person(
                mock_pool,
                DUMMY_WS,
                {
                    "person_id": "p-1",
                    "status": "lead",
                },
            )
            # Must remain 'replied'
            assert res["status"] == "replied"
            # Verify update SQL used final_status = 'replied'
            update_call = mock_conn.fetchrow.call_args_list[1]
            assert update_call[0][8] == "replied"

    asyncio.run(_run())


def test_outcomes_service_summary():
    """Verify outcomes_svc.summarize_outcomes groups by channel and outcome."""

    async def _run():
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"channel": "email", "outcome": "sent", "total": 100, "events": 100},
            {"channel": "email", "outcome": "reply", "total": 5, "events": 5},
            {"channel": "linkedin", "outcome": "published", "total": 1, "events": 1},
        ]

        mock_pool = MagicMock()

        with patch("backend.services.outcomes.workspace_scope") as mock_scope:
            mock_scope.return_value.__aenter__.return_value = mock_conn

            summary = await outcomes_svc.summarize_outcomes(mock_pool, DUMMY_WS)
            assert summary["email"]["sent"] == 100.0
            assert summary["email"]["reply"] == 5.0
            assert summary["linkedin"]["published"] == 1.0

    asyncio.run(_run())


def test_content_items_service_mock():
    """Verify content_svc.update_content_status sets status."""

    async def _run():
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "item_id": "ci-1",
            "status": "approved",
        }

        mock_pool = MagicMock()

        with patch("backend.services.content_items.workspace_scope") as mock_scope:
            mock_scope.return_value.__aenter__.return_value = mock_conn

            res = await content_svc.update_content_status(mock_pool, DUMMY_WS, "ci-1", "approved")
            assert res["item_id"] == "ci-1"
            assert res["status"] == "approved"

    asyncio.run(_run())

"""Phase 3 — Database Migration & Schema Drift Suite.

1. Migration sequence integrity: checks V001..V030 naming, contiguous sequence, and non-empty content.
2. Migration idempotency: verifies that running migrate(pool) on live_db repeatedly succeeds without error.
3. Schema-to-Pydantic catalog drift: verifies that tables and columns defined in schema migrations
   faithfully support the model and schema expectations in backend/schemas.py.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "backend" / "schema"


def test_migration_files_sequential_order_and_integrity():
    """Verify migration files follow strict naming V{num}__{name}.sql and form a contiguous sequence."""
    migration_files = sorted(SCHEMA_DIR.glob("V*.sql"))
    assert len(migration_files) >= 30, (
        f"Expected at least 30 migrations, found {len(migration_files)}"
    )

    pattern = re.compile(r"^V(\d{3})__([a-z0-9_]+)\.sql$")
    versions = []

    for path in migration_files:
        match = pattern.match(path.name)
        assert match is not None, (
            f"Migration file {path.name} violates naming convention VNNN__description.sql"
        )
        ver = int(match.group(1))
        versions.append(ver)

        content = path.read_text(encoding="utf-8").strip()
        assert len(content) > 0, f"Migration file {path.name} is empty"

    expected_versions = list(range(1, len(versions) + 1))
    assert versions == expected_versions, (
        f"Migration version sequence has gaps or is non-contiguous: {versions}"
    )


def test_migrations_idempotency(live_db):
    """Applying migrate(pool) twice against the live Postgres instance must be idempotent."""
    from backend.database import migrate

    async def _go():
        pool = await asyncpg.create_pool(live_db["admin_dsn"], min_size=1, max_size=2)
        try:
            # Re-run migrate
            await migrate(pool, schema_dir=str(SCHEMA_DIR))

            # Query schema_migrations
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT filename, applied_at FROM schema_migrations ORDER BY filename"
                )
                applied = [r["filename"] for r in rows]
                assert len(applied) >= 30
                assert all(r["applied_at"] is not None for r in rows)
        finally:
            await pool.close()

    asyncio.run(_go())


def test_schema_drift_against_pydantic_schemas(live_db):
    """Assert that core columns expected by Pydantic models in backend/schemas.py exist in Postgres."""
    # Mapping of target table -> expected core columns that API schemas serialize/deserialize
    EXPECTED_COLUMNS: dict[str, set[str]] = {
        "workspaces": {"id", "slug", "display_name"},
        "subscriptions": {"id", "workspace_id", "entitlement", "monthly_cost_cap_usd"},
        "users": {
            "id",
            "email",
            "display_name",
            "password_hash",
            "password_changed_at",
            "sessions_invalid_before",
        },
        "agents": {
            "id",
            "workspace_id",
            "name",
            "profile_name",
            "language",
            "monthly_budget_usd",
            "read_scope",
            "daily_dispatch_cap",
        },
        "runs": {
            "id",
            "workspace_id",
            "profile_name",
            "status",
            "created_at",
            "started_at",
            "completed_at",
            "error",
            "error_code",
            "client_request_id",
            "external_ref",
        },
        "cost_records": {
            "id",
            "workspace_id",
            "profile_name",
            "run_id",
            "stage",
            "model",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "agent_id",
        },
        "run_gates": {
            "run_id",
            "workspace_id",
            "gate",
            "node_id",
            "state",
            "content_sha",
            "opened_at",
            "decided_at",
            "applied_at",
        },
        "run_nodes": {"run_id", "workspace_id", "node_id", "state"},
        "run_blocks": {"run_id", "workspace_id", "block_id", "node_id", "block"},
        "run_artifacts": {
            "id",
            "run_id",
            "workspace_id",
            "rel_path",
            "name",
            "size_bytes",
            "media_type",
            "sha256",
        },
        "webhook_events": {"workspace_id", "provider", "event_id", "run_id", "received_at"},
    }

    async def _go():
        pool = await asyncpg.create_pool(live_db["admin_dsn"], min_size=1, max_size=2)
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    """
                )
                actual_columns: dict[str, set[str]] = {}
                for r in rows:
                    actual_columns.setdefault(r["table_name"], set()).add(r["column_name"])

                for table, expected_cols in EXPECTED_COLUMNS.items():
                    assert table in actual_columns, (
                        f"Expected table {table!r} missing from Postgres schema"
                    )
                    missing_cols = expected_cols - actual_columns[table]
                    assert not missing_cols, f"Table {table!r} is missing columns: {missing_cols}"
        finally:
            await pool.close()

    asyncio.run(_go())


def test_ledger_integrity_on_run_delete(clean_db):
    """V040: fk_unified_metering_log_runs and fk_cost_reservations_runs use ON DELETE SET NULL."""

    async def _go():
        pool = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        try:
            async with pool.acquire() as conn:
                user_id = await conn.fetchval(
                    "INSERT INTO users (email, display_name, password_hash) "
                    "VALUES ('ledger-integrity@example.com', 'Audit Test', 'hash') RETURNING id"
                )
                workspace_id = await conn.fetchval(
                    "INSERT INTO workspaces (slug, display_name, owner_user_id) "
                    "VALUES ('acme-audit', 'Acme Audit', $1) RETURNING id",
                    user_id,
                )
                run_id = await conn.fetchval(
                    "INSERT INTO runs (workspace_id, profile_name, prompt) "
                    "VALUES ($1, 'acme', 'test prompt') RETURNING id",
                    workspace_id,
                )
                await conn.execute(
                    "INSERT INTO unified_metering_log (run_id, workspace_id, runtime, tool_name, cost_credits) "
                    "VALUES ($1, $2, 'backend', 'test_tool', 10.0)",
                    run_id,
                    workspace_id,
                )
                await conn.execute(
                    "INSERT INTO cost_reservations (run_id, workspace_id, state, estimated_credits) "
                    "VALUES ($1, $2, 'open', 10.0)",
                    run_id,
                    workspace_id,
                )

                await conn.execute("DELETE FROM runs WHERE id = $1", run_id)

                remaining_uml = await conn.fetchrow(
                    "SELECT run_id FROM unified_metering_log WHERE workspace_id = $1", workspace_id
                )
                assert remaining_uml is not None
                assert remaining_uml["run_id"] is None

                remaining_res = await conn.fetchrow(
                    "SELECT run_id FROM cost_reservations WHERE workspace_id = $1", workspace_id
                )
                assert remaining_res is not None
                assert remaining_res["run_id"] is None
        finally:
            await pool.close()

    asyncio.run(_go())

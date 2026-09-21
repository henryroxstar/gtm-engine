"""Async Postgres connection pool with workspace-scoped RLS enforcement.

Every query that touches tenant data MUST run inside workspace_scope(), which
sets the session-local app.current_workspace_id before any SQL. This is the
server-side enforcement of the RLS policies in backend/schema/V004__rls_policies.sql.

Usage:
    pool = await create_pool()
    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch("SELECT * FROM profiles")
        # RLS: only rows matching workspace_id are visible

The pool is created once at app startup (lifespan) and stored on app.state.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import asyncpg

# workspace_scope + assert_runtime_role_least_privilege now live in gtm_core.db so the
# MCP runtime (mcp_server/) can share them without importing the backend package.
# Re-exported here so every existing `from backend.database import ...` site is unchanged.
from gtm_core.db import (  # noqa: F401 — re-export for back-compat
    assert_runtime_role_least_privilege,
    workspace_scope,
)


async def create_pool(
    dsn: str | None = None,
    *,
    min_size: int = 2,
    max_size: int = 10,
    application_name: str = "gtm-backend",
) -> asyncpg.Pool:
    """Create an asyncpg connection pool.

    ``dsn`` defaults to the ``DATABASE_URL`` env var — the runtime, **non-owner**
    role (``gtm_api``). backend/main.py passes an explicit DSN for the short-lived
    **migration** pool (the owner role, ``gtm_app``) so DDL and request traffic use
    different Postgres roles: FORCE RLS (see V009) only constrains a role that is
    neither superuser nor table owner, so the runtime role must not be the owner.
    """
    dsn_str = dsn or os.environ["DATABASE_URL"]
    statement_cache_size = int(
        os.environ.get(
            "PG_STATEMENT_CACHE_SIZE",
            "0" if ":6432" in dsn_str else "100",
        )
    )
    pool = await asyncpg.create_pool(
        dsn_str,
        min_size=min_size,
        max_size=max_size,
        command_timeout=30,
        statement_cache_size=statement_cache_size,
        server_settings={
            "application_name": application_name,
            "search_path": "public",
        },
    )
    return pool


async def ensure_runtime_role_password(
    migration_pool: asyncpg.Pool,
    *,
    role: str = "gtm_api",
    env_var: str = "POSTGRES_GTMAPI_PASSWORD",
) -> bool:
    """Set the non-owner runtime role's password from Doppler, if configured.

    V009 creates ``gtm_api`` WITHOUT a password — secrets never live in migration
    files. In production the password comes from ``POSTGRES_GTMAPI_PASSWORD`` and
    is applied here, on the owner (migration) pool, before the runtime pool
    connects. Returns True if a password was applied; False (no-op) when the env
    var is unset, in which case the caller falls back to a single pool (local dev).

    Safety: the password is passed as a bind parameter (never interpolated into
    query text, so it never reaches ``pg_stat_activity``) into a transaction-local
    GUC, then applied via server-side ``format(..., %L)`` quoting so a value with
    quotes cannot break out. The GUC is transaction-scoped and clears on commit.
    """
    password = os.environ.get(env_var)
    if not password:
        return False
    if not role.isidentifier():  # role is an internal constant, never user input
        raise ValueError(f"unsafe role name: {role!r}")
    async with migration_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('gtm.role_pw', $1, true)", password)
            await conn.execute(
                "DO $$ BEGIN "
                f"EXECUTE format('ALTER ROLE %I PASSWORD %L', '{role}', "
                "current_setting('gtm.role_pw')); END $$;"
            )
    return True


@asynccontextmanager
async def advisory_singleton(pool: asyncpg.Pool, key: int):
    """Hold a session-level Postgres advisory lock for the block's duration.

    A5: with ``BACKEND_WORKERS > 1`` each forked uvicorn worker runs the whole lifespan,
    so a step that must happen ONCE per deployment (migrations; gate reconciliation) would
    otherwise happen N times — concurrent DDL can deadlock, and N reconciliations would
    re-dispatch the same stranded run N times. Losers BLOCK here rather than skipping, so
    they proceed only once the winner is finished and find the work already done; skipping
    would let a worker start serving against a half-migrated schema.

    Session-level (not ``_xact_``) because the block spans several transactions, and taken
    on a dedicated connection so it is released with that connection even if the process
    is killed mid-block.
    """
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", key)
        try:
            yield conn
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)", key)
            except Exception:  # noqa: BLE001 — the lock dies with the connection anyway
                pass  # nosec B110


async def migrate(pool: asyncpg.Pool, schema_dir: str | None = None) -> None:
    """Apply all pending schema migrations from backend/schema/V*.sql files.

    Idempotent: creates a schema_migrations tracking table on first run and
    skips any migration that has already been applied.
    """
    from pathlib import Path

    if schema_dir is None:
        schema_dir = str(Path(__file__).resolve().parent / "schema")

    sql_files = sorted(Path(schema_dir).glob("V*.sql"))
    if not sql_files:
        raise RuntimeError(f"No migration files found in {schema_dir!r}")

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        for path in sql_files:
            name = path.name
            existing = await conn.fetchval(
                "SELECT filename FROM schema_migrations WHERE filename = $1", name
            )
            if existing:
                continue
            sql = path.read_text(encoding="utf-8")
            # A migration containing a statement that CANNOT run inside a transaction
            # (CREATE INDEX CONCURRENTLY, ALTER TYPE ... ADD VALUE, VACUUM) opts out by
            # putting `-- gtm:no-transaction` in the file. It must then be a SINGLE
            # autocommittable statement; tracking is recorded separately afterward, so a
            # crash between apply and track can leave it applied-but-untracked — the
            # author accepts that trade for CONCURRENTLY (make the statement IF NOT
            # EXISTS / idempotent so a re-run is harmless).
            if "gtm:no-transaction" in sql:
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations(filename) VALUES($1)", name)
            else:
                # Apply the migration and record it atomically. Previously these were
                # two separate autocommits: a crash in between left the migration
                # applied-but-untracked, wedging the next boot when a non-idempotent
                # statement (e.g. CREATE POLICY) re-ran. One transaction fixes that.
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute("INSERT INTO schema_migrations(filename) VALUES($1)", name)
            print(f"  applied {name}")

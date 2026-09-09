"""Shared async-Postgres helpers used by BOTH network-facing runtimes.

The backend (``backend/``) and the paid MCP developer surface (``mcp_server/``) each
run RLS-subject as the non-owner ``gtm_api`` role (see
``backend/schema/V009__rls_force_and_role.sql``). Two helpers are common to both and
live here so the MCP runtime never has to import the ``backend`` package:

  - :func:`workspace_scope` — acquire a connection and activate RLS for one workspace
    (``SET LOCAL app.current_workspace_id``) inside a transaction.
  - :func:`assert_runtime_role_least_privilege` — boot guard that fails fast if the
    runtime pool connected as a superuser / BYPASSRLS role (a mis-deploy that makes
    RLS inert).

Import-light on purpose (mirrors :mod:`gtm_core.metering`): the pool/connection are
duck-typed (any object exposing ``acquire()`` / ``transaction()`` / ``execute()`` /
``fetchrow()``), so there is no runtime ``asyncpg`` import — only a TYPE_CHECKING one
for the annotations. ``backend/database.py`` re-exports both names for back-compat.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg


@asynccontextmanager
async def workspace_scope(
    pool: asyncpg.Pool,
    workspace_id: str,
) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection and activate RLS for one workspace.

    Sets SET LOCAL app.current_workspace_id inside a transaction so RLS
    policies in V004 automatically scope every query to this workspace.
    The SET LOCAL resets automatically when the transaction ends — there is
    no risk of one workspace's ID leaking into another request's connection.

    Raises ValueError if workspace_id is empty or obviously unsafe.
    """
    if not workspace_id or "/" in workspace_id or "\x00" in workspace_id:
        raise ValueError(f"unsafe workspace_id: {workspace_id!r}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_workspace_id', $1, true)",
                workspace_id,
            )
            yield conn


async def assert_runtime_role_least_privilege(pool: Any) -> None:
    """Fail fast if the runtime pool connected as a superuser / BYPASSRLS role.

    FORCE RLS (V009) only constrains a role that is neither a superuser, a table
    owner, nor ``BYPASSRLS``. This guard turns a mis-deploy (``DATABASE_URL``
    still pointed at the ``gtm_app`` owner) from a silent cross-tenant data leak
    into a loud boot failure. Hard error in production; warning locally so
    single-role dev still boots.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT current_user AS role,"
            "  (SELECT rolsuper     FROM pg_roles WHERE rolname = current_user) AS super,"
            "  (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypassrls"
        )
    if row and (row["super"] or row["bypassrls"]):
        msg = (
            f"Runtime DB role {row['role']!r} is superuser={row['super']} "
            f"bypassrls={row['bypassrls']} — RLS is INERT. Point DATABASE_URL at the "
            f"non-owner gtm_api role (see backend/schema/V009__rls_force_and_role.sql)."
        )
        if os.getenv("ENV", "production") == "production":
            raise RuntimeError(msg)
        import warnings

        warnings.warn(msg, stacklevel=2)

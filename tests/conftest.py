"""Repo-wide pytest guard: don't let a tests/ package shadow an installed package.

A ``tests/mcp/`` package used to live here. Under pytest's default ``prepend`` import
mode, collecting any test inside a tests-tree package (e.g. ``tests/backend``, which
ships ``__init__.py``) puts this ``tests/`` directory on ``sys.path``. With ``tests/``
on the path, a bare ``import mcp`` bound ``tests/mcp/`` — a stub with no
``types``/``server`` submodules — instead of the pip-installed ``mcp``. The real
Claude Agent SDK does ``from mcp.types import ToolAnnotations`` at import time and the
``agent.mcp.*`` workers do ``from mcp.server.fastmcp import FastMCP``, so both died
with ``ModuleNotFoundError`` — but only under collection orders that put ``tests/`` on
the path before ``mcp`` was first imported, which made the suite order-dependent.

The dead ``tests/mcp/`` stub has been removed (its tests live in
``tests/test_deck_mcp_server.py`` at the top level). This guard runs at collection and
fails the whole suite loudly if anyone re-creates that shadow, so the order-dependent
breakage cannot regress silently.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

# A ``tests/mcp/__init__.py`` would shadow the installed ``mcp`` on sys.path. Keep
# deck-MCP tests at tests/test_deck_mcp_server.py (top level) instead.
_mcp_shadow = Path(__file__).resolve().parent / "mcp" / "__init__.py"
assert not _mcp_shadow.exists(), (
    f"{_mcp_shadow} re-introduces a `tests/mcp` package that shadows the installed "
    "`mcp` package whenever pytest puts tests/ on sys.path (breaking the Claude Agent "
    "SDK's `from mcp.types import ...`). Move those tests to tests/test_deck_mcp_server.py."
)


# ── Live-DB test tier fixtures (Phase 1 — tenant isolation) ──────────────────────
# These back the ``@pytest.mark.dbtest`` suites (tests/backend/test_rls_live.py and
# tests/mcp_server/test_mcp_rls_live.py). They connect to a REAL Postgres so FORCE RLS
# + the non-owner ``gtm_api`` role + the SECURITY DEFINER auth bootstraps are exercised
# the way production runs them — the rest of the suite mocks ``workspace_scope``, which
# by construction cannot prove RLS actually isolates tenants. Defined at the tests/ root
# so BOTH the backend and mcp_server dbtest packages share one session-scoped ``live_db``.
#
# Opt-in by env: set ``GTM_TEST_PG_ADMIN_DSN`` to a superuser/owner DSN (the ``backend-db``
# job in ci.yml provisions a ``postgres:16`` service and sets it). Without it every dbtest
# SKIPS, so the fast mocked tier and the stdlib-only CI ``tests`` job are untouched.
# ``asyncpg`` is imported lazily (importorskip) for the same reason.

# Resolve ``import backend.database`` / ``import mcp_server.*`` without a full editable
# install — the live-DB CI job installs only pytest + asyncpg, and those code paths need
# no Agent SDK.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_ADMIN_ENV = "GTM_TEST_PG_ADMIN_DSN"
_API_PASSWORD = "gtm_api_test_pw"  # nosec B105 — throwaway test-role password, not a secret
_TENANT_TABLES = (
    "workspaces",
    "workspace_members",
    "subscriptions",
    "profiles",
    "api_keys",
    "encrypted_credentials",
    "cost_records",
    "runs",
    "mcp_calls",
    "push_tokens",
    "entitlement_sync_events",
    "cost_reservations",
)


def _swap_userinfo(dsn: str, user: str, password: str) -> str:
    """Rewrite a DSN's user:password, preserving host/port/db."""
    p = urlsplit(dsn)
    host = p.hostname or "localhost"
    port = f":{p.port}" if p.port else ""
    return urlunsplit((p.scheme, f"{user}:{password}@{host}{port}", p.path, p.query, p.fragment))


@pytest.fixture(scope="session")
def live_db() -> dict[str, str]:
    """Migrate an ephemeral Postgres once; return the owner + gtm_api DSNs.

    Skips the entire dbtest tier unless ``GTM_TEST_PG_ADMIN_DSN`` is set.
    """
    admin_dsn = os.getenv(_ADMIN_ENV)
    if not admin_dsn:
        pytest.skip(f"live-DB tier: set {_ADMIN_ENV} (owner-role DSN) to run")
    asyncpg = pytest.importorskip("asyncpg")

    os.environ["POSTGRES_GTMAPI_PASSWORD"] = _API_PASSWORD
    os.environ.setdefault("ENV", "test")
    from backend.database import ensure_runtime_role_password, migrate

    async def _setup() -> None:
        pool = await asyncpg.create_pool(admin_dsn, min_size=1, max_size=2)
        try:
            await migrate(pool)
            await ensure_runtime_role_password(pool)
        finally:
            await pool.close()

    asyncio.run(_setup())
    return {
        "admin_dsn": admin_dsn,
        "api_dsn": _swap_userinfo(admin_dsn, "gtm_api", _API_PASSWORD),
    }


@pytest.fixture
def clean_db(live_db: dict[str, str]) -> dict[str, str]:
    """Function-scoped: truncate all user + tenant rows before each dbtest."""
    asyncpg = pytest.importorskip("asyncpg")

    async def _truncate() -> None:
        pool = await asyncpg.create_pool(live_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with pool.acquire() as c:
                await c.execute(
                    "TRUNCATE users, " + ", ".join(_TENANT_TABLES) + " RESTART IDENTITY CASCADE"
                )
        finally:
            await pool.close()

    asyncio.run(_truncate())
    return live_db

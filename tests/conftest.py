"""Repo-wide pytest guard: don't let a tests/ package shadow a real package.

A ``tests/mcp/`` package used to live here. Under pytest's default ``prepend`` import
mode, collecting any test inside a tests-tree package (i.e. any ``tests/<x>/`` that
ships ``__init__.py``) puts this ``tests/`` directory itself on ``sys.path``. With
``tests/`` on the path, a bare ``import mcp`` bound ``tests/mcp/`` — a stub with no
``types``/``server`` submodules — instead of the pip-installed ``mcp``. The real
Claude Agent SDK does ``from mcp.types import ToolAnnotations`` at import time and the
``agent.mcp.*`` workers do ``from mcp.server.fastmcp import FastMCP``, so both died
with ``ModuleNotFoundError`` — but only under collection orders that put ``tests/`` on
the path before ``mcp`` was first imported, which made the suite order-dependent.

The dead ``tests/mcp/`` stub has been removed (its tests live in
``tests/test_deck_mcp_server.py`` at the top level). This guard generalizes the single
hardcoded check that incident left behind: it runs at collection and fails the whole
suite loudly if a ``tests/<x>/__init__.py`` reappears for ANY ``<x>`` that names a real
top-level package this repo or its venv provides — ``mcp`` (the incident), or the
repo's own ``agent``/``backend``/``cockpit``/``gtm_core``/``mcp_server`` (a directory
under ``tests/`` sharing one of those names is a live shadow waiting for someone to add
an ``__init__.py`` to it, same mechanism, different victim). Today only
``tests/journey/`` has one, and ``journey`` isn't in the list, so this guard is
currently a no-op — it exists to stay that way.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

# Hand-maintained, not derived: a derived list (e.g. "every dir at repo root with an
# __init__.py") would need the same sys.path-timing care this guard exists to sidestep,
# and the point is to be trivially auditable, not clever. Extend when a new top-level
# package is added to the repo (or a new PyPI dependency the SDK/workers import bare).
_SHADOWABLE_PACKAGE_NAMES = frozenset(
    {"agent", "backend", "cockpit", "gtm_core", "mcp_server", "mcp", "plugin", "scripts"}
)


def _shadowing_init_files(tests_dir: Path) -> list[Path]:
    """``tests/<x>/__init__.py`` files where ``<x>`` collides with a real package name."""
    return sorted(
        init
        for init in tests_dir.glob("*/__init__.py")
        if init.parent.name in _SHADOWABLE_PACKAGE_NAMES
    )


_violations = _shadowing_init_files(Path(__file__).resolve().parent)
assert not _violations, (
    f"{[str(p) for p in _violations]} would shadow a real top-level package once pytest "
    "puts tests/ on sys.path under the default 'prepend' import mode (see this module's "
    "docstring for the tests/mcp/ incident this generalizes). Move these tests to a "
    "directory name that doesn't collide with agent/backend/cockpit/gtm_core/mcp_server/"
    "mcp/plugin/scripts, or make the directory a namespace package (no __init__.py)."
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

# ── Private-tree-only tests (the OSS carve is a genuinely different tree) ────────
# `scripts/oss-export.sh` carves a public cut of this repo: only `profiles/_template`
# ships, `scripts/` is reduced to `bootstrap.sh`, `.claude/` and `docs/prds/` are
# withheld, and `docker-compose.yml` is swapped for the `oss/overlays/` version. Some
# contract tests assert over a census that only the private tree has — "at least three
# committed tenant profiles", "some profile activates the `inbound` pack", "the two
# SHIPPING_DOCS arrays agree" (one of them lives in the un-carved oss-export.sh). Those
# assertions are correct HERE and vacuous THERE, and the export runs the whole suite
# inside the carve as a release gate, so an unguarded one fails the export rather than
# finding a bug. Mark such a test `@pytest.mark.private_tree`.
#
# The detector is the un-carved export script itself — one unambiguous file, and the
# same signal a public clone would see, because a public clone IS the carve. It is
# deliberately NOT "count the profiles": that would silently start skipping in the
# private tree the moment a profile was renamed.
#
# This marker is for a test whose PREMISE the carve removes. It is never the fix for a
# test that fails in the carve because the shipped artifact is wrong — an overlay that
# has drifted behind the file it shadows is a real public defect, and skipping it there
# is how the drift ships.
_OSS_EXPORT_SCRIPT = _REPO_ROOT / "scripts" / "oss-export.sh"
IS_PUBLIC_CUT = not _OSS_EXPORT_SCRIPT.exists()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip `private_tree`-marked tests when running inside a public carve."""
    if not IS_PUBLIC_CUT:
        return
    skip = pytest.mark.skip(
        reason="private-tree-only: this assertion's premise does not exist in the OSS carve"
    )
    for item in items:
        if "private_tree" in item.keywords:
            item.add_marker(skip)


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
    "unified_metering_log",
    "push_tokens",
    "entitlement_sync_events",
    "cost_reservations",
    "run_nodes",
    "run_blocks",
    "run_artifacts",
    "agents",
    "run_gates",
    "run_events",
    "webhook_events",
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

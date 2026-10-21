"""RT-14 — POST /v1/profiles must validate the name and report the stored is_default.

Two bugs, re-derived against backend/routers/profiles.py:add_profile:

1. ``ActivateProfileRequest.profile_name`` was an unconstrained ``str`` while the DB
   enforces ``CHECK (profile_name ~ '^[a-z0-9][a-z0-9_-]*$')``
   (backend/schema/V001__data_spine.sql). A bad name reached Postgres and came back as
   an ``asyncpg.CheckViolationError`` — uncaught, so ``unhandled_exception_handler``
   turned it into a 500 ``internal_error`` envelope instead of a 422 the client could
   act on.
2. ``INSERT ... ON CONFLICT DO NOTHING`` always returned ``ProfileResponse(...,
   is_default=False)`` regardless of what the existing row actually is — wrong
   whenever the conflicting row is the workspace's default.

The fake ``workspace_scope``/connection below mirrors the DB's own CHECK constraint
in ``execute`` (raising the same ``asyncpg`` error Postgres would) so these tests
exercise the real failure shape, not a stand-in for it. No pytest-asyncio: the async
router body runs under Starlette's own TestClient event loop, exactly as the live
app runs it.
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.deps import require_auth
from backend.errors import register_error_handlers
from backend.routers import profiles as profiles_router
from gtm_core.capabilities import Entitlement

_WS = uuid.UUID("44444444-4444-4444-4444-444444444444")
_DB_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _FakeConn:
    """Mirrors the profiles table: an INSERT with a name violating the DB's CHECK
    constraint raises exactly what asyncpg would; fetchval answers the is_default
    read-back keyed off which name is already the workspace's default."""

    def __init__(self, scope: _FakeScope) -> None:
        self._scope = scope

    async def execute(self, sql, *args):
        self._scope.statements.append((" ".join(sql.split()), args))
        if "INSERT INTO profiles" in sql:
            profile_name = args[-1]
            if not _DB_PROFILE_NAME_RE.match(profile_name):
                raise asyncpg.CheckViolationError(
                    'new row for relation "profiles" violates check constraint '
                    '"profiles_profile_name_check"'
                )
        return "OK"

    async def fetchval(self, sql, *args):
        self._scope.statements.append((" ".join(sql.split()), args))
        profile_name = args[-1]
        return profile_name == self._scope.existing_default

    async def fetch(self, sql, *args):
        self._scope.statements.append((" ".join(sql.split()), args))
        return []


class _FakeScope:
    """Stands in for ``workspace_scope`` — no real pool/transaction, just records."""

    def __init__(self, existing_default: str | None) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.existing_default = existing_default

    @asynccontextmanager
    async def __call__(self, pool, workspace_id):
        yield _FakeConn(self)


@pytest.fixture
def scope() -> _FakeScope:
    # "acme" is already registered as this workspace's default profile.
    return _FakeScope(existing_default="acme")


@pytest.fixture
def client(scope, monkeypatch) -> TestClient:
    monkeypatch.setattr(profiles_router, "workspace_scope", scope)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(profiles_router.router, prefix="/v1")
    ctx = SimpleNamespace(
        user_id=str(uuid.uuid4()), workspace_id=str(_WS), entitlement=Entitlement.PRO
    )
    app.dependency_overrides[require_auth] = lambda: ctx
    app.state.pool = object()
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("bad_name", ["Bad Name", "-lead", "../x", ""])
def test_invalid_profile_name_is_a_422_before_any_sql(client, scope, bad_name):
    """A name violating the DB's CHECK constraint must be refused by request
    validation — never reach SQL, and never surface as a bare 500."""
    resp = client.post("/v1/profiles", json={"profile_name": bad_name})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"
    assert scope.statements == [], "an invalid name must never reach the database"


def test_duplicate_of_the_default_reports_is_default_true(client, scope):
    """Re-adding the workspace's existing default profile is a no-op INSERT, but the
    response must reflect the row's actual is_default — not a hardcoded False."""
    resp = client.post("/v1/profiles", json={"profile_name": "acme"})
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"profile_name": "acme", "is_default": True}


def test_new_profile_in_a_workspace_with_a_default_reports_is_default_false(client, scope):
    """Positive control: a genuinely new, non-default profile name still reports
    is_default: false — the fix must not flip this the other way."""
    resp = client.post("/v1/profiles", json={"profile_name": "widgetco"})
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"profile_name": "widgetco", "is_default": False}

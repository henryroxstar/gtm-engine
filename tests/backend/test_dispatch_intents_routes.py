"""The two dispatch-intent routes (F4-10): user-only, append-only, and honest about what they refuse.

The store behind them is the REAL file-backed one on tmp_path via a thin async adapter (the live Postgres
store has its own dbtest file), so the route logic — validation, 404/422 shapes, the user-only lane — is
exercised end to end without a database.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import dispatch_intents as di_router  # noqa: E402
from gtm_core.capabilities import Entitlement  # noqa: E402
from gtm_core.dispatch_intents import FileIntentStore  # noqa: E402
from gtm_core.ledgers import Ledgers  # noqa: E402

WS = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = FileIntentStore(Ledgers(SimpleNamespace(content_root=tmp_path), "example"))
    monkeypatch.setattr(di_router, "_store", lambda request, ws: store)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(di_router.router, prefix="/v1")
    app.dependency_overrides[require_auth] = lambda: WorkspaceCtx(
        user_id="u1", workspace_id=WS, entitlement=Entitlement.FREE
    )
    app.state.store = store
    return TestClient(app), store


def _unknown_batch(store, n=3):
    import asyncio

    emails = [f"p{i}@acme.example" for i in range(n)]
    c = asyncio.run(store.claim("seq-1", emails, tool="import"))
    asyncio.run(store.record_outcome(c.batch_id, "unknown"))
    return c.batch_id, emails


def test_list_shows_only_what_is_waiting_for_a_human(client):
    http, store = client
    assert http.get("/v1/dispatch-intents?state=unknown").json() == []
    bid, _ = _unknown_batch(store)
    rows = http.get("/v1/dispatch-intents?state=unknown").json()
    assert len(rows) == 1 and rows[0]["batch_id"] == bid
    assert (
        rows[0]["recipients"] == 3 and rows[0]["state"] == "unknown" and rows[0]["scope"] == "seq-1"
    )
    assert http.get("/v1/dispatch-intents?state=bogus").status_code == 422


@pytest.mark.parametrize(
    "body, expect",
    [
        ({"resolution": "sent"}, {"sent": 3, "released": 0}),
        ({"resolution": "not_sent"}, {"sent": 0, "released": 3}),
        (
            {
                "resolution": "sent_recipients",
                "sent_recipients": ["P0@Acme.Example", "p1@acme.example"],
            },
            {"sent": 2, "released": 1},
        ),
    ],
)
def test_resolve_settles_the_batch_and_it_leaves_the_list(client, body, expect):
    http, store = client
    bid, _ = _unknown_batch(store)
    r = http.post(f"/v1/dispatch-intents/{bid}/resolve", json=body)
    assert r.status_code == 200 and r.json() == expect
    assert http.get("/v1/dispatch-intents").json() == []


def test_resolve_refuses_malformed_requests_in_the_standard_error_shape(client):
    http, store = client
    bid, _ = _unknown_batch(store)
    no_list = http.post(
        f"/v1/dispatch-intents/{bid}/resolve", json={"resolution": "sent_recipients"}
    )
    assert (
        no_list.status_code == 422
        and no_list.json()["detail"]["code"] == "sent_recipients_required"
    )
    stray = http.post(
        f"/v1/dispatch-intents/{bid}/resolve",
        json={"resolution": "sent", "sent_recipients": ["a@b.example"]},
    )
    assert (
        stray.status_code == 422 and stray.json()["detail"]["code"] == "sent_recipients_unexpected"
    )
    assert (
        http.post(f"/v1/dispatch-intents/{bid}/resolve", json={"resolution": "maybe"}).status_code
        == 422
    )
    # nothing above changed anything
    assert len(http.get("/v1/dispatch-intents").json()) == 1


def test_an_unknown_or_malformed_batch_id_is_a_404_not_a_500(client):
    http, _ = client
    for bad in ("b-000000000000", "nope", "b-ZZZZZZZZZZZZ"):
        r = http.post(f"/v1/dispatch-intents/{bad}/resolve", json={"resolution": "sent"})
        assert r.status_code == 404 and r.json()["detail"]["code"] == "batch_not_found"


def test_f4_10_the_routes_are_user_only_not_on_the_service_principal_allowlist():
    import ast
    from pathlib import Path

    src = Path(di_router.__file__).read_text()
    tree = ast.parse(src)
    deps = {
        n.args[0].id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", "") == "Depends"
        and n.args
        and hasattr(n.args[0], "id")
    }
    assert deps == {
        "require_auth"
    }  # never require_principal: a machine must not clear its own unknown


def test_an_sk_service_token_is_refused_401_by_the_real_auth_dependency():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(di_router.router, prefix="/v1")
    c = TestClient(app)
    r = c.get("/v1/dispatch-intents", headers={"Authorization": "Bearer sk-not-a-jwt-service-key"})
    assert r.status_code == 401


def test_a_real_pool_gets_the_postgres_store_and_a_pool_less_caller_falls_back_to_the_file_ledger():
    from backend.services.dispatch_intents import PgIntentStore, intent_store_for

    class Pool:
        async def acquire(self):  # pragma: no cover - only its presence matters
            ...

    assert isinstance(intent_store_for(Pool(), WS), PgIntentStore)
    assert intent_store_for(object(), WS) is None


def test_the_route_stamps_the_authenticated_user_as_the_actor(client):
    http, store = client
    seen = {}
    real = store.resolve

    async def spy(batch, resolution, **kw):
        seen.update(kw)
        return await real(batch, resolution, **kw)

    store.resolve = spy  # type: ignore[method-assign]
    bid, _ = _unknown_batch(store)
    assert (
        http.post(
            f"/v1/dispatch-intents/{bid}/resolve", json={"resolution": "not_sent"}
        ).status_code
        == 200
    )
    assert seen.get("actor") == "user:u1"

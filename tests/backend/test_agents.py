"""A4 — agent entity: CRUD narrowing (write-time), re-derivation at use (drift),
archive lifecycle, default-agent bootstrap, budget narrowing, and the agent_id
partition on runs/ledger/packs reads.

DB is an in-memory stateful fake (AgentsDb) emulating the exact SQL the routers
issue — ON CONFLICT semantics included — so the race/idempotency shapes are
testable without Postgres (the RLS/live tier covers the real engine).
Convention: no pytest-asyncio; TestClient drives the routes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import agents as agents_mod  # noqa: E402
from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import agents as agents_router  # noqa: E402
from backend.routers import api_keys as api_keys_router  # noqa: E402
from backend.routers import ledger as ledger_router  # noqa: E402
from backend.routers import onboard as onboard_router  # noqa: E402
from backend.routers import packs as packs_router  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    REPO,  # noqa: E402
    SCOPE_MODULES,
)
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402

with (REPO / "schemas" / "agent.schema.json").open() as _f:
    AGENT_SCHEMA = json.load(_f)

from tests.contracts.minijsonschema import validate as schema_validate  # noqa: E402


class AgentsDb:
    """agents + profiles + subscriptions + cost_records + runs, keyed to the
    routers' SQL. Unknown SQL raises — a new query must be modelled on purpose."""

    def __init__(self) -> None:
        self.agents: dict[str, dict] = {}
        self.profiles: list[str] = [PROFILE]
        self.cap: float | None = 50.0
        self.cost_rows: list[dict] = []
        self.runs: list[tuple] = []
        self._seq = 0

    # helpers -----------------------------------------------------------------
    def add_agent(self, **over) -> dict:
        self._seq += 1
        row = {
            "agent_id": str(uuid.uuid4()),
            "workspace_id": over.get("workspace_id", str(uuid.uuid4())),
            "name": f"agent-{self._seq}",
            "profile_name": PROFILE,
            "packs": None,
            "language": None,
            "monthly_budget_usd": None,
            "status": "active",
            "is_default": False,
            "created_at": f"2026-08-09T00:00:0{self._seq % 10}+00:00",
            "updated_at": "2026-08-09T00:00:00+00:00",
        }
        row.update(over)
        self.agents[row["agent_id"]] = row
        return row

    def _live_name_taken(self, ws: str, name: str, exclude: str | None = None) -> bool:
        return any(
            a["workspace_id"] == ws
            and a["name"] == name
            and a["status"] != "archived"
            and a["agent_id"] != exclude
            for a in self.agents.values()
        )

    def _default_of(self, ws: str) -> dict | None:
        return next(
            (a for a in self.agents.values() if a["workspace_id"] == ws and a["is_default"]),
            None,
        )

    # asyncpg-ish surface -----------------------------------------------------
    async def fetchval(self, sql: str, *args):
        if "SELECT 1 FROM profiles" in sql:
            return 1 if args[1] in self.profiles else None
        if "SELECT profile_name FROM profiles" in sql:
            return self.profiles[0] if self.profiles else None
        if "SELECT monthly_cost_cap_usd FROM subscriptions" in sql:
            return self.cap
        if "SELECT id::text FROM agents" in sql and "is_default" in sql:
            d = self._default_of(args[0])
            return d["agent_id"] if d else None
        if "SELECT 1 FROM agents" in sql:
            exclude = args[2] if len(args) > 2 else None
            return 1 if self._live_name_taken(args[0], args[1], exclude) else None
        if "SUM(cost_usd)" in sql and "cost_records" in sql:
            return sum(r["cost_usd"] for r in self.cost_rows if r.get("agent_id") == args[1])
        if "count(*) FROM runs" in sql:  # A5 admission cap, counted across workers
            return len([r for r in self.runs if str(r[1]) == str(args[0])])
        if sql.strip().startswith("INSERT INTO runs") or (
            "SELECT id::text FROM runs WHERE workspace_id" in sql and "client_request_id" in sql
        ):
            return self._runs_fetchval(sql, args)
        raise AssertionError(f"unexpected fetchval: {sql}")

    def _find_run_by_client_request_id(self, ws, client_request_id):
        return next(
            (r for r in self.runs if str(r[1]) == str(ws) and r[7] == client_request_id),
            None,
        )

    def _runs_fetchval(self, sql: str, args):
        """RT-04: models the real ``INSERT ... ON CONFLICT (workspace_id,
        client_request_id) WHERE client_request_id IS NOT NULL DO NOTHING RETURNING
        id::text`` (a non-null id already used by another row in this workspace
        conflicts — the caller falls back to the SELECT branch below; every NULL id
        inserts freely, since NULL never conflicts with NULL) and its SELECT-by-id
        fallback."""
        if sql.strip().startswith("INSERT INTO runs"):
            run_id, ws, *_rest, client_request_id = args
            if client_request_id is not None and self._find_run_by_client_request_id(
                ws, client_request_id
            ):
                return None
            self.runs.append(args)
            return run_id
        ws, client_request_id = args
        existing = self._find_run_by_client_request_id(ws, client_request_id)
        return existing[0] if existing is not None else None

    async def fetchrow(self, sql: str, *args):
        if "AS cap" in sql and "AS spent" in sql:
            # RL-08: gtm_core.metering.PgSink.cap_and_spent — acheck_budget's own query
            # shape, now also read synchronously at admission time (admits(), before
            # insert_run_row). Same (cap, month-to-date spent) shape the ledger's usage
            # summary reads too. Checked BEFORE the plainer monthly_cost_cap_usd branch
            # below, since this query's text is also a superset match for it.
            if self.cap is None:
                return None
            return {"cap": self.cap, "spent": sum(r["cost_usd"] for r in self.cost_rows)}
        if "monthly_cost_cap_usd" in sql and "subscriptions" in sql:
            return {"monthly_cost_cap_usd": self.cap} if self.cap is not None else None
        if sql.strip().startswith("INSERT INTO agents"):
            ws, name, profile, packs, language, budget = args
            return self.add_agent(
                workspace_id=ws,
                name=name,
                profile_name=profile,
                packs=list(packs) if packs is not None else None,
                language=language,
                monthly_budget_usd=budget,
            )
        if "FROM agents WHERE id" in sql:
            row = self.agents.get(args[0])
            return row if row and row["workspace_id"] == args[1] else None
        if sql.strip().startswith("UPDATE agents SET"):
            sets = re.findall(r"(\w+) = \$(\d+)", sql.split("WHERE")[0])
            row = self.agents.get(args[-2])
            for col, idx in sets:
                row[col] = args[int(idx) - 1]
            return row
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql: str, *args):
        if "FROM agents WHERE workspace_id" in sql:
            rows = [a for a in self.agents.values() if a["workspace_id"] == args[0]]
            if "<> 'archived'" in sql:
                rows = [a for a in rows if a["status"] != "archived"]
            return sorted(rows, key=lambda a: a["created_at"])
        if "FROM cost_records" in sql:
            rows = list(self.cost_rows)
            if "agent_id = $3" in sql:
                rows = [r for r in rows if r.get("agent_id") == args[2]]
            return rows
        if "FROM runs WHERE workspace_id" in sql:
            rows = [
                {"id": r[0], "status": "pending", "profile_name": r[2], "agent_id": r[5]}
                for r in self.runs
            ]
            if "agent_id = $2" in sql:
                rows = [r for r in rows if r["agent_id"] == args[1]]
            return rows
        raise AssertionError(f"unexpected fetch: {sql}")

    async def execute(self, sql: str, *args):
        if "UPDATE agents SET status = 'archived'" in sql:
            row = self.agents.get(args[0])
            if row is not None:
                row["status"] = "archived"
                row["is_default"] = False
            return
        if sql.strip().startswith("INSERT INTO agents"):  # default bootstrap
            ws, name, profile = args[0], args[1], args[2]
            if self._default_of(ws) is not None:
                return  # ON CONFLICT (workspace_id) WHERE is_default DO NOTHING
            if self._live_name_taken(ws, name):
                raise RuntimeError("unique violation: agents_name_unique_live")
            self.add_agent(workspace_id=ws, name=name, profile_name=profile, is_default=True)
            return
        if "INSERT INTO runs" in sql:
            self.runs.append(args)
            return
        if "UPDATE runs SET" in sql:
            return
        if "pg_advisory_xact_lock" in sql:  # A5: serialises one workspace's admissions
            return
        raise AssertionError(f"unexpected execute: {sql}")


@asynccontextmanager
async def _scope(pool, workspace_id):  # signature matches backend.database.workspace_scope
    yield _scope.db


def _client(ws_id: str, db: AgentsDb):
    _scope.db = db
    app = FastAPI()
    app.include_router(agents_router.router, prefix="/v1")
    app.include_router(runs_router.router, prefix="/v1")
    app.include_router(ledger_router.router, prefix="/v1")
    app.include_router(packs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    app.state.sessions = MagicMock()
    app.state.cfg = MagicMock(repo_root=REPO)
    # pro_plus: this suite is about agent narrowing/attribution, not entitlement —
    # marketing is now gated at pro_plus (gtm_core/gating.toml).
    ctx = WorkspaceCtx(user_id=str(uuid.uuid4()), workspace_id=ws_id, entitlement="pro_plus")
    app.dependency_overrides[require_auth] = lambda: ctx
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def db(ws_env):
    return AgentsDb()


def _patched(db):
    """start()/stop() patchers, so `workspace_scope` is faked in every module that binds
    it — SCOPE_MODULES is the list `_protocol1` keeps equal to the real binder set (the
    seams test asserts both directions), expanded here because this fixture drives the
    patchers manually rather than through a `with`."""
    return (
        patch.object(agents_router, "workspace_scope", _scope),
        *(patch.object(m, "workspace_scope", _scope) for m in SCOPE_MODULES),
        patch.object(ledger_router, "workspace_scope", _scope),
        patch.object(packs_router, "workspace_scope", _scope),
    )


def _with_client(ws_env, db):
    ps = _patched(db)
    for p in ps:
        p.start()
    client = _client(ws_env.ws_id, db)
    return client, ps


# ── CRUD + write-time narrowing ───────────────────────────────────────────────


def test_create_and_response_matches_contract(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    client, ps = _with_client(ws_env, db)
    try:
        resp = client.post(
            "/v1/agents",
            json={"name": "marketing-agent", "profile_name": PROFILE, "packs": ["marketing"]},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert not schema_validate(body, AGENT_SCHEMA), schema_validate(body, AGENT_SCHEMA)
        assert body["packs"] == ["marketing"] and body["status"] == "active"

        got = client.get(f"/v1/agents/{body['agent_id']}")
        assert got.status_code == 200 and got.json()["name"] == "marketing-agent"
    finally:
        for p in ps:
            p.stop()


def test_create_unknown_profile_422(ws_env, db):
    client, ps = _with_client(ws_env, db)
    try:
        resp = client.post("/v1/agents", json={"name": "x", "profile_name": "nope"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "unknown_profile"
    finally:
        for p in ps:
            p.stop()


def test_create_superset_packs_rejected_subset_ok(ws_env, db):
    """Narrowing at write: packs must be ⊆ the profile's activated set."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    client, ps = _with_client(ws_env, db)
    try:
        bad = client.post(
            "/v1/agents",
            json={
                "name": "wide",
                "profile_name": PROFILE,
                "packs": ["marketing", "prospecting"],
            },
        )
        assert bad.status_code == 422
        assert bad.json()["detail"] == {
            "code": "packs_not_narrowing",
            "not_activated": ["prospecting"],
        }
        ok = client.post(  # positive control
            "/v1/agents", json={"name": "narrow", "profile_name": PROFILE, "packs": ["marketing"]}
        )
        assert ok.status_code == 201
    finally:
        for p in ps:
            p.stop()


def test_create_budget_over_cap_rejected(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    db.cap = 50.0
    client, ps = _with_client(ws_env, db)
    try:
        bad = client.post(
            "/v1/agents",
            json={"name": "rich", "profile_name": PROFILE, "monthly_budget_usd": 100.0},
        )
        assert bad.status_code == 422
        assert bad.json()["detail"] == {"code": "budget_exceeds_cap", "cap_usd": 50.0}
        ok = client.post(
            "/v1/agents",
            json={"name": "frugal", "profile_name": PROFILE, "monthly_budget_usd": 10.0},
        )
        assert ok.status_code == 201
    finally:
        for p in ps:
            p.stop()


def test_duplicate_name_409_and_archived_name_freed(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    client, ps = _with_client(ws_env, db)
    try:
        first = client.post("/v1/agents", json={"name": "the-agent", "profile_name": PROFILE})
        assert first.status_code == 201
        dup = client.post("/v1/agents", json={"name": "the-agent", "profile_name": PROFILE})
        assert dup.status_code == 409 and dup.json()["detail"]["code"] == "agent_name_taken"

        # Archiving frees the name (uniqueness is among NON-archived agents).
        assert client.delete(f"/v1/agents/{first.json()['agent_id']}").status_code == 204
        again = client.post("/v1/agents", json={"name": "the-agent", "profile_name": PROFILE})
        assert again.status_code == 201
    finally:
        for p in ps:
            p.stop()


def test_update_narrowing_and_archived_immutable(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    client, ps = _with_client(ws_env, db)
    try:
        row = db.add_agent(workspace_id=ws_env.ws_id, name="a1")
        bad = client.patch(f"/v1/agents/{row['agent_id']}", json={"packs": ["prospecting"]})
        assert bad.status_code == 422

        ok = client.patch(
            f"/v1/agents/{row['agent_id']}", json={"status": "paused", "language": "de"}
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "paused" and ok.json()["language"] == "de"

        # PATCH may never archive/unarchive: status is Literal["active","paused"].
        assert (
            client.patch(f"/v1/agents/{row['agent_id']}", json={"status": "archived"}).status_code
            == 422
        )

        archived = db.add_agent(workspace_id=ws_env.ws_id, name="a2", status="archived")
        gone = client.patch(f"/v1/agents/{archived['agent_id']}", json={"name": "renamed"})
        assert gone.status_code == 409 and gone.json()["detail"]["code"] == "agent_archived"
    finally:
        for p in ps:
            p.stop()


def test_delete_archives_resolvable_and_excluded(ws_env, db):
    client, ps = _with_client(ws_env, db)
    try:
        row = db.add_agent(workspace_id=ws_env.ws_id, name="doomed", is_default=True)
        assert client.delete(f"/v1/agents/{row['agent_id']}").status_code == 204
        assert client.delete(f"/v1/agents/{row['agent_id']}").status_code == 204  # idempotent

        by_id = client.get(f"/v1/agents/{row['agent_id']}")
        assert by_id.status_code == 200 and by_id.json()["status"] == "archived"
        assert by_id.json()["is_default"] is False  # default slot released

        assert client.get("/v1/agents").json() == []
        listed = client.get("/v1/agents?include_archived=true").json()
        assert [a["agent_id"] for a in listed] == [row["agent_id"]]

        assert client.get(f"/v1/agents/{uuid.uuid4()}").status_code == 404
    finally:
        for p in ps:
            p.stop()


# ── default-agent bootstrap (helper-level, race semantics via the fake) ───────


def test_default_bootstrap_creates_exactly_one(ws_env, db):
    async def _go():
        first = await agents_mod.ensure_default_agent(db, ws_env.ws_id)
        second = await agents_mod.ensure_default_agent(db, ws_env.ws_id)
        return first, second

    first, second = asyncio.run(_go())
    assert first is not None and first == second
    defaults = [a for a in db.agents.values() if a["is_default"]]
    assert len(defaults) == 1 and defaults[0]["profile_name"] == PROFILE


def test_default_bootstrap_survives_name_collision(ws_env, db):
    db.add_agent(workspace_id=ws_env.ws_id, name="default")  # live, NOT default

    async def _go():
        return await agents_mod.ensure_default_agent(db, ws_env.ws_id)

    got = asyncio.run(_go())
    default = next(a for a in db.agents.values() if a["is_default"])
    assert got == default["agent_id"]
    assert default["name"].startswith("default-")  # fell back to a suffixed name


def test_default_bootstrap_none_without_profiles(ws_env, db):
    db.profiles = []
    assert asyncio.run(agents_mod.ensure_default_agent(db, ws_env.ws_id)) is None


# ── budget narrowing at spend time ────────────────────────────────────────────


def test_acheck_agent_budget_narrowing(ws_env, db):
    aid = "11111111-1111-1111-1111-111111111111"
    db.cost_rows = [
        {"agent_id": aid, "cost_usd": 3.0},
        {"agent_id": "other", "cost_usd": 40.0},  # another agent's spend never counts
    ]

    async def _go():
        under = await agents_mod.acheck_agent_budget(db, ws_env.ws_id, aid, 5.0)
        at_cap = await agents_mod.acheck_agent_budget(db, ws_env.ws_id, aid, 3.0)
        no_budget = await agents_mod.acheck_agent_budget(db, ws_env.ws_id, aid, None)
        no_agent = await agents_mod.acheck_agent_budget(db, ws_env.ws_id, None, 5.0)
        broken = await agents_mod.acheck_agent_budget(
            AsyncMock(fetchval=AsyncMock(side_effect=RuntimeError)), ws_env.ws_id, aid, 5.0
        )
        return under, at_cap, no_budget, no_agent, broken

    under, at_cap, no_budget, no_agent, broken = asyncio.run(_go())
    assert under is True
    assert at_cap is False  # spent >= budget denies
    assert no_budget is True and no_agent is True  # workspace cap alone governs
    assert broken is False  # fail-closed on a read error (network-facing paid path)


# ── runs: enforcement at use + drift + attribution ────────────────────────────


def _run_json(**over) -> dict:
    body = {
        "profile_name": PROFILE,
        "pack": "marketing",
        "variant": "linkedin-post",
        "inputs": {"brand_name": "ExampleCo"},
    }
    body.update(over)
    return body


def test_run_with_agent_full_enforcement(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing", "prospecting"]\n')
    client, ps = _with_client(ws_env, db)
    exec_mock = AsyncMock()
    try:
        with patch.object(runs_router, "_execute_pack_run", exec_mock):
            assert (
                client.post("/v1/runs", json=_run_json(agent_id=str(uuid.uuid4()))).status_code
                == 404
            )

            paused = db.add_agent(workspace_id=ws_env.ws_id, name="p", status="paused")
            r = client.post("/v1/runs", json=_run_json(agent_id=paused["agent_id"]))
            assert (r.status_code, r.json()["detail"]["code"]) == (409, "agent_paused")

            archived = db.add_agent(workspace_id=ws_env.ws_id, name="ar", status="archived")
            r = client.post("/v1/runs", json=_run_json(agent_id=archived["agent_id"]))
            assert (r.status_code, r.json()["detail"]["code"]) == (409, "agent_archived")

            other_profile = db.add_agent(
                workspace_id=ws_env.ws_id, name="op", profile_name="other-profile"
            )
            r = client.post("/v1/runs", json=_run_json(agent_id=other_profile["agent_id"]))
            assert (r.status_code, r.json()["detail"]["code"]) == (422, "agent_profile_mismatch")

            narrow = db.add_agent(workspace_id=ws_env.ws_id, name="narrow", packs=["prospecting"])
            r = client.post("/v1/runs", json=_run_json(agent_id=narrow["agent_id"]))
            assert (r.status_code, r.json()["detail"]["code"]) == (403, "agent_pack_not_allowed")

            allowed = db.add_agent(
                workspace_id=ws_env.ws_id,
                name="ok",
                packs=["marketing"],
                monthly_budget_usd=7.5,
            )
            r = client.post("/v1/runs", json=_run_json(agent_id=allowed["agent_id"]))
            assert r.status_code == 202
            assert r.json()["agent_id"] == allowed["agent_id"]
            # A5: the request path enqueues, so the agent context the worker will
            # dispatch with travels in the queued row's payload (arg 7), not in a call.
            payload = json.loads(db.runs[-1][6])
            assert payload["agent_budget_usd"] == 7.5
            # The runs row carries the attribution (arg 6 of the INSERT).
            assert db.runs[-1][5] == allowed["agent_id"]
            # profile_name may be omitted when the agent binds it.
            r2 = client.post(
                "/v1/runs", json=_run_json(agent_id=allowed["agent_id"], profile_name=None)
            )
            assert r2.status_code == 202
    finally:
        for p in ps:
            p.stop()


def test_run_drift_pack_deactivated_after_agent_write(ws_env, db):
    """Both drift directions: the stored row is never trusted. The profile
    deactivating a pack 403s a run whose agent still lists it."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    agent = db.add_agent(workspace_id=ws_env.ws_id, name="drifter", packs=["marketing"])
    client, ps = _with_client(ws_env, db)
    try:
        with patch.object(runs_router, "_execute_pack_run", AsyncMock()):
            ok = client.post("/v1/runs", json=_run_json(agent_id=agent["agent_id"]))
            assert ok.status_code == 202  # positive control before the drift

            # Drift: profile deactivates the pack AFTER the agent was written.
            (ws_env.profiles_root / PROFILE / "packs.toml").write_text("active = []\n")
            r = client.post("/v1/runs", json=_run_json(agent_id=agent["agent_id"]))
            assert (r.status_code, r.json()["detail"]["code"]) == (403, "pack_not_activated")
    finally:
        for p in ps:
            p.stop()


def test_agentless_run_attaches_default_for_attribution_only(ws_env, db):
    """No agent_id ⇒ default agent attaches (attribution), with NO narrowing —
    even a paused default never blocks an agent-less run (backward compat)."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    default = db.add_agent(
        workspace_id=ws_env.ws_id, name="default", is_default=True, status="paused"
    )
    client, ps = _with_client(ws_env, db)
    exec_mock = AsyncMock()
    try:
        with patch.object(runs_router, "_execute_pack_run", exec_mock):
            r = client.post("/v1/runs", json=_run_json())
            assert r.status_code == 202
            assert r.json()["agent_id"] == default["agent_id"]
            assert json.loads(db.runs[-1][6])["agent_budget_usd"] is None
    finally:
        for p in ps:
            p.stop()


def test_list_runs_agent_filter(ws_env, db):
    a1, a2 = str(uuid.uuid4()), str(uuid.uuid4())
    db.runs = [
        ("r1", ws_env.ws_id, PROFILE, "p", False, a1),
        ("r2", ws_env.ws_id, PROFILE, "p", False, a2),
        ("r3", ws_env.ws_id, PROFILE, "p", False, None),
    ]
    client, ps = _with_client(ws_env, db)
    try:
        all_runs = client.get("/v1/runs").json()
        assert {r["agent_id"] for r in all_runs} == {a1, a2, None}
        only_a1 = client.get(f"/v1/runs?agent_id={a1}").json()
        assert [r["run_id"] for r in only_a1] == ["r1"]
    finally:
        for p in ps:
            p.stop()


def test_list_runs_limit_upper_bound_is_valid_not_off_by_one_rejected(ws_env, db):
    """FL15/M-12: ``le=100`` makes the cap explicit in the schema — 100 itself must
    still be accepted (only 101+ 422s), so the router isn't off-by-one on its own
    documented bound."""
    client, ps = _with_client(ws_env, db)
    try:
        resp = client.get("/v1/runs?limit=100")
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 200, resp.text


# ── ledger partition ──────────────────────────────────────────────────────────


def test_ledger_costs_agent_partitions_sum_to_total(ws_env, db):
    a1, a2 = str(uuid.uuid4()), str(uuid.uuid4())
    base = {
        "run_id": "r",
        "stage": "s",
        "model": "m",
        "input_tokens": 1,
        "output_tokens": 1,
        "recorded_at": "t",
    }
    db.cost_rows = [
        {**base, "agent_id": a1, "cost_usd": 1.25},
        {**base, "agent_id": a1, "cost_usd": 0.75},
        {**base, "agent_id": a2, "cost_usd": 2.0},
        {**base, "agent_id": None, "cost_usd": 0.5},  # pre-A4 remainder
    ]
    client, ps = _with_client(ws_env, db)
    try:
        total = client.get("/v1/ledger/costs").json()["total_usd"]
        p1 = client.get(f"/v1/ledger/costs?agent_id={a1}").json()["total_usd"]
        p2 = client.get(f"/v1/ledger/costs?agent_id={a2}").json()["total_usd"]
    finally:
        for p in ps:
            p.stop()
    assert (p1, p2) == (2.0, 2.0)
    assert p1 + p2 + 0.5 == total  # disjoint partitions + NULL remainder = workspace total


# ── packs listing: the agent's view ───────────────────────────────────────────


def test_packs_listing_agent_view(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing", "prospecting"]\n')
    narrow = db.add_agent(workspace_id=ws_env.ws_id, name="narrow", packs=["marketing"])
    client, ps = _with_client(ws_env, db)
    try:
        full = client.get(f"/v1/packs?profile_name={PROFILE}").json()
        assert {d["pack"] for d in full} == {"marketing", "prospecting"}

        scoped = client.get(f"/v1/packs?agent_id={narrow['agent_id']}").json()
        assert scoped and {d["pack"] for d in scoped} == {"marketing"}

        assert client.get(f"/v1/packs?agent_id={uuid.uuid4()}").status_code == 404
    finally:
        for p in ps:
            p.stop()


# ── malformed client ids (ER-02) ──────────────────────────────────────────────


class _PgArgChecks:
    """Wraps a fake connection with the argument checks asyncpg and Postgres make before
    any row logic, so a malformed id surfaces as it would against the real engine: a
    `$n::uuid` argument must parse as a UUID, and LIMIT must not be negative. A bound id
    must also be a `str` — the in-process run state is keyed by the string run id."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls: list[tuple] = []

    def _check(self, sql: str, args: tuple) -> None:
        for n in re.findall(r"\$(\d+)::uuid", sql):
            value = args[int(n) - 1]
            if value is None:
                continue
            assert isinstance(value, str), f"${n}::uuid bound a {type(value).__name__}"
            try:
                uuid.UUID(value)
            except ValueError:
                raise asyncpg.exceptions.DataError(
                    f"invalid input for query argument ${n}"
                ) from None
        # findall, not search: a query with more than one LIMIT clause (e.g. a UNIONed
        # subquery) must not have a negative bind slip past an unchecked second one.
        for n in re.findall(r"LIMIT \$(\d+)", sql):
            if args[int(n) - 1] < 0:
                raise asyncpg.exceptions.InvalidRowCountInLimitClauseError(
                    "LIMIT must not be negative"
                )
        self.calls.append(args)

    async def fetch(self, sql: str, *args):
        self._check(sql, args)
        return await self.inner.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args):
        self._check(sql, args)
        return await self.inner.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args):
        self._check(sql, args)
        return await self.inner.fetchval(sql, *args)

    async def execute(self, sql: str, *args):
        self._check(sql, args)
        return await self.inner.execute(sql, *args)


def _enveloped_client(ws_env, conn):
    """The shared harness with the production error handlers, observing 500s as
    responses rather than re-raised exceptions."""
    client, ps = _with_client(ws_env, conn)
    client.app.include_router(api_keys_router.router, prefix="/v1")
    client.app.include_router(onboard_router.router, prefix="/v1")
    keys_scope = patch.object(api_keys_router, "workspace_scope", _scope)
    keys_scope.start()
    register_error_handlers(client.app)
    return TestClient(client.app, raise_server_exceptions=False), (*ps, keys_scope)


_BAD = "not-a-uuid"
_OK = "00000000-0000-0000-0000-0000000000b1"
_GATE = {"decision": "approve", "content_sha": "ab"}


@pytest.mark.parametrize(
    ("method", "path", "body", "param"),
    [
        ("get", f"/v1/runs/{_BAD}", None, "run_id"),
        ("post", f"/v1/runs/{_BAD}/cancel", None, "run_id"),
        ("post", f"/v1/runs/{_BAD}/gate", _GATE, "run_id"),
        ("get", f"/v1/runs/{_BAD}/stream", None, "run_id"),
        ("get", f"/v1/runs/{_BAD}/artifacts", None, "run_id"),
        ("get", f"/v1/runs/{_BAD}/artifacts/{_OK}", None, "run_id"),
        ("get", f"/v1/runs/{_OK}/artifacts/{_BAD}", None, "artifact_id"),
        ("get", f"/v1/agents/{_BAD}", None, "agent_id"),
        ("patch", f"/v1/agents/{_BAD}", {"name": "renamed"}, "agent_id"),
        ("delete", f"/v1/agents/{_BAD}", None, "agent_id"),
    ],
)
def test_malformed_path_id_is_a_422_validation_error(ws_env, db, method, path, body, param):
    conn = _PgArgChecks(db)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        resp = client.request(method, path, json=body)
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 422, resp.text
    data = resp.json()
    assert data["error"]["code"] == "validation_error"
    assert [e["loc"] for e in data["error"]["details"]] == [["path", param]]
    assert conn.calls == []


def test_path_id_reaches_the_query_as_the_canonical_string(ws_env, db):
    agent = db.add_agent(workspace_id=ws_env.ws_id, name="upper")
    conn = _PgArgChecks(db)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        resp = client.get(f"/v1/agents/{agent['agent_id'].upper()}")
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_id"] == agent["agent_id"]
    assert conn.calls == [(agent["agent_id"], ws_env.ws_id)]


@pytest.mark.parametrize(
    ("method", "path", "body", "loc"),
    [
        ("get", f"/v1/runs?agent_id={_BAD}", None, ["query", "agent_id"]),
        ("get", "/v1/runs?limit=-1", None, ["query", "limit"]),
        ("get", "/v1/runs?limit=0", None, ["query", "limit"]),
        # FL15/M-12: limit is now explicitly bounded (le=100) — 101 is refused at
        # the schema boundary rather than silently truncated to 100.
        ("get", "/v1/runs?limit=101", None, ["query", "limit"]),
        ("get", f"/v1/packs?agent_id={_BAD}", None, ["query", "agent_id"]),
        ("post", "/v1/runs", _run_json(agent_id=_BAD), ["body", "agent_id"]),
        ("delete", f"/v1/api-keys/{_BAD}", None, ["path", "key_id"]),
        ("get", f"/v1/onboard/{_BAD}/diff", None, ["path", "draft_id"]),
        ("delete", f"/v1/onboard/{_BAD}", None, ["path", "draft_id"]),
        (
            "post",
            f"/v1/onboard/{_BAD}/promote",
            {"confirmed_company_name": "Example Co"},
            ["path", "draft_id"],
        ),
        (
            "post",
            f"/v1/onboard/{_BAD}/product/widget/extract",
            {"source_type": "text", "source": "We build widgets."},
            ["path", "draft_id"],
        ),
    ],
)
def test_remaining_malformed_ids_are_a_422_validation_error(ws_env, db, method, path, body, loc):
    conn = _PgArgChecks(db)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        resp = client.request(method, path, json=body)
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 422, resp.text
    data = resp.json()
    assert data["error"]["code"] == "validation_error"
    assert [e["loc"] for e in data["error"]["details"]] == [loc]
    assert conn.calls == []


def test_run_body_agent_id_reaches_admission_as_the_canonical_string(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    agent = db.add_agent(workspace_id=ws_env.ws_id, name="upper", packs=["marketing"])
    conn = _PgArgChecks(db)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        with patch.object(runs_router, "_execute_pack_run", AsyncMock()):
            resp = client.post("/v1/runs", json=_run_json(agent_id=agent["agent_id"].upper()))
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 202, resp.text
    assert resp.json()["agent_id"] == agent["agent_id"]
    assert db.runs[-1][5] == agent["agent_id"]
    assert "agent_id" not in json.loads(db.runs[-1][6])


def test_run_list_agent_id_reaches_the_query_as_the_canonical_string(ws_env, db):
    agent = str(uuid.uuid4())
    db.runs = [("r1", ws_env.ws_id, PROFILE, "p", False, agent)]
    conn = _PgArgChecks(db)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        resp = client.get(f"/v1/runs?agent_id={agent.upper()}")
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 200, resp.text
    assert [r["run_id"] for r in resp.json()] == ["r1"]


def test_onboard_draft_id_is_looked_up_as_the_canonical_string(ws_env, db, monkeypatch):
    monkeypatch.setitem(
        onboard_router._drafts,
        _OK,
        {
            "slug": "example-co",
            "staged_root": ws_env.content_root / "no-such-staging",
            "draft": {},
            "workspace_id": ws_env.ws_id,
        },
    )
    client, ps = _enveloped_client(ws_env, _PgArgChecks(db))
    try:
        resp = client.delete(f"/v1/onboard/{_OK.upper()}")
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 204, resp.text
    assert _OK not in onboard_router._drafts


# ── malformed ledger query params (EN-12) ─────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "param"),
    [
        ("/v1/ledger/costs?agent_id=not-a-uuid", "agent_id"),
        ("/v1/ledger/history?limit=-1", "limit"),
        ("/v1/ledger/history?limit=0", "limit"),
        ("/v1/ledger/rollup?run_id=not-a-uuid", "run_id"),
    ],
)
def test_malformed_ledger_query_is_a_422_validation_error(ws_env, db, path, param):
    conn = _PgArgChecks(db)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        resp = client.get(path)
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 422, resp.text
    data = resp.json()
    assert data["error"]["code"] == "validation_error"
    assert [e["loc"] for e in data["error"]["details"]] == [["query", param]]
    assert conn.calls == []


def test_ledger_costs_agent_id_reaches_the_query_as_the_canonical_string(ws_env, db):
    agent = str(uuid.uuid4())
    db.cost_rows = [
        {
            "run_id": "r",
            "stage": "s",
            "model": "m",
            "input_tokens": 1,
            "output_tokens": 1,
            "recorded_at": "t",
            "agent_id": agent,
            "cost_usd": 1.5,
        }
    ]
    conn = _PgArgChecks(db)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        resp = client.get(f"/v1/ledger/costs?agent_id={agent.upper()}")
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_usd"] == 1.5
    assert conn.calls[0][2] == agent


def test_ledger_rollup_run_id_reaches_the_query_as_the_canonical_string(ws_env, db):
    run_id = str(uuid.uuid4())
    conn = _PgArgChecks(db)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        resp = client.get(f"/v1/ledger/rollup?run_id={run_id.upper()}")
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 200, resp.text
    assert resp.json()["run_id"] == run_id
    assert conn.calls[-1] == (ws_env.ws_id, run_id)


@pytest.mark.parametrize(("limit", "bound"), [(1, 1), (200, 200), (500, 200)])
def test_ledger_history_limit_keeps_the_200_clamp(ws_env, limit, bound):
    inner = AsyncMock()
    inner.fetch.return_value = []
    conn = _PgArgChecks(inner)
    client, ps = _enveloped_client(ws_env, conn)
    try:
        resp = client.get(f"/v1/ledger/history?limit={limit}")
    finally:
        for p in ps:
            p.stop()
    assert resp.status_code == 200, resp.text
    assert conn.calls == [(ws_env.ws_id, bound)]

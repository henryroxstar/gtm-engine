"""Fleet Phase A, Task 3 — REST wiring: a `kind="service"` principal (a gtm-engine
agent's own API key) dispatching, reading, and being refused through the actual
`/v1/runs*` and `/v1/packs*` routes now on `require_principal`.

Convention: no pytest-asyncio; `TestClient` drives the routes; DB is an in-memory
stateful fake modelling the exact SQL the routers/services issue (mirrors
tests/backend/test_agents.py's `AgentsDb` style, but self-contained here since the
new V026 columns — `runs.principal_kind`/`principal_id`, `agents.read_scope`/
`daily_dispatch_cap` — and the new query shapes `backend/services/runs/
principal_admission.py` issues don't overlap cleanly with `AgentsDb`'s positional-
args INSERT modelling).

Task 4's own `backend/callers/limits.py` is exercised THROUGH this wiring (not
re-tested in isolation — see tests/backend/test_caller_limits.py for that), which is
the point of the "daily cap integration" test below.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent.config import Config  # noqa: E402
from backend.callers.limits import enforce_principal_rate  # noqa: E402
from backend.callers.principal import Principal  # noqa: E402
from backend.callers.rest import require_principal  # noqa: E402
from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import api_keys as api_keys_router  # noqa: E402
from backend.routers import packs as packs_router  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from gtm_core.capabilities import Entitlement  # noqa: E402
from tests.backend._protocol1 import REPO  # noqa: E402
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402
from tests.contracts.minijsonschema import validate as schema_validate  # noqa: E402

with (REPO / "schemas" / "run-event.schema.json").open() as _f:
    RUN_EVENT_SCHEMA = json.load(_f)
_SNAPSHOT_DATA_SCHEMA = next(
    b["properties"]["data"]
    for b in RUN_EVENT_SCHEMA["oneOf"]
    if b["properties"]["event"]["const"] == "snapshot"
)


# ── fake DB ──────────────────────────────────────────────────────────────────────


def _agent(agent_id: str, ws_id: str, **over) -> dict:
    row = {
        "agent_id": agent_id,
        "workspace_id": ws_id,
        "name": "svc-agent",
        "profile_name": PROFILE,
        "packs": None,
        "language": None,
        "monthly_budget_usd": None,
        "status": "active",
        "is_default": False,
        "read_scope": "own",
        "daily_dispatch_cap": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(over)
    return row


class ServiceDb:
    """agents + runs + run_gates + api_keys, keyed to the exact SQL Task 3's wiring
    issues (backend/services/runs/principal_admission.py, admission.py, queries.py,
    decisions.py, gates.py, backend/agents.py, backend/routers/api_keys.py). Unknown
    SQL raises — a new query must be modelled on purpose."""

    def __init__(self) -> None:
        self.agents: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.api_keys: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.cap: float | None = 1000.0
        self.cost_rows: list[dict] = []

    def add_agent(self, agent_id: str, ws_id: str, **over) -> dict:
        row = _agent(agent_id, ws_id, **over)
        self.agents[agent_id] = row
        return row

    def add_run(self, run_id: str, ws_id: str, **over) -> dict:
        row = {
            "id": run_id,
            "workspace_id": ws_id,
            "profile_name": PROFILE,
            "status": "queued",
            "agent_id": None,
            "payload": {},
            "principal_kind": None,
            "principal_id": None,
            "client_request_id": None,
            "output": None,
            "error": None,
            "error_code": None,
            "pending_gate": None,
            "pending_content": None,
            "created_at": None,
            "gate_kind": None,
            "gate_node_id": None,
            "gate_state": None,
        }
        row.update(over)
        self.runs[run_id] = row
        return row

    # asyncpg-ish surface -----------------------------------------------------

    def _fetch_agent_row(self, aid, ws, *_rest):
        row = self.agents.get(aid)
        return row if row and row["workspace_id"] == ws else None

    def _fetch_gate_summary(self, run_id, ws, *_rest):
        row = self.runs.get(run_id)
        if row is None or row["workspace_id"] != ws:
            return None
        return {
            "status": row["status"],
            "pending_gate": row["pending_gate"],
            "pending_content": row["pending_content"],
            "gate_kind": row["gate_kind"],
        }

    def _fetch_run_row(self, run_id, ws, *_rest):
        row = self.runs.get(run_id)
        return row if row and row["workspace_id"] == ws else None

    def _fetch_run_status(self, run_id, ws, *_rest):
        row = self.runs.get(run_id)
        return {"status": row["status"]} if row and row["workspace_id"] == ws else None

    def _fetch_run_agent_id(self, run_id, ws, *_rest):
        row = self.runs.get(run_id)
        return {"agent_id": row["agent_id"]} if row and row["workspace_id"] == ws else None

    def _cancel_run_row(self, run_id, ws, *_rest):
        row = self.runs.get(run_id)
        if row is None or row["workspace_id"] != ws:
            return None
        if row["status"] in ("ok", "failed", "rejected", "canceled"):
            return None
        row["status"] = "canceled"
        row["error"] = "canceled by user"
        row["pending_gate"] = None
        row["pending_content"] = None
        return {"id": run_id}

    def _record_gate_decision(self, run_id, ws, new_state, edited_content):
        row = self.runs.get(run_id)
        if row is None or row["workspace_id"] != ws or row["gate_state"] != "open":
            return None
        row["gate_state"] = new_state
        row["pending_content"] = edited_content if new_state == "edited" else row["pending_content"]
        return {"gate": row["gate_kind"], "node_id": row["gate_node_id"]}

    def _run_exists(self, run_id, ws, *_rest):
        row = self.runs.get(run_id)
        return {"?column?": 1} if row and row["workspace_id"] == ws else None

    def add_artifact(self, artifact_id: str, run_id: str, ws_id: str) -> str:
        self.artifacts[artifact_id] = {"run_id": run_id, "workspace_id": ws_id}
        return artifact_id

    def _fetch_artifact_pointer(self, artifact_id, run_id, ws, *_rest):
        row = self.artifacts.get(artifact_id)
        if row is None or row["run_id"] != run_id or row["workspace_id"] != ws:
            return None
        return {"rel_path": "runs/out.md", "name": "out.md", "media_type": "text/markdown"}

    def _cap_and_spent(self, *_args):
        if self.cap is None:
            return None
        return {"cap": self.cap, "spent": sum(r["cost_usd"] for r in self.cost_rows)}

    def _insert_api_key(self, ws, key_hash, prefix, label, entitlement, agent_id):
        key_id = str(uuid.uuid4())
        row = {
            "id": key_id,
            "prefix": prefix,
            "label": label,
            "entitlement": entitlement,
            "last_used_at": None,
            "created_at": datetime.now(UTC),
            "revoked_at": None,
            "agent_id": agent_id,
        }
        self.api_keys[key_id] = row
        return row

    _FETCHROW_ROUTES = (
        (lambda s: "FROM agents WHERE id" in s, "_fetch_agent_row"),
        (lambda s: "r.status, r.pending_gate, r.pending_content" in s, "_fetch_gate_summary"),
        (lambda s: "FROM runs r WHERE r.id" in s, "_fetch_run_row"),
        (lambda s: s.strip().startswith("SELECT status FROM runs WHERE id"), "_fetch_run_status"),
        (
            lambda s: "SELECT agent_id::text AS agent_id FROM runs WHERE id" in s,
            "_fetch_run_agent_id",
        ),
        (lambda s: s.strip().startswith("UPDATE runs") and "RETURNING id" in s, "_cancel_run_row"),
        (
            lambda s: s.strip().startswith("UPDATE run_gates SET state = $3"),
            "_record_gate_decision",
        ),
        (lambda s: "AS cap" in s and "AS spent" in s, "_cap_and_spent"),
        (lambda s: s.strip().startswith("INSERT INTO api_keys"), "_insert_api_key"),
        (lambda s: s.strip().startswith("SELECT 1 FROM runs WHERE id"), "_run_exists"),
        (
            lambda s: "FROM run_artifacts" in s and "id = $1::uuid AND run_id = $2::uuid" in s,
            "_fetch_artifact_pointer",
        ),
    )

    async def fetchrow(self, sql: str, *args):
        for predicate, method_name in self._FETCHROW_ROUTES:
            if predicate(sql):
                return getattr(self, method_name)(*args)
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetchval(self, sql: str, *args):
        if "SELECT read_scope FROM agents WHERE id" in sql:
            aid, ws = args
            row = self.agents.get(aid)
            return row["read_scope"] if row and row["workspace_id"] == ws else None
        if "COUNT(*) FROM runs" in sql:  # agents.py: agent_today_dispatch_count
            ws, aid = args
            return sum(
                1 for r in self.runs.values() if r["workspace_id"] == ws and r["agent_id"] == aid
            )
        if "count(*) FROM runs" in sql:  # admission.py: _reserve_cap in-flight count
            ws = args[0]
            return sum(
                1
                for r in self.runs.values()
                if r["workspace_id"] == ws
                and r["status"] in ("queued", "running", "awaiting_approval")
            )
        if "SUM(cost_usd)" in sql and "cost_records" in sql:
            aid = args[1]
            return sum(r["cost_usd"] for r in self.cost_rows if r.get("agent_id") == aid)
        if sql.strip().startswith("INSERT INTO runs"):
            (
                run_id,
                ws,
                profile_name,
                _prompt,
                _dry_run,
                agent_id,
                payload_json,
                principal_kind,
                principal_id,
                client_request_id,
                *rest,
            ) = args
            external_ref = rest[0] if rest else None
            if client_request_id is not None:
                clash = next(
                    (
                        r
                        for r in self.runs.values()
                        if r["workspace_id"] == ws and r["client_request_id"] == client_request_id
                    ),
                    None,
                )
                if clash is not None:
                    return None
            self.add_run(
                run_id,
                ws,
                profile_name=profile_name,
                agent_id=agent_id,
                payload=json.loads(payload_json),
                principal_kind=principal_kind,
                principal_id=principal_id,
                client_request_id=client_request_id,
                external_ref=external_ref,
            )
            return run_id
        if "SELECT id::text FROM runs WHERE workspace_id" in sql and "client_request_id" in sql:
            ws, client_request_id = args
            match = next(
                (
                    r
                    for r in self.runs.values()
                    if r["workspace_id"] == ws and r["client_request_id"] == client_request_id
                ),
                None,
            )
            return match["id"] if match is not None else None
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetch(self, sql: str, *args):
        if "FROM run_nodes" in sql or "FROM run_blocks" in sql or "FROM run_artifacts" in sql:
            return []
        if "FROM runs WHERE workspace_id" in sql:
            ws = args[0]
            rows = [r for r in self.runs.values() if r["workspace_id"] == ws]
            idx = 1
            if "agent_id = $" in sql:
                rows = [r for r in rows if r["agent_id"] == args[idx]]
                idx += 1
            if "status = $" in sql:
                rows = [r for r in rows if r["status"] == args[idx]]
                idx += 1
            limit = args[-1]
            return sorted(rows, key=lambda r: r["id"])[:limit]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def execute(self, sql: str, *args):
        if "pg_advisory_xact_lock" in sql:
            return
        if sql.strip().startswith("UPDATE run_gates SET state = 'rejected'"):
            run_id, ws = args
            row = self.runs.get(run_id)
            if row is not None and row["workspace_id"] == ws:
                row["gate_state"] = "rejected"
            return
        raise AssertionError(f"unexpected execute: {sql}")


@asynccontextmanager
async def _scope(pool, workspace_id):  # signature matches backend.database.workspace_scope
    yield _scope.db


def _cfg(tmp_path) -> Config:
    """A real Config (not a MagicMock): enforce_agent_daily_cap's/decide_gate's own
    refusal paths call _workspace_scoped_config, which does dataclasses.replace(cfg, ...)
    and needs a real dataclass. repo_root MUST be the real REPO so pack resolution finds
    packs/marketing/graphs/linkedin-post.toml on disk."""
    return Config(
        repo_root=REPO,
        plugin_path=tmp_path / "plugin",
        profiles_root=tmp_path / "profiles",
        content_root=tmp_path / "content",
        default_profile=PROFILE,
    )


def _client(ws_env, db: ServiceDb, principals: dict[str, Principal]) -> TestClient:
    """`principals` maps a bearer token to the Principal require_principal should
    resolve for it — a fixture-level stand-in for verify_chain, exercising the SAME
    Depends(require_principal) parameter binding every route now uses, without a real
    JWT/API-key round trip (already covered by tests/backend/test_callers_ports.py)."""
    _scope.db = db
    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.include_router(packs_router.router, prefix="/v1")
    app.include_router(api_keys_router.router, prefix="/v1")
    app.state.pool = object()
    app.state.sessions = object()
    app.state.cfg = _cfg(ws_env.tmp)

    async def _require_principal_override(request: Request) -> Principal:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        principal = principals[token]
        request.state.principal = principal
        return principal

    app.dependency_overrides[require_principal] = _require_principal_override
    # api_keys.py stays require_auth-only (G4-adjacent: only a human mints keys) —
    # tests below that touch it also override this with a plain WorkspaceCtx.
    app.dependency_overrides[require_auth] = lambda: WorkspaceCtx(
        user_id=str(uuid.uuid4()), workspace_id=ws_env.ws_id, entitlement=Entitlement.PRO_PLUS
    )
    register_error_handlers(app)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _patch_workspace_scope():
    """Every service module admission/dispatch touches binds its OWN `workspace_scope`
    name (backend/services/runs/*, backend/routers/packs.py) — patching only the
    router leaves the others live and they'd hit a real pool. Mirrors
    tests/backend/_protocol1.py's SCOPE_MODULES/patch_everywhere convention, plus
    packs_router (not itself a "runs" service module)."""
    from backend.callers import limits as callers_limits
    from tests.backend._protocol1 import SCOPE_MODULES, patch_everywhere

    extra = (packs_router, api_keys_router, callers_limits)
    with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope):
        with patch_everywhere(extra, "workspace_scope", _scope):
            yield


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    from types import SimpleNamespace

    ws_id = str(uuid.uuid4())
    profiles_root = tmp_path / "workspaces" / ws_id / "profiles"
    profiles_root.mkdir(parents=True)
    return SimpleNamespace(ws_id=ws_id, profiles_root=profiles_root, tmp=tmp_path)


@contextmanager
def _recorded(ws_env):
    """The rows written to this workspace's admission ``denials.jsonl`` inside the block,
    read from the REAL file — the file is the audit, not the arguments a sink was called
    with (a sink that drops fields would pass a kwargs assertion)."""
    path = ws_env.tmp / "workspaces" / ws_env.ws_id / "content" / "_admission" / "denials.jsonl"
    before = len(path.read_text().splitlines()) if path.exists() else 0
    rows: list[dict] = []
    yield rows
    if path.exists():
        rows.extend(json.loads(line) for line in path.read_text().splitlines()[before:])


@pytest.fixture()
def db():
    return ServiceDb()


def _svc(ws_id: str, agent_id: str) -> Principal:
    return Principal(
        kind="service",
        subject=f"key-{agent_id}",
        workspace_id=ws_id,
        entitlement=Entitlement.PRO_PLUS,
        agent_id=agent_id,
        credential_id=f"key-{agent_id}",
        verifier="api_key",
    )


def _user(ws_id: str) -> Principal:
    return Principal(
        kind="user",
        subject=str(uuid.uuid4()),
        workspace_id=ws_id,
        entitlement=Entitlement.PRO_PLUS,
    )


def _run_json(**over) -> dict:
    body = {
        "profile_name": PROFILE,
        "pack": "marketing",
        "variant": "linkedin-post",
        "inputs": {"brand_name": "ExampleCo"},
    }
    body.update(over)
    return body


# ── dispatch ─────────────────────────────────────────────────────────────────────


def test_service_principal_dispatches_inside_its_pack_subset(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing", "prospecting"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"])
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})

    resp = client.post("/v1/runs", json=_run_json(), headers={"Authorization": "Bearer tok"})

    assert resp.status_code == 202
    body = resp.json()
    assert body["agent_id"] == agent_id
    assert body["principal_kind"] == "service"
    assert body["principal_id"] == principal.subject
    assert db.runs[body["run_id"]]["agent_id"] == agent_id


def test_pack_outside_the_subset_refuses_and_audits(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing", "prospecting"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"])
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})

    with _recorded(ws_env) as rows:
        resp = client.post(
            "/v1/runs",
            json=_run_json(pack="prospecting", variant="prospect-outreach"),
            headers={"Authorization": "Bearer tok"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "agent_pack_not_allowed"
    assert len(rows) == 1
    assert rows[0]["principal_kind"] == "service"
    assert rows[0]["code"] == "agent_pack_not_allowed"


def test_prompt_mode_refuses_for_a_service_principal(ws_env, db):
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id)
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})

    with _recorded(ws_env) as rows:
        resp = client.post(
            "/v1/runs",
            json={"profile_name": PROFILE, "prompt": "run market-scan"},
            headers={"Authorization": "Bearer tok"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "prompt_mode_requires_user"
    # PRD §7: every negative control for a service principal must leave a denials.jsonl
    # row naming the principal — admit_run_creation's Refusal sites are no exception.
    assert len(rows) == 1
    assert rows[0]["principal_kind"] == "service"
    assert rows[0]["code"] == "prompt_mode_requires_user"


def test_foreign_agent_id_in_body_refuses_agent_mismatch(ws_env, db):
    agent_id = str(uuid.uuid4())
    other_agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id)
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})

    with _recorded(ws_env) as rows:
        resp = client.post(
            "/v1/runs",
            json=_run_json(agent_id=other_agent_id),
            headers={"Authorization": "Bearer tok"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "agent_mismatch"
    assert len(rows) == 1
    assert rows[0]["principal_kind"] == "service"
    assert rows[0]["code"] == "agent_mismatch"


def test_omitted_agent_id_is_forced_to_the_bound_agent(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"])
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})

    resp = client.post(
        "/v1/runs", json=_run_json(agent_id=None), headers={"Authorization": "Bearer tok"}
    )

    assert resp.status_code == 202
    assert resp.json()["agent_id"] == agent_id


# ── reads / own-run scoping ────────────────────────────────────────────────────


def test_another_agents_run_is_404_on_every_read_route_and_absent_from_list(ws_env, db):
    agent_a = str(uuid.uuid4())
    agent_b = str(uuid.uuid4())
    db.add_agent(agent_a, ws_env.ws_id)
    db.add_agent(agent_b, ws_env.ws_id)
    other_run = db.add_run(str(uuid.uuid4()), ws_env.ws_id, agent_id=agent_b, status="running")
    # A REAL artifact of the other agent's run — a random id would 404 on "artifact not
    # found" before the ownership check ever ran, proving nothing about scoping.
    artifact_id = db.add_artifact(str(uuid.uuid4()), other_run["id"], ws_env.ws_id)
    principal_a = _svc(ws_env.ws_id, agent_a)
    client = _client(ws_env, db, {"tok": principal_a})
    headers = {"Authorization": "Bearer tok"}

    run_url = f"/v1/runs/{other_run['id']}"
    for method, url in [
        ("get", run_url),
        ("post", f"{run_url}/cancel"),
        ("get", f"{run_url}/stream"),
        ("get", f"{run_url}/artifacts"),
        ("get", f"{run_url}/artifacts/{artifact_id}"),
    ]:
        with _recorded(ws_env) as rows:
            resp = getattr(client, method)(url, headers=headers)
        assert resp.status_code == 404, url
        # PRD §7: a 404 on the wire (no existence leak), but still a denials.jsonl row
        # naming the principal.
        assert len(rows) == 1
        assert rows[0]["principal_kind"] == "service", url
        assert rows[0]["code"] == "run_not_found", url

    listed = client.get("/v1/runs", headers=headers).json()
    assert other_run["id"] not in [r["run_id"] for r in listed]

    # Passing the other agent's id as an explicit filter must not widen the list either.
    listed_filtered = client.get(f"/v1/runs?agent_id={agent_b}", headers=headers).json()
    assert listed_filtered == []


def test_another_agents_run_is_byte_identical_to_a_missing_one(ws_env, db):
    # PRD §2.1: a 404 and "not yours" are the same answer — the WHOLE body, including the
    # legacy `detail` field, or a caller can tell a run it cannot see exists.
    agent_a = str(uuid.uuid4())
    agent_b = str(uuid.uuid4())
    db.add_agent(agent_a, ws_env.ws_id)
    db.add_agent(agent_b, ws_env.ws_id)
    foreign = db.add_run(str(uuid.uuid4()), ws_env.ws_id, agent_id=agent_b, status="running")
    foreign_artifact = db.add_artifact(str(uuid.uuid4()), foreign["id"], ws_env.ws_id)
    client = _client(ws_env, db, {"tok": _svc(ws_env.ws_id, agent_a)})
    headers = {"Authorization": "Bearer tok"}
    missing = str(uuid.uuid4())

    for method, suffix in [
        ("get", ""),
        ("post", "/cancel"),
        ("get", "/stream"),
        ("get", "/artifacts"),
    ]:
        call = getattr(client, method)
        seen = call(f"/v1/runs/{foreign['id']}{suffix}", headers=headers)
        absent = call(f"/v1/runs/{missing}{suffix}", headers=headers)
        assert (seen.status_code, seen.json()) == (absent.status_code, absent.json()), suffix

    seen = client.get(f"/v1/runs/{foreign['id']}/artifacts/{foreign_artifact}", headers=headers)
    absent = client.get(f"/v1/runs/{missing}/artifacts/{uuid.uuid4()}", headers=headers)
    assert (seen.status_code, seen.json()) == (absent.status_code, absent.json())


def test_read_scope_workspace_sees_every_run_in_the_workspace(ws_env, db):
    agent_a = str(uuid.uuid4())
    agent_b = str(uuid.uuid4())
    db.add_agent(agent_a, ws_env.ws_id, read_scope="workspace")
    db.add_agent(agent_b, ws_env.ws_id)
    other_run = db.add_run(str(uuid.uuid4()), ws_env.ws_id, agent_id=agent_b, status="running")
    principal_a = _svc(ws_env.ws_id, agent_a)
    client = _client(ws_env, db, {"tok": principal_a})
    headers = {"Authorization": "Bearer tok"}

    assert client.get(f"/v1/runs/{other_run['id']}", headers=headers).status_code == 200
    listed = client.get("/v1/runs", headers=headers).json()
    assert other_run["id"] in [r["run_id"] for r in listed]


def test_read_scope_workspace_does_not_let_a_key_cancel_another_agents_run(ws_env, db):
    # read_scope widens READS only. Cancelling is a write, and a key may only cancel its
    # own agent's runs — otherwise a coordinator key could kill a person's run parked at
    # a gate.
    agent_a = str(uuid.uuid4())
    agent_b = str(uuid.uuid4())
    db.add_agent(agent_a, ws_env.ws_id, read_scope="workspace")
    db.add_agent(agent_b, ws_env.ws_id)
    other_run = db.add_run(
        str(uuid.uuid4()), ws_env.ws_id, agent_id=agent_b, status="awaiting_approval"
    )
    own_run = db.add_run(str(uuid.uuid4()), ws_env.ws_id, agent_id=agent_a, status="running")
    client = _client(ws_env, db, {"tok": _svc(ws_env.ws_id, agent_a)})
    headers = {"Authorization": "Bearer tok"}

    with _recorded(ws_env) as rows:
        refused = client.post(f"/v1/runs/{other_run['id']}/cancel", headers=headers)
    assert refused.status_code == 404
    assert db.runs[other_run["id"]]["status"] == "awaiting_approval"
    assert [r["code"] for r in rows] == ["run_not_found"]

    assert client.post(f"/v1/runs/{own_run['id']}/cancel", headers=headers).status_code == 200
    assert db.runs[own_run["id"]]["status"] == "canceled"


def test_idempotent_replay_of_a_foreign_client_request_id_is_404(ws_env, db):
    agent_a = str(uuid.uuid4())
    agent_b = str(uuid.uuid4())
    db.add_agent(agent_a, ws_env.ws_id)
    db.add_agent(agent_b, ws_env.ws_id)
    db.add_run(
        str(uuid.uuid4()),
        ws_env.ws_id,
        agent_id=agent_b,
        status="ok",
        client_request_id="someone-elses-key",
    )
    principal_a = _svc(ws_env.ws_id, agent_a)
    client = _client(ws_env, db, {"tok": principal_a})

    with _recorded(ws_env) as rows:
        resp = client.post(
            "/v1/runs",
            json={
                "profile_name": PROFILE,
                "prompt": "irrelevant — never reached",
                "pack": None,
                "client_request_id": "someone-elses-key",
            },
            headers={"Authorization": "Bearer tok"},
        )
    # bind_agent forces agent_id to agent_a before the replay lookup even matters —
    # the point here is the replay branch itself is scoped, not just the fresh path.
    assert resp.status_code == 404
    assert len(rows) == 1
    assert rows[0]["code"] == "run_not_found"


# ── gate: G4, human only ───────────────────────────────────────────────────────


@pytest.mark.parametrize("gate_kind", ["plan", "publish", "email_enroll", "review"])
def test_gate_refuses_a_service_principal_at_every_gate_kind(ws_env, db, gate_kind):
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id)
    run = db.add_run(
        str(uuid.uuid4()),
        ws_env.ws_id,
        agent_id=agent_id,
        status="awaiting_approval",
        pending_gate="⟦GATE:x⟧",
        pending_content="draft",
        gate_kind=gate_kind,
        gate_state="open",
    )
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})
    fetch_spy = AsyncMock(return_value=None)

    with (
        patch.object(runs_router, "fetch_open_gate", fetch_spy),
        _recorded(ws_env) as rows,
    ):
        resp = client.post(
            f"/v1/runs/{run['id']}/gate",
            json={"decision": "approve", "content_sha": runs_router._content_sha("draft")},
            headers={"Authorization": "Bearer tok"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "human_approval_required"
    # require_human runs BEFORE any gate-kind branching — fetch_open_gate is never reached.
    fetch_spy.assert_not_called()
    assert len(rows) == 1
    assert rows[0]["code"] == "human_approval_required"


def test_gate_admits_a_user_principal_regardless_of_kind(ws_env, db):
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id)
    run = db.add_run(
        str(uuid.uuid4()),
        ws_env.ws_id,
        agent_id=agent_id,
        status="awaiting_approval",
        pending_gate="⟦GATE:publish⟧",
        pending_content="draft",
        gate_kind="publish",
        gate_state="open",
    )
    principal = _user(ws_env.ws_id)
    client = _client(ws_env, db, {"tok": principal})

    resp = client.post(
        f"/v1/runs/{run['id']}/gate",
        json={"decision": "approve", "content_sha": runs_router._content_sha("draft")},
        headers={"Authorization": "Bearer tok"},
    )

    assert resp.status_code == 200
    assert resp.json()["decision"] == "approve"


# ── attribution ──────────────────────────────────────────────────────────────────


def test_run_response_carries_principal_attribution_for_user_and_service(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"])
    svc = _svc(ws_env.ws_id, agent_id)
    user = _user(ws_env.ws_id)
    client = _client(ws_env, db, {"svc-tok": svc, "user-tok": user})

    svc_resp = client.post(
        "/v1/runs", json=_run_json(), headers={"Authorization": "Bearer svc-tok"}
    )
    assert svc_resp.json()["principal_kind"] == "service"
    assert svc_resp.json()["principal_id"] == svc.subject

    user_resp = client.post(
        "/v1/runs", json=_run_json(agent_id=None), headers={"Authorization": "Bearer user-tok"}
    )
    assert user_resp.json()["principal_kind"] == "user"
    assert user_resp.json()["principal_id"] == user.subject

    got = client.get(
        f"/v1/runs/{svc_resp.json()['run_id']}", headers={"Authorization": "Bearer svc-tok"}
    )
    assert got.json()["principal_kind"] == "service"
    assert got.json()["principal_id"] == svc.subject


def test_sse_snapshot_carries_principal_attribution():
    """Unit-level: the snapshot event builder (backend/services/runs/stream.py)
    exposes the two new columns, validated against the committed schema."""
    from backend.services.runs.stream import _Opening, _snapshot_frame

    row = {
        "id": "r1",
        "status": "running",
        "pending_gate": None,
        "pending_content": None,
        "principal_kind": "service",
        "principal_id": "key-abc",
    }
    frame = _snapshot_frame(_Opening(row, head=0, replay=None, nodes=[], blocks=[]))
    data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
    data = json.loads(data_line[len("data: ") :])
    assert data["principal_kind"] == "service"
    assert data["principal_id"] == "key-abc"
    errors = schema_validate(data, _SNAPSHOT_DATA_SCHEMA)
    assert not errors, errors


# ── pack listing ─────────────────────────────────────────────────────────────────


def test_pack_listing_narrowed_for_service_unchanged_for_user(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing", "prospecting"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"])
    svc = _svc(ws_env.ws_id, agent_id)
    user = _user(ws_env.ws_id)
    client = _client(ws_env, db, {"svc-tok": svc, "user-tok": user})

    svc_packs = client.get(
        f"/v1/packs?profile_name={PROFILE}", headers={"Authorization": "Bearer svc-tok"}
    ).json()
    assert {d["pack"] for d in svc_packs} == {"marketing"}

    user_packs = client.get(
        f"/v1/packs?profile_name={PROFILE}", headers={"Authorization": "Bearer user-tok"}
    ).json()
    assert {d["pack"] for d in user_packs} == {"marketing", "prospecting"}

    # A service principal cannot widen its own view via an explicit ?agent_id= either.
    svc_packs_forced = client.get(
        f"/v1/packs?profile_name={PROFILE}&agent_id={uuid.uuid4()}",
        headers={"Authorization": "Bearer svc-tok"},
    ).json()
    assert {d["pack"] for d in svc_packs_forced} == {"marketing"}


def test_pack_readiness_is_narrowed_for_a_service_principal(ws_env, db):
    # The readiness detail must show a key no more than the listing does: its own agent's
    # profile, and only packs in its agent's subset. Outside the subset is a 404
    # byte-identical to a variant that does not exist.
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing", "prospecting"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"])
    client = _client(
        ws_env, db, {"svc-tok": _svc(ws_env.ws_id, agent_id), "user-tok": _user(ws_env.ws_id)}
    )
    svc = {"Authorization": "Bearer svc-tok"}

    with _recorded(ws_env) as rows:
        outside = client.get(
            f"/v1/packs/prospecting/prospect-outreach/readiness?profile_name={PROFILE}",
            headers=svc,
        )
    missing = client.get(f"/v1/packs/nosuch/variant/readiness?profile_name={PROFILE}", headers=svc)
    assert outside.status_code == 404
    assert (outside.status_code, outside.json()) == (missing.status_code, missing.json())
    # A 404 on the wire, but a scope refusal on the ledger, naming the key.
    assert [(r["principal_kind"], r["code"]) for r in rows] == [("service", "pack_not_found")]

    # A caller-supplied profile is ignored: the agent's own profile is used.
    inside = client.get(
        "/v1/packs/marketing/linkedin-post/readiness?profile_name=someone-elses-profile",
        headers=svc,
    )
    assert inside.status_code == 200
    assert inside.json()["profile_name"] == PROFILE

    # A person is unchanged.
    user = client.get(
        f"/v1/packs/prospecting/prospect-outreach/readiness?profile_name={PROFILE}",
        headers={"Authorization": "Bearer user-tok"},
    )
    assert user.status_code == 200


# ── key minting ──────────────────────────────────────────────────────────────────


def test_create_api_key_bound_to_a_foreign_workspace_agent_is_refused(ws_env, db):
    foreign_agent = str(uuid.uuid4())
    db.add_agent(foreign_agent, str(uuid.uuid4()))  # a DIFFERENT workspace
    client = _client(ws_env, db, {})

    resp = client.post("/v1/api-keys", json={"agent_id": foreign_agent})

    assert resp.status_code == 404


def test_create_api_key_bound_to_an_archived_agent_is_refused(ws_env, db):
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, status="archived")
    client = _client(ws_env, db, {})

    resp = client.post("/v1/api-keys", json={"agent_id": agent_id})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "agent_archived"


def test_create_api_key_bound_to_an_active_agent_succeeds(ws_env, db):
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id)
    client = _client(ws_env, db, {})

    resp = client.post("/v1/api-keys", json={"agent_id": agent_id})

    assert resp.status_code == 201
    assert resp.json()["agent_id"] == agent_id


# ── daily cap integration (Task 4 through Task 3's wiring) ────────────────────────


def test_create_run_exceeding_the_agents_daily_cap_refuses_429(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"], daily_dispatch_cap=1)
    # One run already dispatched today for this agent — at the cap.
    db.add_run(str(uuid.uuid4()), ws_env.ws_id, agent_id=agent_id, status="ok")
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})

    with _recorded(ws_env) as rows:
        resp = client.post("/v1/runs", json=_run_json(), headers={"Authorization": "Bearer tok"})

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "agent_daily_cap_reached"
    assert len(rows) == 1
    assert rows[0]["code"] == "agent_daily_cap_reached"


def test_the_daily_cap_also_refuses_a_person_running_that_agent(ws_env, db):
    # D10: the cap belongs to the agent, not to machine callers — same symmetry as the
    # agent's monthly budget. A person naming a capped-out agent is refused too.
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"], daily_dispatch_cap=1)
    db.add_run(str(uuid.uuid4()), ws_env.ws_id, agent_id=agent_id, status="ok")
    client = _client(ws_env, db, {"jwt": _user(ws_env.ws_id)})

    resp = client.post(
        "/v1/runs", json=_run_json(agent_id=agent_id), headers={"Authorization": "Bearer jwt"}
    )

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "agent_daily_cap_reached"


def test_create_run_under_the_agents_daily_cap_succeeds(ws_env, db):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"], daily_dispatch_cap=5)
    db.add_run(str(uuid.uuid4()), ws_env.ws_id, agent_id=agent_id, status="ok")
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})

    resp = client.post("/v1/runs", json=_run_json(), headers={"Authorization": "Bearer tok"})

    assert resp.status_code == 202


# ── rate-limit wiring (correctness lives in test_caller_limits.py) ────────────────


def test_create_run_wiring_calls_enforce_principal_rate(ws_env, db):
    """This route's OWN wiring must actually invoke the shared limiter dependency —
    not a re-test of the limiter's own semantics (test_caller_limits.py owns that)."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    agent_id = str(uuid.uuid4())
    db.add_agent(agent_id, ws_env.ws_id, packs=["marketing"])
    principal = _svc(ws_env.ws_id, agent_id)
    client = _client(ws_env, db, {"tok": principal})
    spy = AsyncMock(return_value=None)

    async def _override(request: Request) -> None:
        await spy(request)

    client.app.dependency_overrides[enforce_principal_rate] = _override
    resp = client.post("/v1/runs", json=_run_json(), headers={"Authorization": "Bearer tok"})

    assert resp.status_code == 202
    spy.assert_called_once()


# ── user-only routes refuse an API key outright ─────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/v1/agents"), ("get", "/v1/api-keys"), ("get", "/v1/ledger/costs")],
)
def test_an_api_key_on_a_user_only_route_is_401(method, path):
    # No dependency override: the REAL require_auth. The route allowlist contract test
    # proves these routes are require_auth-only; this proves what that means on the wire —
    # an `sk-` key is not a JWT, so it fails decode before any DB access.
    from backend.routers import agents as agents_router
    from backend.routers import ledger as ledger_router

    app = FastAPI()
    for router in (agents_router.router, api_keys_router.router, ledger_router.router):
        app.include_router(router, prefix="/v1")
    app.state.pool = object()
    register_error_handlers(app)
    client = TestClient(app, raise_server_exceptions=True)

    resp = getattr(client, method)(path, headers={"Authorization": "Bearer sk-anything"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "token_invalid"


# ── a person's bearer failures are unchanged on the swapped routes ─────────────────


def _bearer_cases() -> list[tuple[str, dict]]:
    import time

    import jwt as pyjwt

    secret = os.environ["BACKEND_JWT_SECRET"]
    now = int(time.time())
    expired = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "workspace_id": str(uuid.uuid4()),
            "type": "access",
            "iat": now - 7200,
            "exp": now - 3600,
        },
        secret,
        algorithm="HS256",
    )
    no_workspace = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "iat": now, "exp": now + 3600},
        secret,
        algorithm="HS256",
    )
    return [
        ("no header", {}),
        ("garbage", {"Authorization": "Bearer not-a-jwt"}),
        ("expired", {"Authorization": f"Bearer {expired}"}),
        ("no workspace claim", {"Authorization": f"Bearer {no_workspace}"}),
    ]


@pytest.mark.parametrize(("label", "headers"), _bearer_cases())
def test_a_bearer_failure_on_a_require_principal_route_matches_require_auth(label, headers):
    # PRD §7 G0: "a user JWT behaves exactly as today". GET /v1/runs moved to
    # require_principal; GET /v1/agents stayed on require_auth. The same bad credential
    # must get the same status, body and RFC 6750 challenge from both.
    from backend.routers import agents as agents_router

    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.include_router(agents_router.router, prefix="/v1")
    app.state.pool = object()
    register_error_handlers(app)
    client = TestClient(app, raise_server_exceptions=True)

    swapped = client.get("/v1/runs", headers=headers)
    unchanged = client.get("/v1/agents", headers=headers)

    assert unchanged.status_code == 401, label
    assert (swapped.status_code, swapped.json()) == (unchanged.status_code, unchanged.json()), label
    assert swapped.headers.get("www-authenticate") == unchanged.headers.get("www-authenticate"), (
        label
    )

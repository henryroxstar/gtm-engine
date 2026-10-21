"""Tests for profile suppressions API (PRD-324).

All fixtures use fictional names and domains (Rule §R9).
"""

from __future__ import annotations

import csv
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.deps import require_auth
from backend.errors import register_error_handlers
from backend.routers import suppressions
from gtm_core.capabilities import Entitlement
from gtm_core.paths import workspace_content_root


class _FakeConn:
    def __init__(self, existing_profiles: set[tuple[str, str]] | None = None) -> None:
        self.existing_profiles = existing_profiles or set()

    async def fetchval(self, sql, *args):
        if "FROM profiles" in sql:
            ws_id = str(args[0])
            p_name = args[1]
            return 1 if (ws_id, p_name) in self.existing_profiles else None
        return None

    async def execute(self, sql, *args):
        return "OK"

    async def fetch(self, sql, *args):
        return []


class _FakeScope:
    def __init__(self, existing_profiles: set[tuple[str, str]]) -> None:
        self.existing_profiles = existing_profiles

    @asynccontextmanager
    async def __call__(self, pool, workspace_id):
        yield _FakeConn(self.existing_profiles)


@pytest.fixture
def ws_id() -> str:
    return str(uuid.UUID("55555555-5555-5555-5555-555555555555"))


@pytest.fixture
def user_id() -> str:
    return str(uuid.UUID("66666666-6666-6666-6666-666666666666"))


@pytest.fixture
def repo_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return repo


@pytest.fixture
def client_and_scope(ws_id, user_id, repo_root, monkeypatch):
    existing_profiles = {(ws_id, "test-corp")}
    scope = _FakeScope(existing_profiles)
    monkeypatch.setattr(suppressions, "workspace_scope", scope)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(suppressions.router, prefix="/v1")

    ctx = SimpleNamespace(
        user_id=user_id,
        workspace_id=ws_id,
        entitlement=Entitlement.PRO,
    )
    app.dependency_overrides[require_auth] = lambda: ctx
    app.state.pool = object()
    app.state.cfg = SimpleNamespace(repo_root=repo_root)

    client = TestClient(app, raise_server_exceptions=False)
    return client, scope


# ── T9: Read Suppressions with Filters ────────────────────────────────────────


def test_get_suppressions_empty_ledger(client_and_scope):
    client, _ = client_and_scope
    resp = client.get("/v1/profiles/test-corp/suppressions")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["suppressions"] == []
    assert data["total"] == 0
    assert data["limit"] == 50
    assert data["offset"] == 0


def test_get_suppressions_with_pagination_and_filter(client_and_scope, repo_root, ws_id):
    client, _ = client_and_scope
    content_root = workspace_content_root(ws_id, repo_root)
    sup_file = content_root / "test-corp" / "prospects" / ".pool" / "suppression.csv"
    sup_file.parent.mkdir(parents=True, exist_ok=True)

    with sup_file.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["email", "name", "company_domain", "reason", "date", "note"]
        )
        w.writeheader()
        w.writerow(
            {
                "email": "",
                "name": "",
                "company_domain": "rival-one.test",
                "reason": "competitor",
                "date": "2026-10-01",
                "note": "direct competitor",
            }
        )
        w.writerow(
            {
                "email": "contact@client-two.example",
                "name": "Jane",
                "company_domain": "client-two.example",
                "reason": "customer",
                "date": "2026-10-02",
                "note": "existing account",
            }
        )
        w.writerow(
            {
                "email": "",
                "name": "",
                "company_domain": "rival-two.test",
                "reason": "competitor",
                "date": "2026-10-03",
                "note": "direct competitor",
            }
        )

    # Filter by reason=competitor
    resp = client.get("/v1/profiles/test-corp/suppressions?reason=competitor")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 2
    assert len(data["suppressions"]) == 2
    assert data["suppressions"][0]["company_domain"] == "rival-one.test"
    assert data["suppressions"][1]["company_domain"] == "rival-two.test"

    # Pagination: limit=1, offset=1
    resp = client.get("/v1/profiles/test-corp/suppressions?limit=1&offset=1")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3
    assert len(data["suppressions"]) == 1
    assert data["suppressions"][0]["company_domain"] == "client-two.example"


# ── T13: Batch Suppression Ingestion ──────────────────────────────────────────


def test_batch_suppression_ingestion(client_and_scope, repo_root, ws_id, user_id):
    client, _ = client_and_scope

    items = [
        {
            "company_domain": f"bad-actor-{i}.test",
            "reason": "competitor",
            "note": f"Rival {i}",
        }
        for i in range(50)
    ]
    resp = client.post("/v1/profiles/test-corp/suppressions", json={"items": items})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added"] == 50
    assert data["skipped"] == 0

    # Verify file written
    content_root = workspace_content_root(ws_id, repo_root)
    sup_file = content_root / "test-corp" / "prospects" / ".pool" / "suppression.csv"
    assert sup_file.exists()
    rows = list(csv.DictReader(sup_file.open(encoding="utf-8")))
    assert len(rows) == 50
    assert rows[0]["note"] == f"added_by:{user_id} | Rival 0"

    # Verify audit history logged
    history_file = content_root / "test-corp" / "history.jsonl"
    assert history_file.exists()
    history_lines = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert any("suppressions_added" in line for line in history_lines)

    # Re-posting same batch skips all 50
    resp = client.post("/v1/profiles/test-corp/suppressions", json={"items": items})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added"] == 0
    assert data["skipped"] == 50


def test_invalid_reason_rejected_by_schema(client_and_scope):
    client, _ = client_and_scope
    resp = client.post(
        "/v1/profiles/test-corp/suppressions",
        json={"items": [{"company_domain": "rival.test", "reason": "invalid-reason"}]},
    )
    assert resp.status_code == 422, resp.text


def test_empty_identity_rejected_by_schema(client_and_scope):
    """At least one of company_domain, email, or name must be provided."""
    client, _ = client_and_scope
    resp = client.post(
        "/v1/profiles/test-corp/suppressions",
        json={"items": [{"reason": "competitor"}]},
    )
    assert resp.status_code == 422, resp.text


def test_unknown_profile_suppressions_returns_404(client_and_scope):
    client, _ = client_and_scope
    resp = client.get("/v1/profiles/unknown-corp/suppressions")
    assert resp.status_code == 404, resp.text

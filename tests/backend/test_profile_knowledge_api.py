"""Tests for profile knowledge staging, diffing, promotion, and case study API (PRD-324).

All fixtures use fictional names and domains (Rule §R9).
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.deps import require_auth
from backend.errors import register_error_handlers
from backend.routers import profile_knowledge
from gtm_core.capabilities import Entitlement
from gtm_core.groundedness import case_study_numbers_traceable
from gtm_core.paths import workspace_content_root, workspace_profiles_root


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
def ws_a_id() -> str:
    return str(uuid.UUID("11111111-1111-1111-1111-111111111111"))


@pytest.fixture
def ws_b_id() -> str:
    return str(uuid.UUID("22222222-2222-2222-2222-222222222222"))


@pytest.fixture
def user_id() -> str:
    return str(uuid.UUID("33333333-3333-3333-3333-333333333333"))


@pytest.fixture
def repo_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return repo


@pytest.fixture
def app_and_client(ws_a_id, user_id, repo_root, monkeypatch):
    existing_profiles = {(ws_a_id, "test-corp")}
    scope = _FakeScope(existing_profiles)
    monkeypatch.setattr(profile_knowledge, "workspace_scope", scope)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(profile_knowledge.router, prefix="/v1")

    ctx = SimpleNamespace(
        user_id=user_id,
        workspace_id=ws_a_id,
        entitlement=Entitlement.PRO,
    )
    app.dependency_overrides[require_auth] = lambda: ctx
    app.state.pool = object()
    app.state.cfg = SimpleNamespace(repo_root=repo_root)

    client = TestClient(app, raise_server_exceptions=False)
    return client, scope


# ── T4: Path Traversal & Limits ───────────────────────────────────────────────


def test_path_traversal_refused_with_400(app_and_client):
    client, _ = app_and_client
    resp = client.get("/v1/profiles/test-corp/knowledge/..%2F..%2Fsecret.txt")
    assert resp.status_code == 400, resp.text


def test_unsupported_extension_refused_with_422(app_and_client):
    client, _ = app_and_client
    resp = client.get("/v1/profiles/test-corp/knowledge/test.py")
    assert resp.status_code == 422, resp.text


def test_payload_exceeding_max_length_refused_with_422(app_and_client):
    client, _ = app_and_client
    oversized = "a" * 500_001
    resp = client.post(
        "/v1/profiles/test-corp/knowledge/topic.md/stage", json={"content": oversized}
    )
    assert resp.status_code == 422, resp.text


def test_unknown_profile_returns_404(app_and_client):
    client, _ = app_and_client
    resp = client.get("/v1/profiles/unknown-profile/knowledge/case-studies.md")
    assert resp.status_code == 404, resp.text


# ── T5: Full Stage → Diff → Promote Lifecycle with Concurrency ─────────────────


def test_stage_diff_promote_lifecycle(app_and_client, repo_root, ws_a_id):
    client, _ = app_and_client
    profiles_root = workspace_profiles_root(ws_a_id, repo_root)
    content_root = workspace_content_root(ws_a_id, repo_root)

    # 1. Setup live file
    live_file = profiles_root / "test-corp" / "knowledge" / "mission.md"
    live_file.parent.mkdir(parents=True, exist_ok=True)
    live_content = "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n# Mission\nOld mission statement.\n"
    live_file.write_text(live_content, encoding="utf-8")

    # 2. GET live topic
    resp = client.get("/v1/profiles/test-corp/knowledge/mission.md")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["topic"] == "mission.md"
    assert "Old mission statement" in body["content"]
    assert body["frontmatter"]["refreshed"] == "2026-01-01"

    # 3. Initially no staged topics
    resp = client.get("/v1/profiles/test-corp/knowledge-staging")
    assert resp.status_code == 200
    assert resp.json() == {"staged_topics": []}

    # 4. Stage candidate
    candidate_content = "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n# Mission\nNew mission statement.\n"
    resp = client.post(
        "/v1/profiles/test-corp/knowledge/mission.md/stage", json={"content": candidate_content}
    )
    assert resp.status_code == 200, resp.text

    # Verify staged topics list shows mission
    resp = client.get("/v1/profiles/test-corp/knowledge-staging")
    assert resp.status_code == 200
    assert "mission" in resp.json()["staged_topics"]

    # 5. GET diff
    resp = client.get("/v1/profiles/test-corp/knowledge/mission.md/diff")
    assert resp.status_code == 200, resp.text
    diff_data = resp.json()
    assert diff_data["staged"] is True
    assert diff_data["has_changes"] is True
    assert "-Old mission statement." in diff_data["diff"]
    assert "+New mission statement." in diff_data["diff"]
    expected_sha = hashlib.sha256(candidate_content.encode("utf-8")).hexdigest()
    assert diff_data["candidate_sha"] == expected_sha

    # 6. Attempt promote with mismatched sha -> 409
    resp = client.post(
        "/v1/profiles/test-corp/knowledge/mission.md/promote",
        json={
            "candidate_sha": "wrong-sha-0000000000000000000000000000000000000000000000000000000000"
        },
    )
    assert resp.status_code == 409, resp.text

    # 7. Promote with matching sha -> 200
    resp = client.post(
        "/v1/profiles/test-corp/knowledge/mission.md/promote",
        json={"candidate_sha": expected_sha},
    )
    assert resp.status_code == 200, resp.text
    promote_data = resp.json()
    assert promote_data["status"] == "promoted"
    today_iso = date.today().isoformat()
    assert promote_data["refreshed"] == today_iso

    # Assert live file updated
    promoted_text = live_file.read_text(encoding="utf-8")
    assert "New mission statement." in promoted_text
    assert f"refreshed: {today_iso}" in promoted_text

    # Assert staged file unlinked
    staged_file = content_root / "test-corp" / "knowledge-staging" / "mission.md"
    assert not staged_file.exists()


# ── T6: Case Study Merge & Direct Save ─────────────────────────────────────────


def test_case_study_stage_and_merge(app_and_client, repo_root, ws_a_id):
    client, _ = app_and_client
    profiles_root = workspace_profiles_root(ws_a_id, repo_root)

    # Create existing case-studies.md
    cs_file = profiles_root / "test-corp" / "knowledge" / "case-studies.md"
    cs_file.parent.mkdir(parents=True, exist_ok=True)
    existing_cs = (
        "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n"
        "# Case Studies\n\n"
        "## Case study 1\n"
        "- **Customer:** Rival Alpha\n"
        "- **Problem:** Latency\n"
        "- **What you did:** Cached queries\n"
        "- **Result:** 2x faster\n"
    )
    cs_file.write_text(existing_cs, encoding="utf-8")

    # Stage a second case study
    req = {
        "customer": "Beta Corp",
        "problem": "Manual prospecting",
        "action": "Automated workflow",
        "result": "Saved 45% time",
        "auto_promote": False,
    }
    resp = client.post("/v1/profiles/test-corp/case-studies/stage", json=req)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["staged"] is True
    assert data["promoted"] is False
    assert data["customer"] == "Beta Corp"

    # Verify staged file contains both Case study 1 and Case study 2
    content_root = workspace_content_root(ws_a_id, repo_root)
    staged_cs = content_root / "test-corp" / "knowledge-staging" / "case-studies.md"
    staged_text = staged_cs.read_text(encoding="utf-8")
    assert "## Case study 1" in staged_text
    assert "## Case study 2" in staged_text
    assert "- **Customer:** Beta Corp" in staged_text
    assert "- **Result:** Saved 45% time" in staged_text


def test_case_study_auto_promote_and_grounding(app_and_client, repo_root, ws_a_id):
    client, _ = app_and_client
    profiles_root = workspace_profiles_root(ws_a_id, repo_root)

    req = {
        "customer": "Delta Systems",
        "problem": "High churn",
        "action": "Deployed proactive monitoring",
        "result": "Reduced churn by 45%",
        "auto_promote": True,
    }
    resp = client.post("/v1/profiles/test-corp/case-studies/stage", json=req)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["staged"] is False
    assert data["promoted"] is True

    # Assert live file updated
    live_cs = profiles_root / "test-corp" / "knowledge" / "case-studies.md"
    assert live_cs.exists()
    live_text = live_cs.read_text(encoding="utf-8")
    assert "Delta Systems" in live_text

    # T7: Grounding agreement
    traceable, untraceable = case_study_numbers_traceable("Reduced churn by 45%", live_text)
    assert traceable is True
    assert untraceable == []


# ── T8: Multi-Tenant Workspace Filesystem Isolation ───────────────────────────


def test_multi_tenant_workspace_isolation(repo_root, ws_a_id, ws_b_id, user_id, monkeypatch):
    # Setup two workspaces both having "shared-name" profile
    existing_profiles = {(ws_a_id, "shared-name"), (ws_b_id, "shared-name")}
    scope = _FakeScope(existing_profiles)
    monkeypatch.setattr(profile_knowledge, "workspace_scope", scope)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(profile_knowledge.router, prefix="/v1")
    app.state.pool = object()
    app.state.cfg = SimpleNamespace(repo_root=repo_root)

    # Client A
    ctx_a = SimpleNamespace(user_id=user_id, workspace_id=ws_a_id, entitlement=Entitlement.PRO)
    app.dependency_overrides[require_auth] = lambda: ctx_a
    client_a = TestClient(app, raise_server_exceptions=False)

    # Client A stages a file
    client_a.post(
        "/v1/profiles/shared-name/knowledge/secret.md/stage",
        json={"content": "# Secret A content"},
    )

    # Client B switches in
    ctx_b = SimpleNamespace(user_id=user_id, workspace_id=ws_b_id, entitlement=Entitlement.PRO)
    app.dependency_overrides[require_auth] = lambda: ctx_b
    client_b = TestClient(app, raise_server_exceptions=False)

    # Client B staged list must be empty
    resp_b = client_b.get("/v1/profiles/shared-name/knowledge-staging")
    assert resp_b.status_code == 200
    assert resp_b.json() == {"staged_topics": []}

    # Filesystem check: WS B tree contains no files from WS A
    ws_b_tree = repo_root / "data" / "workspaces" / ws_b_id
    if ws_b_tree.exists():
        for f in ws_b_tree.rglob("*"):
            if f.is_file():
                assert "Secret A" not in f.read_text(encoding="utf-8")


# ── T12: Staging Discard & Unstaged Diff Status ────────────────────────────────


def test_staging_discard_and_unstaged_diff_status(app_and_client, repo_root, ws_a_id):
    client, _ = app_and_client
    profiles_root = workspace_profiles_root(ws_a_id, repo_root)

    # Create live file
    live_file = profiles_root / "test-corp" / "knowledge" / "overview.md"
    live_file.parent.mkdir(parents=True, exist_ok=True)
    live_file.write_text("# Overview\nOriginal text.\n", encoding="utf-8")

    # When no candidate staged, diff returns 200 with has_changes=False, staged=False
    resp = client.get("/v1/profiles/test-corp/knowledge/overview.md/diff")
    assert resp.status_code == 200, resp.text
    diff_data = resp.json()
    assert diff_data["has_changes"] is False
    assert diff_data["staged"] is False
    assert diff_data["diff"] == ""
    assert diff_data["candidate_sha"] is None

    # Stage a candidate
    client.post(
        "/v1/profiles/test-corp/knowledge/overview.md/stage",
        json={"content": "# Overview\nModified text.\n"},
    )

    # Discard candidate via DELETE .../stage
    resp = client.delete("/v1/profiles/test-corp/knowledge/overview.md/stage")
    assert resp.status_code == 200, resp.text

    content_root = workspace_content_root(ws_a_id, repo_root)
    staged_path = content_root / "test-corp" / "knowledge-staging" / "overview.md"
    assert not staged_path.exists()


def test_stage_preserves_live_frontmatter_metadata(app_and_client, repo_root, ws_a_id):
    """Staging body-only markdown retains existing live frontmatter metadata upon promotion."""
    client, _ = app_and_client
    profiles_root = workspace_profiles_root(ws_a_id, repo_root)
    content_root = workspace_content_root(ws_a_id, repo_root)

    live_file = profiles_root / "test-corp" / "knowledge" / "custom.md"
    live_file.parent.mkdir(parents=True, exist_ok=True)
    live_file.write_text(
        "---\nowner: alice\ntags: [b2b, enterprise]\n---\n# Custom Body\n", encoding="utf-8"
    )

    # Read topic
    resp = client.get("/v1/profiles/test-corp/knowledge/custom.md")
    assert resp.status_code == 200
    assert resp.json()["frontmatter"]["owner"] == "alice"

    # Stage body without frontmatter
    resp = client.post(
        "/v1/profiles/test-corp/knowledge/custom.md/stage",
        json={"content": "# Updated Custom Body\n"},
    )
    assert resp.status_code == 200

    # Diff
    diff_resp = client.get("/v1/profiles/test-corp/knowledge/custom.md/diff")
    assert diff_resp.status_code == 200
    sha = diff_resp.json()["candidate_sha"]

    # Promote
    prom_resp = client.post(
        "/v1/profiles/test-corp/knowledge/custom.md/promote",
        json={"candidate_sha": sha},
    )
    assert prom_resp.status_code == 200

    # Verify promoted live file preserved owner and tags
    updated_text = live_file.read_text(encoding="utf-8")
    assert "owner: alice" in updated_text
    assert "b2b" in updated_text
    assert "# Updated Custom Body" in updated_text

    # Verify history.jsonl
    history_file = content_root / "test-corp" / "history.jsonl"
    assert history_file.exists()
    assert "knowledge_promoted" in history_file.read_text(encoding="utf-8")


def test_case_study_auto_promote_rejects_unreviewed_staged_changes(app_and_client):
    """Auto-promote must refuse if an unreviewed staged candidate already exists."""
    client, _ = app_and_client

    # Stage a manual draft first
    client.post(
        "/v1/profiles/test-corp/knowledge/case-studies.md/stage",
        json={"content": "# Unreviewed draft\n"},
    )

    # Attempt to auto-promote a new case study
    req = {
        "customer": "Epsilon Inc",
        "problem": "Latency",
        "action": "Optimized queries",
        "result": "50% faster",
        "auto_promote": True,
    }
    resp = client.post("/v1/profiles/test-corp/case-studies/stage", json=req)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "staged_candidate_exists"
    assert "Cannot auto_promote while unreviewed staged candidate exists" in detail["message"]


def test_rate_limiting_enforced_at_30_per_minute(ws_a_id, user_id, repo_root, monkeypatch):
    """Calling an endpoint more than 30 times in a minute triggers 429 Too Many Requests."""
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from backend.ratelimit import limiter

    existing_profiles = {(ws_a_id, "test-corp")}
    scope = _FakeScope(existing_profiles)
    monkeypatch.setattr(profile_knowledge, "workspace_scope", scope)

    app = FastAPI()
    register_error_handlers(app)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(profile_knowledge.router, prefix="/v1")

    ctx = SimpleNamespace(user_id=user_id, workspace_id=ws_a_id, entitlement=Entitlement.PRO)
    app.dependency_overrides[require_auth] = lambda: ctx
    app.state.pool = object()
    app.state.cfg = SimpleNamespace(repo_root=repo_root)

    limiter.reset()
    limiter.enabled = True
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            codes = [
                client.get("/v1/profiles/test-corp/knowledge-staging").status_code
                for _ in range(35)
            ]
    finally:
        limiter.enabled = False
        limiter.reset()

    assert codes[:30] == [200] * 30
    assert 429 in codes[30:]

"""A11 — run artifacts: attribution, listing, download (PRD §3 T1–T14).

T13 (schema count guard + fixture validation) lives in tests/contracts/. T7's
live-RLS mirror is the run_artifacts registration in test_rls_live.py. Every
denial case here keeps a positive-control twin (fail-closed probe rule).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import artifacts as artifacts_mod  # noqa: E402
from backend.callers.rest import require_principal  # noqa: E402
from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    REPO,
    RUN_ARTIFACT_SCHEMA,
    SCOPE_MODULES,  # noqa: E402
    StateConn,
    drive_gate,
    fake_executor,
    pack_run_harness,
    user_principal,
    validate_frame,
)
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402
from tests.contracts.minijsonschema import validate as schema_validate  # noqa: E402

# ── scanner units (T2/T3/T4 + containment) ────────────────────────────────────


class _MultiPatch:
    """start()/stop() (and `with`) over several patchers — the shape this fixture
    stashes on the client, used both ways in this module."""

    def __init__(self, patchers):
        self._patchers = patchers

    def start(self):
        for p in self._patchers:
            p.start()
        return self

    def stop(self):
        for p in reversed(self._patchers):
            p.stop()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def test_exclusion_list_never_snapshots_control_files(tmp_path):
    """T2: ledgers/control files, dotfiles, and temp suffixes are invisible to
    the scanner; a real deliverable in the same tree is the positive control."""
    root = tmp_path / "profile"
    (root / "runs").mkdir(parents=True)
    (root / "accounts" / "acme").mkdir(parents=True)
    (root / "history.jsonl").write_text("{}")
    (root / "costs.jsonl").write_text("{}")
    (root / "settings.json").write_text("{}")
    (root / "runs" / "x.json").write_text("{}")
    (root / ".hidden").write_text("x")
    (root / "accounts" / ".DS_Store").write_text("x")
    (root / "accounts" / "acme" / "draft.tmp").write_text("x")
    (root / "accounts" / "acme" / "download.partial").write_text("x")
    (root / "accounts" / "acme" / "busy.lock").write_text("x")
    (root / "accounts" / "acme" / "dossier.docx").write_bytes(b"real deliverable")

    snap = artifacts_mod.snapshot_tree(root)
    assert list(snap) == [str(os.path.join("accounts", "acme", "dossier.docx"))]


def test_diff_detects_create_and_modify_then_settles(tmp_path):
    """T3 + T4: a rewrite (same path, new bytes) re-registers with the new sha;
    an unchanged tree between two later snapshots yields nothing."""
    root = tmp_path / "profile"
    (root / "accounts").mkdir(parents=True)
    target = root / "accounts" / "list.csv"
    target.write_bytes(b"v1 bytes")
    snap1 = artifacts_mod.snapshot_tree(root)

    v2 = "v2 bytes — different".encode()
    target.write_bytes(v2)
    os.utime(target, ns=(1, 1))  # force a distinct mtime_ns even on coarse clocks
    snap2 = artifacts_mod.snapshot_tree(root)
    found = artifacts_mod.diff_new_artifacts(root, "prof", snap1, snap2)
    assert [a.rel_path for a in found] == [str(os.path.join("prof", "accounts", "list.csv"))]
    assert found[0].sha256 == hashlib.sha256(v2).hexdigest()

    snap3 = artifacts_mod.snapshot_tree(root)
    assert artifacts_mod.diff_new_artifacts(root, "prof", snap2, snap3) == []


def test_media_type_allowlist_and_fallback():
    assert artifacts_mod.media_type_for("report.docx").startswith("application/vnd.openxml")
    assert artifacts_mod.media_type_for("page.HTML") == "text/html"
    assert artifacts_mod.media_type_for("weird.bin") == artifacts_mod.FALLBACK_MEDIA_TYPE
    assert artifacts_mod.media_type_for("noext") == artifacts_mod.FALLBACK_MEDIA_TYPE


def test_resolve_contained_blocks_escapes(tmp_path):
    """T9 (unit): traversal rel_paths and symlink escapes resolve to None; a real
    contained file is the positive control."""
    root = tmp_path / "content"
    (root / "prof" / "accounts").mkdir(parents=True)
    inside = root / "prof" / "accounts" / "ok.md"
    inside.write_text("fine")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = root / "prof" / "accounts" / "link.txt"
    link.symlink_to(outside)

    assert artifacts_mod.resolve_contained(root, "prof/accounts/ok.md") == inside.resolve()
    assert artifacts_mod.resolve_contained(root, "../outside.txt") is None
    assert artifacts_mod.resolve_contained(root, "prof/accounts/link.txt") is None
    assert artifacts_mod.resolve_contained(root, "prof/accounts/missing.md") is None
    assert artifacts_mod.resolve_contained(root, "") is None  # the root itself is not a file


# ── registration through a pack run (T1/T5) ───────────────────────────────────

_FILES = {
    "studio": [
        ("accounts/acme/report.docx", b"docx bytes here"),
        ("accounts/acme/prospects.csv", b"name,email\nfictional,someone@acme.example\n"),
        ("accounts/acme/notes.weird", b"unknown extension bytes"),
    ]
}


async def _run_pack(ws_env, conn, executor, run_id: str):
    with pack_run_harness(conn, executor):
        await runs_router._execute_pack_run(
            MagicMock(),
            REPO,
            ws_env.ws_id,
            run_id,
            PROFILE,
            "marketing",
            "linkedin-post",
            {},
            entitlement="pro_plus",
        )


def test_run_registers_written_files_with_attribution(ws_env):
    """T1: three files written during `studio` → three rows; sha/size/media_type
    match the bytes; node_id names the producing node; a `file` content event
    goes out for each with the registered artifact_id."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id = str(uuid.uuid4())
    conn = StateConn()

    runs_router._run_subscribers.pop(run_id, None)
    q = runs_router._subscribe(run_id)
    try:
        asyncio.run(_run_pack(ws_env, conn, fake_executor([], files_by_stage=_FILES), run_id))
        frames = []
        while True:
            try:
                frames.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
    finally:
        runs_router._unsubscribe(run_id, q)

    by_rel = {row["rel_path"]: row for row in conn.artifacts.values()}
    assert set(by_rel) == {
        str(os.path.join(PROFILE, "accounts", "acme", "report.docx")),
        str(os.path.join(PROFILE, "accounts", "acme", "prospects.csv")),
        str(os.path.join(PROFILE, "accounts", "acme", "notes.weird")),
    }
    for rel, data in _FILES["studio"]:
        row = by_rel[str(os.path.join(PROFILE, rel))]
        assert row["sha256"] == hashlib.sha256(data).hexdigest()
        assert row["size_bytes"] == len(data)
        assert row["node_id"] == "studio"
    assert (
        by_rel[str(os.path.join(PROFILE, "accounts", "acme", "notes.weird"))]["media_type"]
        == artifacts_mod.FALLBACK_MEDIA_TYPE
    )

    file_blocks = [
        d["block"] for e, d in frames if e == "content" and d["block"].get("type") == "file"
    ]
    assert len(file_blocks) == 3
    registered_ids = {row["id"] for row in conn.artifacts.values()}
    assert {b["props"]["artifact_id"] for b in file_blocks} == registered_ids
    for b in file_blocks:
        assert not validate_frame("content", {"run_id": run_id, "mode": "replace", "block": b})


def test_artifacts_registered_while_awaiting_approval(ws_env):
    """T5: a file produced BEFORE the gate is registered while the run is still
    paused — a dossier must be reviewable at the gate, not only at terminal."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    pending = ws_env.content_root / PROFILE / "plans" / ".pending"
    pending.mkdir(parents=True)
    (pending / "2026-32.draft.json").write_text("[]")

    run_id = str(uuid.uuid4())
    conn = StateConn()
    seen_while_paused: list[str] = []
    files = {"radar": [("research/scan.md", b"# scan output")]}

    async def _go():
        task = asyncio.create_task(
            _run_pack(
                ws_env, conn, fake_executor([], awaiting_on="plan", files_by_stage=files), run_id
            )
        )
        for _ in range(800):
            if run_id in runs_router._gate_events:
                break
            await asyncio.sleep(0.005)
        seen_while_paused.extend(row["rel_path"] for row in conn.artifacts.values())
        await drive_gate(run_id, "reject")
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(_go())
    assert seen_while_paused == [str(os.path.join(PROFILE, "research", "scan.md"))]


# ── routes (T6–T12) ───────────────────────────────────────────────────────────


RUN_ID = "00000000-0000-0000-0000-000000000010"


def _route_client(ws_env, conn):
    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    app.state.cfg = MagicMock(repo_root=REPO)
    ctx = WorkspaceCtx(user_id="u", workspace_id=ws_env.ws_id, entitlement="pro")
    app.dependency_overrides[require_auth] = lambda: ctx
    # Fleet Phase A (Task 3): runs routes now depend on require_principal.
    app.dependency_overrides[require_principal] = lambda: user_principal(ctx)

    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    client = TestClient(app)
    # Every module that binds workspace_scope — the handler reads through the queries
    # service since Phase 1b, so patching the router alone leaves the real pool live.
    client._scope_patch = _MultiPatch(  # type: ignore[attr-defined]
        [patch.object(m, "workspace_scope", _scope) for m in SCOPE_MODULES]
    )
    client._app = app  # type: ignore[attr-defined]
    return client


def _artifact_row(ws_env, rel_path: str, name: str, media_type: str = "text/markdown") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "run_id": RUN_ID,
        "rel_path": rel_path,
        "name": name,
        "size_bytes": 12,
        "media_type": media_type,
        "sha256": "ab" * 32,
        "node_id": "studio",
        "created_at": datetime.now(UTC),
    }


def test_listing_items_schema_valid_and_cross_tenant_404(ws_env):
    """T6 + T7: items validate against run-artifact.schema.json; an unknown or
    cross-tenant run (scoped query finds nothing) is a plain 404."""
    rows = [
        _artifact_row(ws_env, f"{PROFILE}/accounts/acme/a.md", "a.md"),
        _artifact_row(ws_env, f"{PROFILE}/accounts/acme/b.csv", "b.csv", "text/csv"),
    ]
    conn = AsyncMock()
    conn.fetchrow.return_value = {"?column?": 1}
    conn.fetch.return_value = rows
    client = _route_client(ws_env, conn)
    with client._scope_patch:  # type: ignore[attr-defined]
        resp = client.get(f"/v1/runs/{RUN_ID}/artifacts")
    assert resp.status_code == 200
    items = resp.json()["artifacts"]
    assert [i["name"] for i in items] == ["a.md", "b.csv"]
    for item in items:
        errors = schema_validate(item, RUN_ARTIFACT_SCHEMA)
        assert not errors, (item["name"], errors)

    conn.fetchrow.return_value = None  # cross-tenant / unknown run
    conn.fetch.return_value = []
    with client._scope_patch:  # type: ignore[attr-defined]
        assert client.get(f"/v1/runs/{RUN_ID}/artifacts").status_code == 404


def test_download_bytes_and_headers(ws_env):
    """T8: byte-equality with what the skill wrote; attachment + RFC 5987
    filename* + nosniff + no-store on every response."""
    payload = "# dossier — résumé\n".encode()
    rel = f"{PROFILE}/accounts/acme/dossier–é.md"
    path = ws_env.content_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)

    row = _artifact_row(ws_env, rel, "dossier–é.md")
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "rel_path": row["rel_path"],
        "name": row["name"],
        "media_type": row["media_type"],
    }
    client = _route_client(ws_env, conn)
    with client._scope_patch:  # type: ignore[attr-defined]
        resp = client.get(f"/v1/runs/{RUN_ID}/artifacts/{row['id']}")
    assert resp.status_code == 200
    assert resp.content == payload
    assert resp.headers["content-type"].startswith("text/markdown")
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "filename*=UTF-8''" in disposition
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "no-store"


def test_download_containment_refused_404(ws_env):
    """T9 (route): a tampered row whose resolution escapes the workspace root —
    traversal or symlink — is 404, even though the target file EXISTS."""
    outside = ws_env.content_root.parent / "outside-secret.txt"
    outside.write_text("cross-tenant bytes")
    link_rel = f"{PROFILE}/accounts/link.md"
    link = ws_env.content_root / link_rel
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)

    conn = AsyncMock()
    client = _route_client(ws_env, conn)
    for rel_path in ("../outside-secret.txt", link_rel):
        conn.fetchrow.return_value = {
            "rel_path": rel_path,
            "name": "x.md",
            "media_type": "text/markdown",
        }
        with client._scope_patch:  # type: ignore[attr-defined]
            resp = client.get(f"/v1/runs/{RUN_ID}/artifacts/{uuid.uuid4()}")
        assert resp.status_code == 404, rel_path
        assert "cross-tenant" not in resp.text


def test_download_registered_but_gone_410(ws_env):
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "rel_path": f"{PROFILE}/accounts/acme/deleted.docx",
        "name": "deleted.docx",
        "media_type": "application/octet-stream",
    }
    client = _route_client(ws_env, conn)
    with client._scope_patch:  # type: ignore[attr-defined]
        resp = client.get(f"/v1/runs/{RUN_ID}/artifacts/{uuid.uuid4()}")
    assert resp.status_code == 410
    assert resp.json()["detail"]["code"] == "artifact_gone"


def test_download_unauthenticated_401_and_unknown_artifact_404(ws_env):
    """T11: bad token → 401; a valid workspace fetching an id it can't see → 404
    (the workspace-B-token case collapses to the scoped-query-finds-nothing path)."""
    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    app.state.cfg = MagicMock(repo_root=REPO)
    with TestClient(app) as anon:
        resp = anon.get(
            f"/v1/runs/{RUN_ID}/artifacts/{uuid.uuid4()}",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    assert resp.status_code == 401

    conn = AsyncMock()
    conn.fetchrow.return_value = None
    client = _route_client(ws_env, conn)
    with client._scope_patch:  # type: ignore[attr-defined]
        assert client.get(f"/v1/runs/{RUN_ID}/artifacts/{uuid.uuid4()}").status_code == 404


def test_download_rate_limited_429(ws_env):
    """T11 smoke: the download route enforces 30/minute once the (pytest-disabled)
    limiter is re-enabled — slowapi wired exactly as backend/main.py does."""
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from backend.ratelimit import limiter

    rel = f"{PROFILE}/accounts/acme/limited.md"
    path = ws_env.content_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("bytes")
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "rel_path": rel,
        "name": "limited.md",
        "media_type": "text/markdown",
    }

    client = _route_client(ws_env, conn)
    client._app.state.limiter = limiter  # type: ignore[attr-defined]
    client._app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[attr-defined]

    artifact_id = str(uuid.uuid4())
    limiter.reset()
    limiter.enabled = True
    try:
        with client._scope_patch:  # type: ignore[attr-defined]
            codes = [
                client.get(f"/v1/runs/{RUN_ID}/artifacts/{artifact_id}").status_code
                for _ in range(31)
            ]
    finally:
        limiter.enabled = False
        limiter.reset()
    assert codes[:30] == [200] * 30
    assert codes[30] == 429


def test_cascade_clause_present_in_migration():
    """T12 (static): the FK cascade that makes account-DELETE wipe artifact rows
    is declared in V015; tenant isolation itself is proven by the live-RLS tier
    (run_artifacts is registered in test_rls_live.py's _TENANT_TABLES)."""
    sql = (REPO / "backend" / "schema" / "V015__run_nodes_blocks_artifacts.sql").read_text()
    artifact_ddl = sql.split("CREATE TABLE IF NOT EXISTS run_artifacts", 1)[1]
    assert "REFERENCES runs(id) ON DELETE CASCADE" in artifact_ddl
    assert "REFERENCES workspaces(id) ON DELETE CASCADE" in artifact_ddl


# ── wire contract (T14) ───────────────────────────────────────────────────────


def test_file_block_contract_no_url_vocabulary():
    """T14: a file block validates as a content event, carries mandatory
    fallback_text, and its props contain NO url/href/host-shaped key — the
    client composes the download URL from the contract route."""
    art = SimpleNamespace(
        name="report.docx",
        size_bytes=1234,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        sha256="cd" * 32,
    )
    block = runs_router._file_block("11111111-2222-3333-4444-555555555555", art)
    errs = validate_frame("content", {"run_id": RUN_ID, "mode": "replace", "block": block})
    assert not errs, errs
    assert block["fallback_text"] == "Attachment: report.docx (1234 bytes)"
    assert block["props"]["artifact_id"] == "11111111-2222-3333-4444-555555555555"
    forbidden = ("url", "href", "host", "token")
    for key in block["props"]:
        assert not any(f in key.lower() for f in forbidden), key
    # And nothing url-shaped anywhere in the serialized block.
    assert "http" not in json.dumps(block)


@pytest.fixture(autouse=True)
def _clean_slate():
    """Belt-and-braces: no leaked gate waiters/subscribers between tests."""
    yield
    runs_router._gate_decisions.pop(RUN_ID, None)
    runs_router._gate_events.pop(RUN_ID, None)

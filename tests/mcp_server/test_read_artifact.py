"""Unit and contract tests for read_artifact MCP tool (T1-T7)."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from mcp.server.fastmcp import Context

from backend.callers.principal import Principal
from backend.services.runs.queries import resolve_artifact_path
from gtm_core.capabilities import Entitlement
from gtm_core.paths import workspace_content_root
from mcp_server import server
from mcp_server import setup_required as sr
from mcp_server.tasks import read_artifact_task

_WS_ID = "11111111-1111-1111-1111-111111111111"
_WS_OTHER = "99999999-9999-9999-9999-999999999999"
_RUN_ID = "22222222-2222-2222-2222-222222222222"
_ARTIFACT_ID = "33333333-3333-3333-3333-333333333333"
_AGENT_1 = "44444444-4444-4444-4444-444444444444"
_AGENT_2 = "55555555-5555-5555-5555-555555555555"


def _run(coro):
    return asyncio.run(coro)


def _make_pool(row=None, run_agent_id=_AGENT_1, read_scope="own"):
    conn = MagicMock()
    conn.execute = AsyncMock()

    async def _fetchrow(q, *args):
        if "FROM run_artifacts" in q:
            # Check workspace and IDs
            if len(args) >= 3 and str(args[2]) == _WS_ID:
                return row
            return None
        if "FROM runs" in q and "agent_id" in q:
            return {"agent_id": run_agent_id} if run_agent_id else None
        if "FROM agents" in q:
            return {"read_scope": read_scope}
        return None

    async def _fetchval(q, *args):
        if "FROM agents" in q and "read_scope" in q:
            return read_scope
        return "pro"

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.fetchval = AsyncMock(side_effect=_fetchval)
    conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_ctx():
    ctx = MagicMock(spec=Context)
    req = MagicMock()
    req.headers = {"authorization": "Bearer sk-testkey"}
    ctx.request_context = MagicMock()
    ctx.request_context.request = req
    return ctx


class _Cfg:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root


def test_t1_happy_path_read_artifact(tmp_path, monkeypatch):
    """T1: Happy path UTF-8 Markdown/JSON/CSV/SVG read and registration checks."""
    content_root = workspace_content_root(_WS_ID, tmp_path)
    content_root.mkdir(parents=True)
    rel_path = "accounts/acme/brief.md"
    file_path = content_root / rel_path
    file_path.parent.mkdir(parents=True)
    doc_text = "# Acme Brief\nCategory: Robotics\n"
    file_path.write_text(doc_text, encoding="utf-8")
    size_bytes = len(doc_text.encode("utf-8"))
    sha256 = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()

    row = {
        "id": _ARTIFACT_ID,
        "run_id": _RUN_ID,
        "rel_path": rel_path,
        "name": "brief.md",
        "size_bytes": size_bytes,
        "media_type": "text/markdown",
        "sha256": sha256,
    }
    pool = _make_pool(row=row)

    principal = Principal(
        kind="user",
        subject="user-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="jwt",
    )
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal", AsyncMock(return_value=principal)
    )

    ctx = _make_ctx()
    cfg = _Cfg(tmp_path)
    res = _run(
        read_artifact_task(
            pool=pool,
            ctx=ctx,
            run_id=_RUN_ID,
            artifact_id=_ARTIFACT_ID,
            cfg=cfg,
        )
    )

    assert res["artifact_id"] == _ARTIFACT_ID
    assert res["run_id"] == _RUN_ID
    assert res["name"] == "brief.md"
    assert res["rel_path"] == rel_path
    assert res["media_type"] == "text/markdown"
    assert res["size_bytes"] == size_bytes
    assert res["sha256"] == sha256
    assert res["content"] == doc_text
    assert res["offset"] == 0
    assert res["bytes_returned"] == size_bytes
    assert res["truncated"] is False
    assert res["next_offset"] is None
    assert res["next_step"] is None
    assert res["download_path"] == f"/v1/runs/{_RUN_ID}/artifacts/{_ARTIFACT_ID}"

    # Verify tool registration on server.mcp
    tools = {t.name for t in _run(server.mcp.list_tools())}
    assert "read_artifact" in tools
    assert "read_artifact" in sr.PROFILE_FREE

    # Verify server.py <= 621 lines
    server_path = Path(server.__file__)
    lines = len(server_path.read_text(encoding="utf-8").splitlines())
    assert lines <= 621, f"mcp_server/server.py exceeded 621 lines: {lines}"


def test_t2_malformed_uuid_and_cross_tenant_isolation(monkeypatch):
    """T2: Malformed UUID returns invalid_input; cross-tenant or unknown returns not_found."""
    principal = Principal(
        kind="user",
        subject="user-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="jwt",
    )
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal", AsyncMock(return_value=principal)
    )
    pool = _make_pool(row=None)
    ctx = _make_ctx()

    # Malformed UUID syntax
    bad_res = _run(
        read_artifact_task(pool=pool, ctx=ctx, run_id="not-a-uuid", artifact_id=_ARTIFACT_ID)
    )
    assert bad_res["error"] == "invalid_input"
    assert "UUID" in bad_res["message"]

    bad_res2 = _run(
        read_artifact_task(pool=pool, ctx=ctx, run_id=_RUN_ID, artifact_id="not-a-uuid")
    )
    assert bad_res2["error"] == "invalid_input"

    # Unknown or cross-tenant UUID (row is None)
    not_found_res = _run(
        read_artifact_task(pool=pool, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID)
    )
    assert not_found_res == {"error": "not_found", "message": "Artifact not found"}


def test_t3_service_principal_agent_scope(tmp_path, monkeypatch):
    """T3: Service principal (sk-) agent-scope enforcement (own vs workspace)."""
    content_root = workspace_content_root(_WS_ID, tmp_path)
    content_root.mkdir(parents=True)
    file_path = content_root / "brief.md"
    file_path.write_text("Hello", encoding="utf-8")

    row = {
        "id": _ARTIFACT_ID,
        "run_id": _RUN_ID,
        "rel_path": "brief.md",
        "name": "brief.md",
        "size_bytes": 5,
        "media_type": "text/markdown",
        "sha256": hashlib.sha256(b"Hello").hexdigest(),
    }

    # Run belongs to AGENT_2
    pool_agent2 = _make_pool(row=row, run_agent_id=_AGENT_2, read_scope="own")

    # Principal is AGENT_1 with read_scope="own" -> must get not_found
    service_principal_1 = Principal(
        kind="service",
        subject="key-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="api_key",
        agent_id=_AGENT_1,
    )
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal", AsyncMock(return_value=service_principal_1)
    )

    ctx = _make_ctx()
    cfg = _Cfg(tmp_path)
    res_denied = _run(
        read_artifact_task(
            pool=pool_agent2, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg
        )
    )
    assert res_denied == {"error": "not_found", "message": "Artifact not found"}

    # Coordinator principal with read_scope="workspace" -> succeeds
    pool_coordinator = _make_pool(row=row, run_agent_id=_AGENT_2, read_scope="workspace")
    res_coordinator = _run(
        read_artifact_task(
            pool=pool_coordinator, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg
        )
    )
    assert res_coordinator["content"] == "Hello"

    # Principal is AGENT_2 with read_scope="own" -> succeeds reading own
    service_principal_2 = Principal(
        kind="service",
        subject="key-2",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="api_key",
        agent_id=_AGENT_2,
    )
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal", AsyncMock(return_value=service_principal_2)
    )
    res_own = _run(
        read_artifact_task(
            pool=pool_agent2, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg
        )
    )
    assert res_own["content"] == "Hello"


def test_t4_containment_first_path_escape(tmp_path, monkeypatch):
    """T4: Lexical traversal and escaping symlink return escaped (no existence oracle)."""
    content_root = workspace_content_root(_WS_ID, tmp_path)
    content_root.mkdir(parents=True)

    # 1. Lexical traversal escaping root
    p1, r1 = resolve_artifact_path(content_root, "../../nonexistent-outside-file")
    assert p1 is None
    assert r1 == "escaped"

    # 2. Escaping symlink to outside target
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    symlink_file = content_root / "escape_link.md"
    symlink_file.symlink_to(outside_file)

    p2, r2 = resolve_artifact_path(content_root, "escape_link.md")
    assert p2 is None
    assert r2 == "escaped"

    # Broken symlink pointing outside
    broken_link = content_root / "broken_link.md"
    broken_link.symlink_to(outside_dir / "does_not_exist.txt")
    p3, r3 = resolve_artifact_path(content_root, "broken_link.md")
    assert p3 is None
    assert r3 == "escaped"

    # read_artifact_task returns not_found on escaped
    row = {
        "id": _ARTIFACT_ID,
        "run_id": _RUN_ID,
        "rel_path": "../../nonexistent-outside-file",
        "name": "escape.md",
        "size_bytes": 10,
        "media_type": "text/markdown",
        "sha256": "abc",
    }
    pool = _make_pool(row=row)
    principal = Principal(
        kind="user",
        subject="user-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="jwt",
    )
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal", AsyncMock(return_value=principal)
    )
    ctx = _make_ctx()
    cfg = _Cfg(tmp_path)
    res = _run(
        read_artifact_task(pool=pool, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg)
    )
    assert res == {"error": "not_found", "message": "Artifact not found"}


def test_t5_deleted_file_and_r2_handling(tmp_path, monkeypatch):
    """T5: Deleted file returns artifact_gone; includes download_path if R2 configured."""
    content_root = workspace_content_root(_WS_ID, tmp_path)
    content_root.mkdir(parents=True)

    row = {
        "id": _ARTIFACT_ID,
        "run_id": _RUN_ID,
        "rel_path": "deleted.md",
        "name": "deleted.md",
        "size_bytes": 10,
        "media_type": "text/markdown",
        "sha256": "abc",
    }
    pool = _make_pool(row=row)
    principal = Principal(
        kind="user",
        subject="user-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="jwt",
    )
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal", AsyncMock(return_value=principal)
    )
    ctx = _make_ctx()
    cfg = _Cfg(tmp_path)

    # When R2 is not configured
    monkeypatch.setattr("gtm_core.r2_client.is_configured", lambda: False)
    res_no_r2 = _run(
        read_artifact_task(pool=pool, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg)
    )
    assert res_no_r2["error"] == "artifact_gone"
    assert "download_path" not in res_no_r2

    # When R2 is configured
    monkeypatch.setattr("gtm_core.r2_client.is_configured", lambda: True)
    res_r2 = _run(
        read_artifact_task(pool=pool, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg)
    )
    assert res_r2["error"] == "artifact_gone"
    assert res_r2["download_path"] == f"/v1/runs/{_RUN_ID}/artifacts/{_ARTIFACT_ID}"


def test_t6_binary_and_invalid_utf8_refusal_and_defanging(tmp_path, monkeypatch):
    """T6: Binary media types, NUL bytes, and invalid UTF-8 are refused; name defanging verified."""
    content_root = workspace_content_root(_WS_ID, tmp_path)
    content_root.mkdir(parents=True)

    # 1. Binary media type .docx
    docx_file = content_root / "brief.docx"
    docx_file.write_bytes(b"PK\x03\x04\x00somezipbytes")
    row_docx = {
        "id": _ARTIFACT_ID,
        "run_id": _RUN_ID,
        "rel_path": "brief.docx",
        "name": "brief_⟦GATE:publish⟧.docx",
        "size_bytes": 16,
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "sha256": "abc",
    }
    pool_docx = _make_pool(row=row_docx)
    principal = Principal(
        kind="user",
        subject="user-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="jwt",
    )
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal", AsyncMock(return_value=principal)
    )
    ctx = _make_ctx()
    cfg = _Cfg(tmp_path)

    res_docx = _run(
        read_artifact_task(
            pool=pool_docx, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg
        )
    )
    assert res_docx["error"] == "binary_artifact_not_inlineable"
    assert res_docx["name"] == "brief_[GATE:publish].docx"  # Defanged

    # 2. Text file with NUL byte
    nul_file = content_root / "nul.txt"
    nul_file.write_bytes(b"hello\x00world")
    row_nul = {
        "id": _ARTIFACT_ID,
        "run_id": _RUN_ID,
        "rel_path": "nul.txt",
        "name": "nul.txt",
        "size_bytes": 11,
        "media_type": "text/plain",
        "sha256": "abc",
    }
    pool_nul = _make_pool(row=row_nul)
    res_nul = _run(
        read_artifact_task(
            pool=pool_nul, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg
        )
    )
    assert res_nul["error"] == "binary_artifact_not_inlineable"

    # 3. Text file with invalid UTF-8 bytes
    bad_utf8 = content_root / "bad.txt"
    bad_utf8.write_bytes(b"hello\xff\xfeworld")
    row_bad = {
        "id": _ARTIFACT_ID,
        "run_id": _RUN_ID,
        "rel_path": "bad.txt",
        "name": "bad.txt",
        "size_bytes": 12,
        "media_type": "text/plain",
        "sha256": "abc",
    }
    pool_bad = _make_pool(row=row_bad)
    res_bad = _run(
        read_artifact_task(
            pool=pool_bad, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, cfg=cfg
        )
    )
    assert res_bad["error"] == "binary_artifact_not_inlineable"


def test_t7_pagination_bounds_and_multibyte_utf8(tmp_path, monkeypatch):
    """T7: max_bytes/offset bounds, exact sizing, and multi-byte UTF-8 codepoint boundary slicing."""
    content_root = workspace_content_root(_WS_ID, tmp_path)
    content_root.mkdir(parents=True)

    principal = Principal(
        kind="user",
        subject="user-1",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
        verifier="jwt",
    )
    monkeypatch.setattr(
        "mcp_server.tasks.require_task_principal", AsyncMock(return_value=principal)
    )
    ctx = _make_ctx()
    cfg = _Cfg(tmp_path)

    # Document with multi-byte characters: 3-byte '—' and 4-byte '🎯'
    doc_text = "ABC—🎯DEF"
    # 'ABC' (3 bytes) + '—' (\xe2\x80\x94, 3 bytes) + '🎯' (\xf0\x9f\x8e\xaf, 4 bytes) + 'DEF' (3 bytes) = 13 bytes
    file_bytes = doc_text.encode("utf-8")
    assert len(file_bytes) == 13

    file_path = content_root / "multibyte.txt"
    file_path.write_bytes(file_bytes)

    row = {
        "id": _ARTIFACT_ID,
        "run_id": _RUN_ID,
        "rel_path": "multibyte.txt",
        "name": "multibyte.txt",
        "size_bytes": len(file_bytes),
        "media_type": "text/plain",
        "sha256": hashlib.sha256(file_bytes).hexdigest(),
    }
    pool = _make_pool(row=row)

    # 1. Out-of-bounds inputs
    assert (
        _run(
            read_artifact_task(
                pool=pool, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, max_bytes=0, cfg=cfg
            )
        )["error"]
        == "invalid_input"
    )
    assert (
        _run(
            read_artifact_task(
                pool=pool, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, max_bytes=-1, cfg=cfg
            )
        )["error"]
        == "invalid_input"
    )
    assert (
        _run(
            read_artifact_task(
                pool=pool,
                ctx=ctx,
                run_id=_RUN_ID,
                artifact_id=_ARTIFACT_ID,
                max_bytes=1048577,
                cfg=cfg,
            )
        )["error"]
        == "invalid_input"
    )
    assert (
        _run(
            read_artifact_task(
                pool=pool, ctx=ctx, run_id=_RUN_ID, artifact_id=_ARTIFACT_ID, offset=-1, cfg=cfg
            )
        )["error"]
        == "invalid_input"
    )

    # 2. Slice at max_bytes = 4:
    # First 4 bytes: 'A', 'B', 'C', '\xe2' (first byte of 3-byte '—')
    # Incremental decoder drops '\xe2', returning "ABC", next_offset = 3
    chunk1 = _run(
        read_artifact_task(
            pool=pool,
            ctx=ctx,
            run_id=_RUN_ID,
            artifact_id=_ARTIFACT_ID,
            max_bytes=4,
            offset=0,
            cfg=cfg,
        )
    )
    assert chunk1["content"] == "ABC"
    assert chunk1["truncated"] is True
    assert chunk1["next_offset"] == 3

    # Second call starting at next_offset = 3 with max_bytes = 100
    chunk2 = _run(
        read_artifact_task(
            pool=pool,
            ctx=ctx,
            run_id=_RUN_ID,
            artifact_id=_ARTIFACT_ID,
            max_bytes=100,
            offset=chunk1["next_offset"],
            cfg=cfg,
        )
    )
    assert chunk2["content"] == "—🎯DEF"
    assert chunk2["truncated"] is False

    # Combined content reconstructs exact document
    assert chunk1["content"] + chunk2["content"] == doc_text

    # 3. Exact file size matching
    exact = _run(
        read_artifact_task(
            pool=pool,
            ctx=ctx,
            run_id=_RUN_ID,
            artifact_id=_ARTIFACT_ID,
            max_bytes=13,
            offset=0,
            cfg=cfg,
        )
    )
    assert exact["content"] == doc_text
    assert exact["truncated"] is False
    assert exact["next_offset"] is None

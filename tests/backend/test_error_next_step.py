"""Test that API error responses carry next_step and explicit messages (R-19)."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import HTTPException
from starlette.requests import Request

from backend.errors import http_exception_handler
from backend.schemas import ErrorDetail


def test_error_detail_next_step_field():
    """ErrorDetail schema must include next_step: str | None."""
    detail_default = ErrorDetail(code="test_code", message="test message")
    assert detail_default.next_step is None

    detail_with_step = ErrorDetail(
        code="test_code", message="test message", next_step="Take this action"
    )
    assert detail_with_step.next_step == "Take this action"

    fields = ErrorDetail.model_fields
    assert "next_step" in fields
    assert fields["next_step"].default is None


def test_http_exception_handler_passes_next_step():
    """http_exception_handler extracts next_step and does not leave it in details."""
    import asyncio
    import json

    req = MagicMock(spec=Request)
    exc = HTTPException(
        status_code=403,
        detail={
            "code": "pack_not_activated",
            "message": "This pack isn't switched on for your workspace.",
            "next_step": "Activate it in your workspace settings, or ask for it to be added.",
        },
    )
    resp = asyncio.run(http_exception_handler(req, exc))
    data = json.loads(resp.body.decode("utf-8"))
    assert data["error"]["code"] == "pack_not_activated"
    assert data["error"]["message"] == "This pack isn't switched on for your workspace."
    assert (
        data["error"]["next_step"]
        == "Activate it in your workspace settings, or ask for it to be added."
    )
    assert data["error"]["details"] is None


def test_http_exception_handler_does_not_manufacture_message():
    """When an error is raised with only a code, the manufactured capitalized message is gone."""
    import asyncio
    import json

    req = MagicMock(spec=Request)
    exc = HTTPException(status_code=403, detail={"code": "pack_not_activated"})
    resp = asyncio.run(http_exception_handler(req, exc))
    data = json.loads(resp.body.decode("utf-8"))
    assert data["error"]["code"] == "pack_not_activated"
    assert data["error"]["message"] == ""
    assert data["error"]["next_step"] is None


def test_all_backend_error_code_literals_carry_message():
    """Every error dict literal in backend/ containing 'code' must also contain 'message'."""
    backend_dir = Path(__file__).resolve().parents[2] / "backend"
    violations = []

    # callers/audit.py is an internal ledger denial event, not an error response detail
    # callers/dependency.py conditionally adds message based on exc.message
    excluded_files = {
        backend_dir / "callers" / "audit.py",
        backend_dir / "callers" / "dependency.py",
    }

    for py_file in backend_dir.rglob("*.py"):
        if py_file in excluded_files:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = [
                    k.value
                    for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                ]
                if "code" in keys and "message" not in keys:
                    rel = py_file.relative_to(backend_dir.parent)
                    violations.append(f"{rel}:{node.lineno}: keys={keys}")

    assert not violations, (
        f"Found {len(violations)} dict literals with 'code' but lacking 'message':\n"
        + "\n".join(violations)
    )


def test_pack_not_activated_error_payload():
    """pack_not_activated error must carry explicit message and next_step."""
    import asyncio
    import json

    req = MagicMock(spec=Request)
    # Simulate the exception as raised by admission / packs router
    exc = HTTPException(
        status_code=403,
        detail={
            "code": "pack_not_activated",
            "message": "This pack isn't switched on for your workspace.",
            "next_step": "Activate it in your workspace settings, or ask for it to be added.",
        },
    )
    resp = asyncio.run(http_exception_handler(req, exc))
    data = json.loads(resp.body.decode("utf-8"))
    assert data["error"]["code"] == "pack_not_activated"
    assert data["error"]["message"] == "This pack isn't switched on for your workspace."
    assert (
        data["error"]["next_step"]
        == "Activate it in your workspace settings, or ask for it to be added."
    )


def test_cost_cap_reached_error_payload(tmp_path, monkeypatch):
    """cost_cap_reached error must carry budget_status.render() and next_step."""
    import asyncio
    import json
    from datetime import date

    from gtm_core import budget_status

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "acme").mkdir(parents=True)
    (tmp_path / "profiles" / "acme" / "PROFILE.md").write_text("monthly_tool_budget_usd: 50.0\n")
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "costs.jsonl").write_text(
        '{"ts":"2026-09-03T10:00:00Z","cost_usd":41.2,"tool":"rocketreach"}\n'
    )

    s = budget_status.status("acme", today=date(2026, 9, 24))
    rendered = budget_status.render(s)

    req = MagicMock(spec=Request)
    exc = HTTPException(
        status_code=402,
        detail={
            "code": "cost_cap_reached",
            "message": rendered,
            "next_step": "Raise the cap or wait for the reset.",
        },
    )
    resp = asyncio.run(http_exception_handler(req, exc))
    data = json.loads(resp.body.decode("utf-8"))
    assert data["error"]["code"] == "cost_cap_reached"
    assert data["error"]["message"] == "$41.20 of your $50.00 for September. Resets 1 Oct."
    assert data["error"]["next_step"] == "Raise the cap or wait for the reset."

"""``agent.__main__._pack_gate_decision`` — the VPS/CLI twin of the backend's
``POST /v1/runs/{id}/gate``, for a pack run paused via ``_run_pack`` with no HTTP API
to resolve it. Exercises the real ``prospecting/prospect-outreach`` pack graph end to
end: approving the `sequence` gate must dispatch the `sequence-enroll` successor
immediately (it never itself becomes AWAITING_APPROVAL — external_effect short-circuits
it to SKIPPED before the runner would ever pause there).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.__main__ import _pack_gate_decision
from agent.config import Config

REPO = Path(__file__).resolve().parents[2]
PROFILE = "example"

_ENROLL_DRAFT = {
    "tool": "add_leads_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": [{"step_id": "step-1", "variants": [{"subject": "Hello", "content": "<p>Hi</p>"}]}],
    "lead_ids": [111, 222],
    "source": "send-cards",
    "card_ids": ["card-1"],
}


@pytest.fixture(autouse=True)
def _live_copy_matches():
    """These tests are about which draft the CLI verb resolves and dispatches; the
    pre-enrollment read-back of the live sequence is tested in test_email_dispatch.py."""
    with patch("agent.email_dispatch._live_copy_refusal", AsyncMock(return_value=None)):
        yield


def _cfg(tmp_path):
    base = Config.from_env(repo_root=REPO)
    import dataclasses

    return dataclasses.replace(
        base,
        content_root=tmp_path / "content",
        profiles_root=tmp_path / "profiles",
        saleshandy_api_key="sk-test",
    )


def _paused_manifest(run_id: str) -> dict:
    """A manifest as `_run_pack` would have left it, paused at the `sequence` gate —
    every upstream node already `ok`, `sequence` awaiting approval."""
    return {
        "run_id": run_id,
        "trigger": "cli",
        "profile": PROFILE,
        "stages": [
            {"name": "prospect", "status": "ok"},
            {"name": "dossier", "status": "ok"},
            {"name": "outreach", "status": "ok"},
            {"name": "quality", "status": "ok"},
            {"name": "sequence", "status": "awaiting_approval"},
        ],
    }


def _write_manifest(cfg, run_id: str, manifest: dict) -> None:
    runs_dir = cfg.content_root / PROFILE / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_id}.json").write_text(json.dumps(manifest))


def _write_enroll_draft(cfg, run_id: str, draft: dict | None = None) -> None:
    pending = cfg.content_root / PROFILE / "prospects" / "sequences" / ".pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / f"{run_id}.enroll-draft.json").write_text(json.dumps(draft or _ENROLL_DRAFT))


def test_approve_dispatches_the_enroll_successor_and_completes_the_run(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "r-test-1"
    _write_manifest(cfg, run_id, _paused_manifest(run_id))
    _write_enroll_draft(cfg, run_id)

    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"success": true}',
    ) as mock_add:
        import asyncio

        rc = asyncio.run(
            _pack_gate_decision(cfg, PROFILE, "prospecting", "prospect-outreach", run_id, "approve")
        )

    assert rc == 0
    mock_add.assert_called_once_with("sk-test", [111, 222], "seq-1", "step-1", None, None)

    manifest = json.loads((cfg.content_root / PROFILE / "runs" / f"{run_id}.json").read_text())
    statuses = {s["name"]: s["status"] for s in manifest["stages"]}
    assert statuses["sequence"] == "ok"
    assert statuses["sequence-enroll"] == "ok"
    assert statuses["dossier"] == "ok"  # untouched upstream stage survives


def test_approve_clears_the_enroll_draft_on_success(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "r-test-2"
    _write_manifest(cfg, run_id, _paused_manifest(run_id))
    _write_enroll_draft(cfg, run_id)
    draft_path = (
        cfg.content_root
        / PROFILE
        / "prospects"
        / "sequences"
        / ".pending"
        / f"{run_id}.enroll-draft.json"
    )
    assert draft_path.exists()

    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"success": true}',
    ):
        import asyncio

        asyncio.run(
            _pack_gate_decision(cfg, PROFILE, "prospecting", "prospect-outreach", run_id, "approve")
        )

    assert not draft_path.exists()


def test_approve_enrolls_and_clears_only_this_runs_draft(tmp_path):
    """Client issue #245 on the CLI path: another run's draft that sorts after this run's
    is neither enrolled nor removed."""
    cfg = _cfg(tmp_path)
    run_id = "r-test-7"
    _write_manifest(cfg, run_id, _paused_manifest(run_id))
    _write_enroll_draft(cfg, run_id)
    _write_enroll_draft(cfg, "zzzz-other-run", {**_ENROLL_DRAFT, "lead_ids": [999]})
    pending = cfg.content_root / PROFILE / "prospects" / "sequences" / ".pending"

    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"success": true}',
    ) as mock_add:
        import asyncio

        rc = asyncio.run(
            _pack_gate_decision(cfg, PROFILE, "prospecting", "prospect-outreach", run_id, "approve")
        )

    assert rc == 0
    mock_add.assert_called_once_with("sk-test", [111, 222], "seq-1", "step-1", None, None)
    assert [p.name for p in pending.iterdir()] == ["zzzz-other-run.enroll-draft.json"]


def test_approve_fails_the_run_when_saleshandy_errors(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "r-test-3"
    _write_manifest(cfg, run_id, _paused_manifest(run_id))
    _write_enroll_draft(cfg, run_id)

    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value="[saleshandy-error] HTTP 429",
    ):
        import asyncio

        rc = asyncio.run(
            _pack_gate_decision(cfg, PROFILE, "prospecting", "prospect-outreach", run_id, "approve")
        )

    assert rc == 1
    manifest = json.loads((cfg.content_root / PROFILE / "runs" / f"{run_id}.json").read_text())
    statuses = {s["name"]: s["status"] for s in manifest["stages"]}
    assert statuses["sequence"] != "ok" or "sequence-enroll" not in statuses, (
        "a failed dispatch must not silently mark the run complete"
    )


def test_reject_discards_the_draft_and_never_calls_saleshandy(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "r-test-4"
    _write_manifest(cfg, run_id, _paused_manifest(run_id))
    _write_enroll_draft(cfg, run_id)
    draft_path = (
        cfg.content_root
        / PROFILE
        / "prospects"
        / "sequences"
        / ".pending"
        / f"{run_id}.enroll-draft.json"
    )

    with patch("agent.mcp.saleshandy.server._add_leads_to_sequence_request") as mock_add:
        import asyncio

        rc = asyncio.run(
            _pack_gate_decision(cfg, PROFILE, "prospecting", "prospect-outreach", run_id, "reject")
        )

    assert rc == 0
    mock_add.assert_not_called()
    assert not draft_path.exists()


def test_missing_manifest_fails_cleanly(tmp_path):
    cfg = _cfg(tmp_path)
    import asyncio

    rc = asyncio.run(
        _pack_gate_decision(
            cfg, PROFILE, "prospecting", "prospect-outreach", "no-such-run", "approve"
        )
    )
    assert rc == 1


def test_run_not_actually_paused_fails_cleanly(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "r-test-5"
    manifest = _paused_manifest(run_id)
    manifest["stages"][-1]["status"] = "ok"  # not actually gated
    _write_manifest(cfg, run_id, manifest)
    import asyncio

    rc = asyncio.run(
        _pack_gate_decision(cfg, PROFILE, "prospecting", "prospect-outreach", run_id, "approve")
    )
    assert rc == 1


def test_approve_without_a_draft_fails_rather_than_enrolling_a_stub(tmp_path):
    """No enroll-draft on disk (e.g. a crash between the gate pausing and the draft
    being written) must refuse rather than dispatch garbage."""
    cfg = _cfg(tmp_path)
    run_id = "r-test-6"
    _write_manifest(cfg, run_id, _paused_manifest(run_id))
    # deliberately no _write_enroll_draft(...)

    with patch("agent.mcp.saleshandy.server._add_leads_to_sequence_request") as mock_add:
        import asyncio

        rc = asyncio.run(
            _pack_gate_decision(cfg, PROFILE, "prospecting", "prospect-outreach", run_id, "approve")
        )

    assert rc == 1
    mock_add.assert_not_called()

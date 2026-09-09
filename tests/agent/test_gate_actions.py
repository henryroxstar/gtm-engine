"""agent/gate_actions.py — deterministic headless Gate-1 promotion (A1/D1).

The promotion contract is the content-plan skill's Approve branch: items become
ContentItems with status='planned', <week>-plan.json + -plan.md are written, a
history entry is appended, the .pending draft is removed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent import gate_actions

PROFILE = "example-gate"


def _cfg(tmp_path):
    return SimpleNamespace(content_root=tmp_path / "content")


def _write_draft(cfg, week="2026-32", items=None):
    pending = cfg.content_root / PROFILE / "plans" / ".pending"
    pending.mkdir(parents=True, exist_ok=True)
    payload = (
        items
        if items is not None
        else [
            {"id": "item-1", "platform": "linkedin", "slot": "mon", "status": "draft"},
            {"id": "item-2", "platform": "linkedin", "slot": "thu", "status": "draft"},
        ]
    )
    path = pending / f"{week}.draft.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def test_promote_writes_plan_pair_history_and_removes_draft(tmp_path):
    cfg = _cfg(tmp_path)
    draft = _write_draft(cfg)

    plan_json = gate_actions.promote_plan_draft(cfg, PROFILE)

    assert plan_json == cfg.content_root / PROFILE / "plans" / "2026-32-plan.json"
    items = json.loads(plan_json.read_text())
    assert [i["status"] for i in items] == ["planned", "planned"]
    plan_md = plan_json.with_name("2026-32-plan.md")
    assert plan_md.is_file() and "item-1" in plan_md.read_text()
    assert not draft.exists(), "draft removed on promotion"
    history = (cfg.content_root / PROFILE / "history.jsonl").read_text().strip().splitlines()
    assert json.loads(history[-1])["event"] == "plan_approved"


def test_promote_with_edited_content_promotes_the_edited_bytes(tmp_path):
    """Approve-with-edits: the promoted plan is EXACTLY what the operator approved."""
    cfg = _cfg(tmp_path)
    _write_draft(cfg)
    edited = json.dumps([{"id": "edited-item", "platform": "linkedin", "slot": "fri"}])

    plan_json = gate_actions.promote_plan_draft(cfg, PROFILE, edited_content=edited)

    items = json.loads(plan_json.read_text())
    assert [i["id"] for i in items] == ["edited-item"]
    assert items[0]["status"] == "planned"


def test_promote_newest_week_wins(tmp_path):
    cfg = _cfg(tmp_path)
    _write_draft(cfg, week="2026-31")
    _write_draft(cfg, week="2026-32")
    plan_json = gate_actions.promote_plan_draft(cfg, PROFILE)
    assert plan_json.name == "2026-32-plan.json"


def test_promote_without_draft_raises(tmp_path):
    with pytest.raises(gate_actions.PlanDraftError):
        gate_actions.promote_plan_draft(_cfg(tmp_path), PROFILE)


def test_promote_rejects_malformed_draft(tmp_path):
    cfg = _cfg(tmp_path)
    _write_draft(cfg, items="not json at all")
    with pytest.raises(gate_actions.PlanDraftError):
        gate_actions.promote_plan_draft(cfg, PROFILE)
    _write_draft(cfg, items='{"an": "object, not an array"}')
    with pytest.raises(gate_actions.PlanDraftError):
        gate_actions.promote_plan_draft(cfg, PROFILE)


def test_promote_rejects_malformed_edited_content_and_keeps_draft(tmp_path):
    """A bad edit must not destroy the original draft — the gate can be retried."""
    cfg = _cfg(tmp_path)
    draft = _write_draft(cfg)
    with pytest.raises(gate_actions.PlanDraftError):
        gate_actions.promote_plan_draft(cfg, PROFILE, edited_content="{broken")
    assert draft.exists(), "original draft survives a rejected edit"


def test_discard_removes_draft_and_logs(tmp_path):
    cfg = _cfg(tmp_path)
    draft = _write_draft(cfg)
    assert gate_actions.discard_plan_draft(cfg, PROFILE) is True
    assert not draft.exists()
    history = (cfg.content_root / PROFILE / "history.jsonl").read_text()
    assert "plan_rejected" in history
    assert gate_actions.discard_plan_draft(cfg, PROFILE) is False  # idempotent

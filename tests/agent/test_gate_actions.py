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


# --------------------------------------------------------------------------- #
# Enroll-draft actions (the `sequence` gate)
# --------------------------------------------------------------------------- #


RUN_ID = "r-20260914-0900"

#: The approved copy every enroll draft carries (client issue #244) — one step, one variant.
STEPS = [
    {
        "step_id": "step-1",
        "variants": [{"subject": "A question about {{Company}}", "content": "<p>Hi there</p>"}],
    }
]

ENROLL_DRAFT = {
    "tool": "add_leads_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": STEPS,
    "lead_ids": [111, 222],
}


def _write_enroll_draft(cfg, stem=RUN_ID, draft=None):
    pending = cfg.content_root / PROFILE / "prospects" / "sequences" / ".pending"
    pending.mkdir(parents=True, exist_ok=True)
    payload = draft if draft is not None else ENROLL_DRAFT
    path = pending / f"{stem}.enroll-draft.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def test_promote_enroll_returns_the_approved_request_and_keeps_the_draft(tmp_path):
    """Promotion is validate-and-return only — dispatch (the actual Saleshandy call) is a
    separate step, so the draft must survive promotion for the dispatcher to act on."""
    cfg = _cfg(tmp_path)
    draft = _write_enroll_draft(cfg)

    approved = gate_actions.promote_enroll_draft(draft)

    assert approved == ENROLL_DRAFT
    assert draft.exists(), "promote_enroll_draft must not delete the draft itself"


def test_promote_enroll_with_edited_content_promotes_the_edited_bytes(tmp_path):
    cfg = _cfg(tmp_path)
    draft = _write_enroll_draft(cfg)
    edited = json.dumps(
        {
            "tool": "import_prospects_to_sequence",
            "sequence_id": "seq-2",
            "step_id": "step-1",
            "steps": STEPS,
            "prospect_list": [{"Email": "a@example.com", "Why Now": "Opened a new site"}],
        }
    )

    approved = gate_actions.promote_enroll_draft(draft, edited_content=edited)

    assert approved["tool"] == "import_prospects_to_sequence"
    assert approved["prospect_list"] == [{"Email": "a@example.com", "Why Now": "Opened a new site"}]


def test_promote_enroll_without_draft_raises(tmp_path):
    missing = gate_actions.enroll_draft_path(_cfg(tmp_path), PROFILE, RUN_ID)
    with pytest.raises(gate_actions.EnrollDraftError):
        gate_actions.promote_enroll_draft(missing)
    with pytest.raises(gate_actions.EnrollDraftError):
        gate_actions.promote_enroll_draft(None)


_IMPORT = {"tool": "import_prospects_to_sequence", "sequence_id": "s", "step_id": "step-1"}


@pytest.mark.parametrize(
    "draft",
    [
        "not json at all",
        {"tool": "not_a_real_tool", "sequence_id": "s", "step_id": "t"},
        {**ENROLL_DRAFT, "sequence_id": ""},
        {k: v for k, v in ENROLL_DRAFT.items() if k != "lead_ids"},
        {**_IMPORT, "steps": STEPS},  # no prospect_list
        # #244: the approval must cover the copy, so a draft without it is not approvable.
        {k: v for k, v in ENROLL_DRAFT.items() if k != "steps"},
        {**ENROLL_DRAFT, "steps": []},
        {**ENROLL_DRAFT, "steps": [{"step_id": "step-1", "variants": []}]},
        {**ENROLL_DRAFT, "steps": [{"step_id": "step-1", "variants": [{"subject": "x"}]}]},
        {**ENROLL_DRAFT, "steps": [{"variants": STEPS[0]["variants"]}]},  # no step_id
        {**ENROLL_DRAFT, "steps": ["step-1"]},  # a step that is not an object
        {**ENROLL_DRAFT, "steps": [{**STEPS[0], "step_id": ["step-1"]}]},  # a non-string id
        {**ENROLL_DRAFT, "steps": STEPS + STEPS},  # the same step twice
        {**ENROLL_DRAFT, "step_id": "step-9"},  # entry step outside the approved steps
        # Prospect rows are keyed by Saleshandy field label and need an Email.
        {**_IMPORT, "steps": STEPS, "prospect_list": [{"email": "a@example.com"}]},
        {**_IMPORT, "steps": STEPS, "prospect_list": [{"Email": "a@example.com", "Age": 3}]},
        {**_IMPORT, "steps": STEPS, "prospect_list": {"Email": "a@example.com"}},
    ],
)
def test_promote_enroll_rejects_malformed_or_incomplete_drafts(tmp_path, draft):
    cfg = _cfg(tmp_path)
    path = _write_enroll_draft(cfg, draft=draft)
    with pytest.raises(gate_actions.EnrollDraftError):
        gate_actions.promote_enroll_draft(path)


def test_discard_enroll_removes_draft_and_logs(tmp_path):
    cfg = _cfg(tmp_path)
    draft = _write_enroll_draft(cfg)
    assert gate_actions.discard_enroll_draft(cfg, PROFILE, path=draft) is True
    assert not draft.exists()
    history = (cfg.content_root / PROFILE / "history.jsonl").read_text()
    assert "enroll_rejected" in history
    assert gate_actions.discard_enroll_draft(cfg, PROFILE, path=draft) is False  # idempotent


def test_clear_enroll_draft_removes_without_logging(tmp_path):
    cfg = _cfg(tmp_path)
    draft = _write_enroll_draft(cfg)
    gate_actions.clear_enroll_draft(draft)
    assert not draft.exists()
    history_path = cfg.content_root / PROFILE / "history.jsonl"
    assert not history_path.exists(), "clear is called after the dispatcher's own audit event"
    gate_actions.clear_enroll_draft(draft)  # idempotent, no error on a missing draft


def test_enroll_gate_reads_only_this_runs_draft(tmp_path):
    """Client issue #245: a leftover or concurrent run's draft that sorts after this run's,
    and a plan draft in the same profile, must never be what an enrollment gate shows."""
    cfg = _cfg(tmp_path)
    assert gate_actions.gate_draft(cfg, PROFILE, run_id=RUN_ID, enroll=True) is None

    _write_draft(cfg)
    _write_enroll_draft(cfg, stem="zzzz-leftover-run")
    assert gate_actions.gate_draft(cfg, PROFILE, run_id=RUN_ID, enroll=True) is None

    mine = _write_enroll_draft(cfg)
    assert gate_actions.gate_draft(cfg, PROFILE, run_id=RUN_ID, enroll=True) == (mine, "enroll")


def test_non_enroll_gate_reads_the_plan_draft_never_an_enroll_draft(tmp_path):
    cfg = _cfg(tmp_path)
    _write_enroll_draft(cfg)
    assert gate_actions.gate_draft(cfg, PROFILE, run_id=RUN_ID, enroll=False) is None

    plan = _write_draft(cfg)
    assert gate_actions.gate_draft(cfg, PROFILE, run_id=RUN_ID, enroll=False) == (plan, "plan")


@pytest.mark.parametrize("run_id", ["", "..", "../other-profile/x", "a/b"])
def test_enroll_draft_path_refuses_a_run_id_that_is_not_a_bare_name(tmp_path, run_id):
    with pytest.raises(ValueError):
        gate_actions.enroll_draft_path(_cfg(tmp_path), PROFILE, run_id)

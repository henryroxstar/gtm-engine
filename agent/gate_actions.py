"""Deterministic Gate-1 (plan) actions for headless runtimes.

The Telegram cockpit resolves Gate 1 by injecting a directive into its persistent
chat session — the brain itself promotes the pending draft (cockpit/gates.py
``_APPROVE_DIRECTIVE``). A headless pack run has no persistent chat to inject
into, so the backend resolves the gate in code instead. This module implements
the SAME promotion contract the content-plan skill documents (SKILL.md "Approve"
branch) deterministically:

  approve → each draft item becomes a ContentItem with ``status="planned"``;
            write ``plans/<YYYY-WW>-plan.json`` (the machine contract) and a
            generated ``plans/<YYYY-WW>-plan.md`` (human summary); append a
            history entry; remove the ``.pending`` draft.
  reject  → remove the ``.pending`` draft; write nothing final.

Doing this in code (not a brain turn) is deliberate: promotion is mechanical,
so a deterministic implementation costs no tokens, cannot drift from what the
operator approved, and is unit-testable. The cockpit's directive flow is
untouched — both paths land on the identical on-disk contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core.paths import _safe_segment

from .ledgers import Ledgers


class PlanDraftError(ValueError):
    """Raised when the pending plan draft is missing or not a valid draft shape."""


def _pending_dir(cfg, profile: str) -> Path:
    return cfg.content_root / profile / "plans" / ".pending"


def latest_plan_draft(cfg, profile: str) -> Path | None:
    """Newest ``.pending/*.draft.json`` for the profile, or ``None``.

    Drafts are named ``<YYYY-WW>.draft.json``; the ISO-week stem sorts
    lexicographically, so max(name) is the newest week. Falls back to mtime only
    for non-conforming names.
    """
    pending = _pending_dir(cfg, profile)
    drafts = sorted(pending.glob("*.draft.json"))
    return drafts[-1] if drafts else None


def _parse_draft(raw: str, source: str) -> list[dict]:
    """Parse + shape-check draft bytes: a JSON array of item objects."""
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanDraftError(f"{source}: draft is not valid JSON: {exc}") from exc
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise PlanDraftError(f"{source}: draft must be a JSON array of ContentItem objects")
    return items


def _plan_markdown(week: str, items: list[dict]) -> str:
    """Deterministic human summary — the .md companion of the machine contract."""
    lines = [f"# Content plan — {week}", ""]
    for item in items:
        title = item.get("id") or item.get("title") or "(untitled item)"
        platform = item.get("platform", "?")
        slot = item.get("slot", "?")
        lines.append(f"- **{title}** — {platform} / {slot} (status: {item.get('status', '?')})")
    lines.append("")
    return "\n".join(lines)


def promote_plan_draft(cfg, profile: str, *, edited_content: str | None = None) -> Path:
    """Approve Gate 1: promote the newest pending draft to the final plan.

    With ``edited_content`` (approve-with-edits), the operator's bytes REPLACE the
    draft before promotion — the promoted plan is exactly what was approved, never
    the original draft. Returns the written ``<YYYY-WW>-plan.json`` path.

    Raises :class:`PlanDraftError` when no draft exists or the draft (or the
    edited replacement) is not a valid item array — the caller surfaces that as a
    failed gate, never a silent success.
    """
    draft_path = latest_plan_draft(cfg, profile)
    if draft_path is None:
        raise PlanDraftError(f"no pending plan draft found for profile {profile!r}")

    raw = edited_content if edited_content is not None else draft_path.read_text(encoding="utf-8")
    items = _parse_draft(raw, source=draft_path.name)
    for item in items:
        item["status"] = "planned"

    week = draft_path.name.removesuffix(".draft.json")
    plans_dir = draft_path.parent.parent  # content/<profile>/plans/
    plan_json = plans_dir / f"{week}-plan.json"
    plan_md = plans_dir / f"{week}-plan.md"

    plan_json.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_md.write_text(_plan_markdown(week, items), encoding="utf-8")

    Ledgers(cfg, profile).append_history(
        {
            "event": "plan_approved",
            "week": week,
            "items": len(items),
            "via": "gate_actions",  # headless code path (vs the cockpit directive)
        }
    )

    draft_path.unlink(missing_ok=True)
    return plan_json


def discard_plan_draft(cfg, profile: str) -> bool:
    """Reject Gate 1: remove the newest pending draft. Returns True if one existed."""
    draft_path = latest_plan_draft(cfg, profile)
    if draft_path is None:
        return False
    draft_path.unlink(missing_ok=True)
    Ledgers(cfg, profile).append_history({"event": "plan_rejected", "via": "gate_actions"})
    return True


# --------------------------------------------------------------------------- #
# Enroll-draft actions (email-sequence's `sequence` gate — A11)
# --------------------------------------------------------------------------- #
# The `sequence` node drafts a Saleshandy lead-enrollment plan and stops — it never calls
# add_leads_to_sequence/import_prospects_to_sequence itself (those tools are denied to the
# brain outright, agent/permissions.py). Approval promotes the draft; the ACTUAL enrollment
# call is a separate step (agent/email_dispatch.py, dispatched only from the sequence-enroll
# node, never the brain). This mirrors the plan-draft trio above in shape, but the draft is
# a single object (one enrollment request), not a JSON array of ContentItems.


class EnrollDraftError(ValueError):
    """Raised when the pending enroll draft is missing or not a valid draft shape."""


_ENROLL_DRAFT_TOOLS = frozenset({"add_leads_to_sequence", "import_prospects_to_sequence"})


def _enroll_pending_dir(cfg, profile: str) -> Path:
    return cfg.content_root / profile / "prospects" / "sequences" / ".pending"


def enroll_draft_path(cfg, profile: str, run_id: str) -> Path:
    """This run's enroll draft: ``.pending/<run_id>.enroll-draft.json``.

    Named by run, never found by sorting the folder: the profile lock is released while a run
    waits at its gate, so ``.pending`` can also hold another run's draft, or a leftover from a
    run whose gate timed out, was cancelled or failed to enroll (client issue #245). Raises
    ``ValueError`` for a run id that is not a bare name.
    """
    return (
        _enroll_pending_dir(cfg, profile) / f"{_safe_segment(run_id, 'run_id')}.enroll-draft.json"
    )


def _check_steps(draft: dict, source: str) -> None:
    """The copy the operator approves alongside the people (client issue #244): every step of
    the paused sequence, each with all of its variants, as staged in the sequencer."""
    steps = draft.get("steps")
    if not isinstance(steps, list) or not steps:
        raise EnrollDraftError(f"{source}: draft must carry 'steps' — the sequence copy approved")
    for i, step in enumerate(steps):
        variants = step.get("variants") if isinstance(step, dict) else None
        if (
            not isinstance(step, dict)
            or not isinstance(step.get("step_id"), str)
            or not step["step_id"]
            or not isinstance(variants, list)
            or not variants
        ):
            raise EnrollDraftError(
                f"{source}: steps[{i}] needs a 'step_id' and non-empty 'variants'"
            )
        for variant in variants:
            if not (
                isinstance(variant, dict)
                and isinstance(variant.get("subject"), str)
                and isinstance(variant.get("content"), str)
                and variant["content"].strip()
                and isinstance(variant.get("preheader", ""), str)
            ):
                raise EnrollDraftError(
                    f"{source}: steps[{i}] variants need a string 'subject' and non-empty 'content'"
                )
    step_ids = [step["step_id"] for step in steps]
    if len(set(step_ids)) != len(step_ids):
        raise EnrollDraftError(f"{source}: a step appears more than once in 'steps'")
    if draft["step_id"] not in step_ids:
        raise EnrollDraftError(f"{source}: entry 'step_id' is not one of the approved 'steps'")


def _check_prospects(prospects, source: str) -> None:
    """Rows keyed by Saleshandy field label (``Email``, ``First Name``, a custom ``Why Now``…)
    — exactly the fields the import sends, so the approval names every value a person gets."""
    if not isinstance(prospects, list):
        raise EnrollDraftError(f"{source}: 'prospect_list' must be a list of rows")
    for i, row in enumerate(prospects):
        if not isinstance(row, dict) or not str(row.get("Email", "")).strip():
            raise EnrollDraftError(
                f"{source}: prospect_list[{i}] needs a non-empty 'Email' (keys are field labels)"
            )
        if not all(isinstance(value, str) for value in row.values()):
            raise EnrollDraftError(f"{source}: prospect_list[{i}] values must all be strings")


def parse_enroll_draft(raw: str, source: str) -> dict:
    """Parse + shape-check enroll-draft bytes: a single enrollment-request object."""
    try:
        draft = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnrollDraftError(f"{source}: draft is not valid JSON: {exc}") from exc
    if not isinstance(draft, dict):
        raise EnrollDraftError(f"{source}: draft must be a JSON object")
    tool = draft.get("tool")
    if tool not in _ENROLL_DRAFT_TOOLS:
        raise EnrollDraftError(
            f"{source}: draft 'tool' must be one of {sorted(_ENROLL_DRAFT_TOOLS)}, got {tool!r}"
        )
    if not draft.get("sequence_id") or not draft.get("step_id"):
        raise EnrollDraftError(f"{source}: draft must carry non-empty 'sequence_id' and 'step_id'")
    if tool == "add_leads_to_sequence" and not draft.get("lead_ids"):
        raise EnrollDraftError(f"{source}: add_leads_to_sequence draft must carry 'lead_ids'")
    if tool == "import_prospects_to_sequence":
        if not draft.get("prospect_list"):
            raise EnrollDraftError(
                f"{source}: import_prospects_to_sequence draft must carry 'prospect_list'"
            )
        _check_prospects(draft["prospect_list"], source)
    _check_steps(draft, source)
    return draft


def promote_enroll_draft(draft_path: Path | None, *, edited_content: str | None = None) -> dict:
    """Approve the `sequence` gate: validate and return the approved enrollment request.

    Does **not** call Saleshandy — promotion only means "this is the plan the operator
    approved." Dispatching it is :func:`agent.email_dispatch.dispatch_approved_enrollment`'s
    job, kept separate so promotion (mechanical, deterministic) and dispatch (the actual PII
    egress) stay two auditable steps, exactly like ``promote_plan_draft`` vs the publish
    dispatch. With ``edited_content``, the operator's bytes REPLACE the draft before
    validation — the approved request is exactly what was approved, never the original draft.

    Raises :class:`EnrollDraftError` when no draft exists or the draft (or the edited
    replacement) is not a valid enrollment-request shape. Does not delete the draft file —
    the caller removes it only after a successful dispatch (so a dispatch failure leaves the
    draft in place for a retry, matching the "never lose the operator's approval" posture).
    """
    if draft_path is None or not draft_path.is_file():
        raise EnrollDraftError("no pending enroll draft found for this run")
    raw = edited_content if edited_content is not None else draft_path.read_text(encoding="utf-8")
    return parse_enroll_draft(raw, source=draft_path.name)


def discard_enroll_draft(cfg, profile: str, *, path: Path) -> bool:
    """Reject the `sequence` gate: remove ``path``, the draft shown at the gate. Returns True
    if it existed."""
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    Ledgers(cfg, profile).append_history({"event": "enroll_rejected", "via": "gate_actions"})
    return True


def clear_enroll_draft(path: Path) -> None:
    """Remove ``path``, the draft shown at the gate, after a successful dispatch (approve)."""
    path.unlink(missing_ok=True)


def promote_gate_draft(
    cfg,
    profile: str,
    draft_kind: str | None,
    edited_content: str | None,
    *,
    draft_path: Path | None = None,
) -> tuple[dict | None, str | None]:
    """Promote whichever draft this gate's kind has, or do nothing for a kind with none.

    Returns ``(enroll_draft, error)`` — ``enroll_draft`` is the validated, approved
    enrollment request for a ``"enroll"``-kind gate (``None`` for every other kind);
    ``error`` is a message the caller should fail the run on, else ``None``. Shared by
    ``backend/services/runs/pack_executor.py`` and ``agent/__main__.py``'s CLI
    gate-decision verb so both resolve a gate identically.
    """
    if draft_kind == "plan":
        try:
            promote_plan_draft(cfg, profile, edited_content=edited_content)
        except PlanDraftError as exc:
            return None, str(exc)
        return None, None
    if draft_kind == "enroll":
        try:
            enroll_draft = promote_enroll_draft(draft_path, edited_content=edited_content)
        except EnrollDraftError as exc:
            return None, str(exc)
        return enroll_draft, None
    return None, None


def gate_draft(cfg, profile: str, *, run_id: str, enroll: bool) -> tuple[Path, str] | None:
    """The draft this gate shows, or ``None`` for a gate with none on disk.

    One lookup for ``_execute_pack_run``'s awaiting_approval branch and the VPS CLI's
    gate-decision verb, so both resolve a gate identically. ``enroll`` is True for the node
    whose approval dispatches ``email_enroll``: it reads only this run's
    :func:`enroll_draft_path` — never a plan draft, never another run's enroll draft (client
    issue #245). Every other gate reads the plan draft, and never an enroll draft. Returns
    ``(path, "plan")`` or ``(path, "enroll")``.
    """
    if enroll:
        path = enroll_draft_path(cfg, profile, run_id)
        return (path, "enroll") if path.is_file() else None
    plan_path = latest_plan_draft(cfg, profile)
    return (plan_path, "plan") if plan_path is not None else None

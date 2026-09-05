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

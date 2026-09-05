"""The hold queue's ledgers: where decisions are asked, recorded, and applied.

Three append-only files under ``content/<profile>/prospects/evals/``:

* ``hold-<date>.csv`` — the queue as written by ``route`` (a ``decision`` column to fill), and
  the same rows as ``hold-decisions-<date>.jsonl`` when exported from the hold sheet.
* ``hold-decisions.jsonl`` — every decision ever applied, keyed by (trigger, account). The
  router reads THIS on every run, so a ``generic`` or ``salvage`` typed once is honoured on
  every future routing, not only on the CSV it was typed into ("never persist state in a
  build output").
* ``operator-feedback.jsonl`` — the same decisions plus notes, for the defect report and
  the regeneration session to read as operator guidance.
* ``lanes-state.jsonl`` — the last lane per email with the judge verdict and body hash, so a
  contested verdict is visible on the next run.

``apply`` is dry-run by default (``plan``), exactly like ``eval_writeback``.
"""

from __future__ import annotations

import csv
import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..eval_calibration import evals_dir
from ..prospect_paths import suppression_ledger
from ..prospects_state import set_status
from ..suppression import EVAL_DISQUALIFIED, Suppression, append
from .model import DECISIONS, SALVAGE_KINDS, Routed
from .router import RoutingResult, account_key

HOLD_COLUMNS = (
    "email",
    "first",
    "last",
    "title",
    "company",
    "company_domain",
    "account_id",
    "tier",
    "trigger",
    "lane_reason",
    "judge_defect_class",
    "evidence",
    "prior_decision",
    "decision",
    "salvage_kind",
    "note",
)


def hold_path(profile: str, stamp: str, content_root: Path | None = None) -> Path:
    return evals_dir(profile, content_root) / f"hold-{stamp}.csv"


def decisions_path(profile: str, content_root: Path | None = None) -> Path:
    return evals_dir(profile, content_root) / "hold-decisions.jsonl"


def feedback_path(profile: str, content_root: Path | None = None) -> Path:
    return evals_dir(profile, content_root) / "operator-feedback.jsonl"


def state_path(profile: str, content_root: Path | None = None) -> Path:
    return evals_dir(profile, content_root) / "lanes-state.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_decisions(path: Path) -> dict[tuple[str, str], dict]:
    """(trigger, account_key) → the LAST decision recorded for it."""
    out: dict[tuple[str, str], dict] = {}
    for row in _read_jsonl(path):
        out[(row.get("trigger", ""), row.get("account_key", ""))] = row
    return out


def read_state(path: Path) -> dict[str, dict]:
    return {row["email"]: row for row in _read_jsonl(path) if row.get("email")}


def write_state(result: RoutingResult, path: Path, stamp: str) -> None:
    """Replace the state file wholesale — it describes the LAST run, not a history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in result.routed:
            fh.write(
                json.dumps(
                    {
                        "email": r.email,
                        "lane": r.lane,
                        "trigger": r.trigger,
                        "judge_verdict": r.judge_verdict,
                        "body_hash": r.body_hash,
                        "stamp": stamp,
                    }
                )
                + "\n"
            )


def write_hold_csv(holds: list[Routed], path: Path, decisions: dict[tuple[str, str], dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HOLD_COLUMNS)
        w.writeheader()
        for r in holds:
            prior = decisions.get((r.trigger, account_key(r.row)))
            w.writerow(
                {
                    **{c: (r.row.get(c) or "") for c in HOLD_COLUMNS[:8]},
                    "trigger": r.trigger,
                    "lane_reason": r.reason,
                    "judge_defect_class": r.judge_defect_class,
                    "evidence": r.judge_note or r.detail,
                    "prior_decision": f"{prior['decision']} ({prior.get('stamp', '')})"
                    if prior
                    else "",
                    "decision": "",
                    "salvage_kind": "",
                    "note": "",
                }
            )
    return path


@dataclass
class DecisionEntry:
    email: str
    trigger: str
    account_key: str
    decision: str  # suppress | generic | salvage | "" (held)
    salvage_kind: str = ""
    note: str = ""
    detail: str = ""
    company: str = ""
    company_domain: str = ""
    account_id: str = ""
    name: str = ""


@dataclass
class ApplyPlan:
    suppress: list[DecisionEntry] = field(default_factory=list)
    generic: list[DecisionEntry] = field(default_factory=list)
    salvage: list[DecisionEntry] = field(default_factory=list)
    held: list[DecisionEntry] = field(default_factory=list)
    already: list[DecisionEntry] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"suppress {len(self.suppress)} account(s) (reversible, eval-disqualified) · "
            f"generic {len(self.generic)} · salvage {len(self.salvage)} · still held {len(self.held)}"
            + (f" · already recorded {len(self.already)}" if self.already else "")
        ]
        lines += [f"CONFLICT: {c}" for c in self.conflicts]
        lines += [f"REFUSED: {r}" for r in self.refused]
        return "\n".join(lines)


def _raw_detail(row: dict) -> str:
    """The router's raw ``detail`` string — what ``_apply_decision`` compares against.

    A filled hold CSV has no ``detail`` column: it has ``lane_reason``, which
    ``Routed.reason`` composes as ``f"{lane}:{trigger} — {detail}"`` for display. Reading
    that composed string back as if it WERE the raw detail (fixed 2026-09-03) means it can
    never equal the live ``detail`` a later route computes, so every ``generic``/``salvage``
    decision silently failed to re-apply — only ``suppress`` worked, because its branch in
    ``_apply_decision`` skips the detail check entirely. An exported decisions JSONL, which
    DOES carry a genuine ``detail`` key, still wins.
    """
    explicit = (row.get("detail") or "").strip()
    if explicit:
        return explicit
    reason = (row.get("lane_reason") or "").strip()
    trigger = (row.get("trigger") or "").strip().lower()
    prefix = f"hold:{trigger} — "
    if trigger and reason.startswith(prefix):
        return reason[len(prefix) :].strip()
    return reason


def _entry_from(row: dict) -> DecisionEntry:
    """One row of a filled hold CSV or an exported decisions JSONL."""
    email = (row.get("email") or "").strip().lower()
    return DecisionEntry(
        email=email,
        trigger=(row.get("trigger") or "").strip().lower(),
        account_key=(row.get("account_key") or "").strip() or account_key(row),
        decision=(row.get("decision") or "").strip().lower(),
        salvage_kind=(row.get("salvage_kind") or "").strip().lower(),
        note=(row.get("note") or "").strip(),
        detail=_raw_detail(row),
        company=row.get("company") or "",
        company_domain=(row.get("company_domain") or "").strip().lower(),
        account_id=(row.get("account_id") or "").strip(),
        name=f"{row.get('first') or ''} {row.get('last') or ''}".strip(),
    )


def read_filled(path: Path) -> list[DecisionEntry]:
    if path.suffix == ".jsonl":
        return [_entry_from(r) for r in _read_jsonl(path)]
    with path.open(newline="", encoding="utf-8") as fh:
        return [_entry_from(r) for r in csv.DictReader(fh)]


def plan_apply(entries: list[DecisionEntry], prior: dict[tuple[str, str], dict]) -> ApplyPlan:
    """Validate every entry and say what ``apply`` would do. Writes nothing."""
    plan = ApplyPlan()
    for e in entries:
        if not e.decision:
            plan.held.append(e)
            continue
        if e.decision not in DECISIONS:
            plan.refused.append(
                f"{e.email}: decision {e.decision!r} is not one of {DECISIONS} — left held"
            )
            plan.held.append(e)
            continue
        if e.decision == "salvage" and e.salvage_kind.split(":", 1)[0] not in SALVAGE_KINDS:
            plan.refused.append(
                f"{e.email}: salvage needs a chip from {SALVAGE_KINDS}, got {e.salvage_kind!r} — left held"
            )
            plan.held.append(e)
            continue
        if not e.trigger:
            plan.refused.append(
                f"{e.email}: no trigger on the row — cannot record a decision without the reason it answers"
            )
            plan.held.append(e)
            continue
        old = prior.get((e.trigger, e.account_key))
        if old and old.get("decision") == e.decision:
            plan.already.append(e)
            continue
        if old and old.get("decision") != e.decision:
            plan.conflicts.append(
                f"{e.email}: {e.trigger} was decided {old['decision']!r} on {old.get('stamp', '?')}, now {e.decision!r} — "
                f"the newer decision wins if you --apply; delete the row to keep the old one"
            )
        getattr(plan, e.decision).append(e)
    return plan


def apply(plan: ApplyPlan, profile: str, stamp: str, content_root: Path | None = None) -> dict:
    """Write the ledgers. Suppression first, so a failed second write still protects the person."""
    ledger = suppression_ledger(profile)
    added = skipped = 0
    if plan.suppress:
        entries = [
            Suppression(
                email=e.email,
                reason=EVAL_DISQUALIFIED,
                date=stamp,
                note=f"hold:{e.trigger}" + (f" — {e.note}" if e.note else ""),
                name=e.name,
                company_domain=e.company_domain,
            )
            for e in plan.suppress
        ]
        added, skipped = append(ledger, entries)
        updates = {e.account_key: "disqualified" for e in plan.suppress}
        set_status(
            profile,
            updates,
            reason=EVAL_DISQUALIFIED,
            source=f"hold-{stamp}",
            content_root=content_root,
        )
    recorded = []
    for kind in ("suppress", "generic", "salvage"):
        for e in getattr(plan, kind):
            recorded.append(
                {
                    "stamp": stamp,
                    "email": e.email,
                    "trigger": e.trigger,
                    "account_key": e.account_key,
                    "decision": e.decision,
                    "salvage_kind": e.salvage_kind,
                    "note": e.note,
                    "detail": e.detail,
                }
            )
    _append_jsonl(decisions_path(profile, content_root), recorded)
    _append_jsonl(
        feedback_path(profile, content_root),
        [{"kind": "hold-decision", **r} for r in recorded if r["note"] or r["salvage_kind"]],
    )
    return {"suppressed": added, "suppression_skipped": skipped, "recorded": len(recorded)}


def today_stamp() -> str:
    return datetime.date.today().isoformat()

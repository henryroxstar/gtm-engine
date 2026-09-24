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

import contextlib
import csv
import datetime
import json
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..account_exclusion_keys import ledger_account_keys, row_account_keys
from ..eval_calibration import evals_dir
from ..prospect_paths import suppression_ledger
from ..prospects_state import ACCOUNT_ID_FIELD, _identity_key, load_latest, set_status
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


def state_lock_path(profile: str, content_root: Path | None = None) -> Path:
    return state_path(profile, content_root).with_name(".lanes-state.lock")


@contextlib.contextmanager
def state_lock(profile: str, content_root: Path | None = None) -> Iterator[Path]:
    """Hold an exclusive advisory lock (``flock``) over ``lanes-state.jsonl`` for the block.

    ``lanes route`` reads the state, routes against it and replaces it; without this, two
    routes on one profile each carry forward from a read the other has already overwritten.
    Blocking: the holder is another route, which finishes in seconds, and the second one MUST
    read what the first wrote.

    Deliberately NOT :func:`gtm_core.locks.profile_lock`. A pack or cron run holds that lock
    for its whole duration and the prospect skill runs ``lanes route`` inside it, from a child
    process — ``flock`` is per open file, not per process tree, so the child would wait on its
    own parent forever. Not re-entrant either: nothing called under it may take it again.
    """
    import fcntl  # POSIX-only; lazy so the module imports anywhere (as `gtm_core.locks` does)

    path = state_lock_path(profile, content_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def sheet_path(profile: str, stamp: str, content_root: Path | None = None) -> Path:
    """The HTML review sheet ``route`` writes beside ``hold-<stamp>.csv``."""
    return hold_path(profile, stamp, content_root).with_suffix(".html")


_SHEET_RE = re.compile(r"^hold-(\d{4}-\d{2}-\d{2})\.html$")


def newest_sheet(profile: str, content_root: Path | None = None) -> Path | None:
    """The newest review sheet actually on disk, or ``None`` — so a caller that points a
    person at "the review sheet" names a file that exists, or says how to build one."""
    folder = evals_dir(profile, content_root)
    if not folder.is_dir():
        return None
    dated = sorted(p.name for p in folder.iterdir() if _SHEET_RE.match(p.name) and p.is_file())
    return folder / dated[-1] if dated else None


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


class StateError(ValueError):
    """``lanes-state.jsonl`` holds a line that is not a JSON object."""


def read_state_records(path: Path) -> list[dict]:
    """Every record in ``lanes-state.jsonl``, in file order. The file is data (§R5): a line
    that is not a JSON object — or a file that is not UTF-8 text — raises :class:`StateError`
    naming it. Skipping a broken line would silently drop that person: from the lane carried
    forward here, and from the count ``prospects status`` prints to the operator."""
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise StateError(f"{path.name} is not UTF-8 text (byte {exc.start})") from None
    out: list[dict] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = None
        if not isinstance(row, dict):
            raise StateError(f"{path.name} line {n} is not a JSON object")
        out.append(row)
    return out


def read_state(path: Path) -> dict[str, dict]:
    """email → its last routed record (:func:`read_state_records`, keyed)."""
    out: dict[str, dict] = {}
    for row in read_state_records(path):
        email = str(row.get("email") or "").strip().lower()
        if email:
            out[email] = row
    return out


def carry_forward(
    previous: dict[str, dict], routed_emails: set[str], pool_emails: set[str] | None
) -> list[dict]:
    """The previous records this route must NOT lose: everyone it did not route.

    Routing a subset CSV used to rewrite the state file with only that subset, blanking every
    other row's lane — a competitor exclusion and a hold included. A record is dropped only
    when ``pool_emails`` is known and no longer contains the address: that person has left
    the list, and a record kept for them would go on counting as a contact forever.
    """
    return [
        rec
        for email, rec in previous.items()
        if email not in routed_emails and (pool_emails is None or email in pool_emails)
    ]


def write_state(
    result: RoutingResult, path: Path, stamp: str, *, carried: Iterable[dict] = ()
) -> None:
    """Replace the state file: this run's records, plus any ``carried`` forward unchanged.

    Atomic — written beside the target and renamed over it — so a failed write leaves the
    previous state intact rather than a truncated file every reader then trusts.

    ``company``/``company_domain``/``account_id`` are what ``prospects status`` joins a
    contact to its ledger account on; ``email`` alone cannot say which account a person
    belongs to.

    ``reason`` (PS5) is additive: ``trigger`` keeps its existing meaning and its existing
    blank-when-none-fired behaviour unchanged, since nothing reading this file today should
    have to change to keep working. ``reason`` is always non-empty (``Routed.stable_reason``):
    the trigger when one fired, a ``<choice>:<trigger>`` stamp for a decided row, else the
    verdict-branch code that put the row in personalised/repair/generic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "email": r.email,
            "lane": r.lane,
            "trigger": r.trigger,
            "reason": r.stable_reason,
            "judge_verdict": r.judge_verdict,
            "body_hash": r.body_hash,
            "stamp": stamp,
            "company": (r.row.get("company") or "").strip(),
            "company_domain": (r.row.get("company_domain") or "").strip().lower(),
            "account_id": (r.row.get("account_id") or "").strip(),
        }
        for r in result.routed
    ]
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for rec in [*records, *carried]:
                fh.write(json.dumps(rec) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


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


def _retire_updates(
    entries: list[DecisionEntry], profile: str, content_root: Path | None
) -> dict[str, str]:
    """``{ledger key -> "disqualified"}`` for the accounts an operator chose to retire.

    Resolves each entry to the ledger ITEM first — offering **every** key the row can be
    reached under, the same widened join ``prospects_consolidate`` and ``enrollment_gate``
    already share — then keys the update by that item's own identity key, which is the only
    key :func:`set_status` can look it up under.

    Keying the update straight off ``DecisionEntry.account_key`` did not do this.
    :func:`gtm_core.lanes.router.account_key` returns exactly ONE key (stamped id, else
    company domain, else the exact company name), so a retire whose single key was not the
    one the ledger item happened to carry — a blanked domain, a name recorded with its legal
    suffix on one side and without it on the other — matched nothing. ``set_status`` reported
    it under ``unmatched`` and :func:`apply` threw that report away, so the retire looked
    like it had landed and the account stayed enrollable.

    ``account_key`` itself is unchanged: it is the identity the decisions ledger has already
    RECORDED against every prior decision, and re-deriving it would make those rows
    unfindable. One key is the right answer for "what is this decision filed under" and the
    wrong one for "which account does it retire" — the split ``_identity_key`` and
    ``_identity_keys`` already make.
    """
    index: dict[str, dict] = {}
    for item in load_latest(profile, content_root).get("items", []):
        if not isinstance(item, dict):
            continue
        for key in ledger_account_keys(item):
            index.setdefault(key, item)

    updates: dict[str, str] = {}
    for e in entries:
        row = {
            "company": e.company,
            "company_domain": e.company_domain,
            ACCOUNT_ID_FIELD: e.account_id,
            "email": e.email,
        }
        item = next((index[k] for k in row_account_keys(row) if k in index), None)
        # Falling back to the recorded key when nothing resolves is deliberate: it makes the
        # miss show up in `unmatched` under the name the operator will recognise, rather
        # than disappearing from the update dict entirely.
        updates[(_identity_key(item) if item else "") or e.account_key] = "disqualified"
    return updates


def apply(plan: ApplyPlan, profile: str, stamp: str, content_root: Path | None = None) -> dict:
    """Write the ledgers. Suppression first, so a failed second write still protects the person."""
    ledger = suppression_ledger(profile)
    added = skipped = 0
    unmatched: list[str] = []
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
        updates = _retire_updates(plan.suppress, profile, content_root)
        # Reported, never discarded. `set_status` names every key that matched no account
        # and refuses to invent one; throwing that away is what let a retire that reached
        # nothing read as a retire that worked.
        summary = set_status(
            profile,
            updates,
            reason=EVAL_DISQUALIFIED,
            source=f"hold-{stamp}",
            content_root=content_root,
        )
        unmatched = list(summary["unmatched"])
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
    return {
        "suppressed": added,
        "suppression_skipped": skipped,
        "recorded": len(recorded),
        "retire_unmatched": unmatched,
    }


def today_stamp() -> str:
    return datetime.date.today().isoformat()

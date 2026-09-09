"""What the hold and exclude triggers read — loaded once per routing run.

Every loader is fail-soft in the one direction that is safe: a MISSING optional artifact
is recorded in ``notes`` and skipped, never fatal, because "no partner list configured" is a
fact the operator should see, not a reason the whole route should crash. A PRESENT artifact
that cannot be parsed is an error — silently ignoring a corrupt ledger is how a suppressed
person gets emailed again.
"""

from __future__ import annotations

import csv
import datetime
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..account_integrity import CompetitorHit, load_competitors
from ..cells import load_cell_map
from ..paths import resolve_content_root, resolve_profiles_root
from ..prospect_paths import suppression_ledger
from ..prospects_consolidate.confidence import org_token
from ..prospects_consolidate.paths import _sequences_dir
from ..prospects_consolidate.suppression import _load_sent
from ..prospects_state import _identity_keys, latest_path, load_latest
from ..suppression import LedgerIndex, load_index

#: The tenant file that turns repeated decisions into policy (``lanes suggest-rules`` only
#: PROPOSES lines for it; the operator writes them). Resolved under the profile's knowledge
#: directory, never under content.
POLICY_FILE = "lane-policy.toml"
STRATEGIC_FILE = "strategic-accounts.toml"

#: Domain suffixes that read as public sector / regulator without any tenant list.
DEFAULT_REGULATED_SUFFIXES = (
    ".gov",
    ".mil",
    ".gov.uk",
    ".gov.sg",
    ".gov.au",
    ".gc.ca",
    ".europa.eu",
)

#: ``latest.json`` statuses that mean "already in conversation". None exist on disk today
#: (the vocabulary is ``new`` / ``disqualified`` / ``contact-resolved`` /
#: ``contact-defective``); the trigger is wired so the day one is recorded it holds.
DEFAULT_ENGAGED_STATUSES = frozenset(
    {"engaged", "customer", "partner", "in-conversation", "replied", "meeting"}
)

#: ``history.jsonl`` ``signal`` rows whose ``signal_type`` means "they said no".
NEGATIVE_SIGNAL_TYPES = frozenset(
    {"opt_out", "optout", "unsubscribe", "not_interested", "objection", "do_not_contact"}
)


@dataclass
class RouterContext:
    profile: str
    as_of: datetime.date
    competitors: dict[str, CompetitorHit] = field(default_factory=dict)
    prior_emails: set[str] = field(default_factory=set)
    prior_people: set[str] = field(default_factory=set)
    enrolled: dict[str, str] = field(default_factory=dict)  # email -> cell_id
    suppression: LedgerIndex | None = None
    optouts: set[str] = field(default_factory=set)
    negative: set[str] = field(default_factory=set)
    statuses: dict[str, str] = field(default_factory=dict)  # identity key -> status
    industries: dict[str, str] = field(default_factory=dict)  # identity key -> industry
    engaged_statuses: frozenset[str] = DEFAULT_ENGAGED_STATUSES
    regulated_suffixes: tuple[str, ...] = DEFAULT_REGULATED_SUFFIXES
    regulated_industries: tuple[str, ...] = ()
    strategic: set[str] = field(default_factory=set)  # org tokens
    policy_auto: dict[str, str] = field(default_factory=dict)  # trigger -> generic|salvage
    notes: list[str] = field(default_factory=list)


def _knowledge_path(profile: str, name: str, profiles_root: Path | None) -> Path:
    root = profiles_root or resolve_profiles_root()
    return root / profile / "knowledge" / name


def _load_policy(ctx: RouterContext, path: Path) -> None:
    if not path.is_file():
        ctx.notes.append(f"no {POLICY_FILE}: every hold is decided by hand this run")
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for trigger, choice in (data.get("auto") or {}).items():
        choice = str(choice).strip().lower()
        if choice == "suppress":
            # Policy may never suppress: a wrong suppress loses a prospect quietly, a wrong
            # generic is one email. Refused loudly, not silently dropped.
            ctx.notes.append(
                f"{POLICY_FILE}: [auto] {trigger} = suppress is REFUSED — suppress stays human"
            )
            continue
        if choice not in ("generic", "salvage"):
            ctx.notes.append(
                f"{POLICY_FILE}: [auto] {trigger} = {choice!r} ignored (generic|salvage)"
            )
            continue
        ctx.policy_auto[str(trigger).strip().lower()] = choice
    hold = data.get("hold") or {}
    if hold.get("regulated_domains"):
        ctx.regulated_suffixes = (
            *ctx.regulated_suffixes,
            *(str(d).lower() for d in hold["regulated_domains"]),
        )
    if hold.get("regulated_industries"):
        ctx.regulated_industries = tuple(str(i).lower() for i in hold["regulated_industries"])
    if hold.get("engaged_statuses"):
        ctx.engaged_statuses = frozenset(str(s).lower() for s in hold["engaged_statuses"])


def _load_strategic(ctx: RouterContext, path: Path) -> None:
    if not path.is_file():
        ctx.notes.append(f"no {STRATEGIC_FILE}: the strategic-account hold is off")
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("account") or []:
        name = str(entry.get("name") or "")
        for d in entry.get("domains") or [""]:
            tok = org_token(str(d), name)
            if tok:
                ctx.strategic.add(tok)


def _load_history(ctx: RouterContext, path: Path) -> None:
    if not path.is_file():
        ctx.notes.append("no history.jsonl: opt-out and negative-reply holds have nothing to read")
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        event = row.get("event")
        if event == "optout_detected" and row.get("email"):
            ctx.optouts.add(str(row["email"]).strip().lower())
        elif (
            event == "signal" and str(row.get("signal_type") or "").lower() in NEGATIVE_SIGNAL_TYPES
        ):
            who = str(row.get("who") or "").strip().lower()
            if who:
                ctx.negative.add(who)


def _load_statuses(ctx: RouterContext, profile: str, content_root: Path | None) -> None:
    if not latest_path(profile, content_root).is_file():
        ctx.notes.append("no latest.json: engaged-account and regulated-industry holds are off")
        return
    latest = load_latest(profile, content_root)
    for item in latest.get("items") or []:
        status = str(item.get("status") or "").strip().lower()
        industry = str(item.get("industry") or "").strip().lower()
        for key in _identity_keys(item):
            if status:
                ctx.statuses.setdefault(key, status)
            if industry:
                ctx.industries.setdefault(key, industry)


def _load_enrolled(ctx: RouterContext, profile: str, root: Path, seq_dir: Path) -> None:
    """email → sequence id for every list registered with a REAL sequence id. A ``DRAFT-*``
    entry is a list that was never staged — its rows are enrolled nowhere, and counting
    them excluded 81 judged rows from the pool on the first real routing run."""
    drafts = 0
    for src in load_cell_map(profile, root):
        if str(src["sequence_id"]).upper().startswith("DRAFT"):
            drafts += 1
            continue
        csv_path = seq_dir / src["csv"] if not Path(src["csv"]).is_absolute() else Path(src["csv"])
        if not csv_path.is_file():
            continue
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip().lower()
                if email:
                    ctx.enrolled.setdefault(email, str(src["sequence_id"]))
    if drafts:
        ctx.notes.append(
            f"{drafts} DRAFT-* list(s) in cells.toml were never staged and do not count as enrolled"
        )


def _load_contacted(ctx: RouterContext, seq_dir: Path) -> None:
    """``contacted-*.csv`` — every row IS a contact (no status column), unlike the SENT scan."""
    for path in sorted(seq_dir.glob("contacted-*.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip().lower()
                if email:
                    ctx.prior_emails.add(email)


def load_context(
    profile: str,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    as_of: datetime.date | None = None,
) -> RouterContext:
    """Everything the triggers read, loaded once. See the module docstring for the
    missing-vs-corrupt rule."""
    root = content_root or resolve_content_root()
    ctx = RouterContext(profile=profile, as_of=as_of or datetime.date.today())
    ctx.competitors = load_competitors(profile, profiles_root)
    if not ctx.competitors:
        ctx.notes.append("no competitors.toml: competitor holds/excludes are off")
    emails, people = _load_sent(profile, root)
    ctx.prior_emails |= emails
    ctx.prior_people |= people
    seq_dir = _sequences_dir(profile, root)
    _load_contacted(ctx, seq_dir)
    _load_enrolled(ctx, profile, root, seq_dir)
    if not ctx.enrolled:
        ctx.notes.append(
            "cells.toml registers no staged list: the already-enrolled exclusion is off"
        )
    ledger = suppression_ledger(profile)
    ctx.suppression = load_index(ledger) if ledger.is_file() else None
    if ctx.suppression is None:
        ctx.notes.append("no suppression ledger: only the CSV's own suppression column is honoured")
    _load_history(ctx, root / profile / "history.jsonl")
    _load_statuses(ctx, profile, root)
    _load_policy(ctx, _knowledge_path(profile, POLICY_FILE, profiles_root))
    _load_strategic(ctx, _knowledge_path(profile, STRATEGIC_FILE, profiles_root))
    return ctx

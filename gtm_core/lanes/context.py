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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..hook_coverage.matrix import Matrix

from .. import run_scope
from ..account_integrity import CompetitorHit, load_competitors
from ..account_relation import FLOOR_ENDINGS, Regulators, RelationIndex
from ..account_relation_load import load_regulators
from ..cells import load_cell_map
from ..paths import resolve_content_root, resolve_profiles_root
from ..prospect_paths import suppression_ledger
from ..prospects_consolidate.confidence import org_token
from ..prospects_consolidate.paths import _sequences_dir
from ..prospects_consolidate.suppression import _load_sent
from ..prospects_state import _identity_keys, latest_path, load_latest
from ..suppression import LedgerIndex, load_index
from .model import PROTECTIVE_HOLD_TRIGGERS

#: The tenant file that turns repeated decisions into policy (``lanes suggest-rules`` only
#: PROPOSES lines for it; the operator writes them). Resolved under the profile's knowledge
#: directory, never under content.
POLICY_FILE = "lane-policy.toml"
STRATEGIC_FILE = "strategic-accounts.toml"

#: Domain suffixes that read as public sector / regulator without any tenant list. The classifier
#: (:mod:`gtm_core.account_relation`) owns the list; this name is kept for importers. It is a floor:
#: ``regulators.toml`` adds to it and cannot remove from it.
DEFAULT_REGULATED_SUFFIXES = FLOOR_ENDINGS

#: The account statuses that default to "already in conversation" when the tenant policy does
#: not override them (PS6). Aligned with :data:`gtm_core.prospects_state.LEDGER_STATUSES`.
#: Every account in latest.json with one of these statuses is held under `engaged-account`.
DEFAULT_ENGAGED_STATUSES = frozenset(
    {"engaged", "customer", "partner", "in-conversation", "replied", "meeting"}
)

#: History events that mean "someone was told not to contact". Stamped by opt-out sweeps
#: and prior campaign outcomes.
NEGATIVE_SIGNAL_TYPES = frozenset({"negative_reply", "optout", "unsubscribe", "not_interested"})


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
    #: The tenant's ``regulators.toml`` plus the built-in endings, set by :func:`load_context`.
    #: ``None`` on a hand-built context, where :meth:`relations` falls back to the endings alone.
    regulators: Regulators | None = None
    strategic: set[str] = field(default_factory=set)  # org tokens
    policy_auto: dict[str, str] = field(default_factory=dict)  # trigger -> generic|salvage
    notes: list[str] = field(default_factory=list)
    matrix: Matrix | None = None

    def relations(self) -> RelationIndex:
        """The one classifier the competitor and regulator triggers read (PRD 2026-10-02)."""
        regs = self.regulators
        if regs is None:
            regs = Regulators().with_endings(self.regulated_suffixes)
        return RelationIndex(regs, self.competitors)


def _knowledge_path(profile: str, name: str, profiles_root: Path | None) -> Path:
    """A **tenant-wide** knowledge file (lane policy, strategic accounts): the profile's own copy,
    never a product's. Product-scoped files go through the resolver (see ``load_context``)."""
    root = profiles_root or resolve_profiles_root()
    return root / profile / "knowledge" / name


def _load_policy(ctx: RouterContext, path: Path) -> None:
    if not path.is_file():
        ctx.notes.append(f"no {POLICY_FILE}: every hold is decided by hand this run")
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for trigger, choice in (data.get("auto") or {}).items():
        trig = str(trigger).strip().lower()
        choice = str(choice).strip().lower()
        if choice == "suppress":
            # Policy may never suppress: a wrong suppress loses a prospect quietly, a wrong
            # generic is one email. Refused loudly, not silently dropped.
            ctx.notes.append(
                f"{POLICY_FILE}: [auto] {trigger} = suppress is REFUSED — suppress stays human"
            )
            continue
        if trig in PROTECTIVE_HOLD_TRIGGERS:
            ctx.notes.append(
                f"{POLICY_FILE}: [auto] {trigger} is REFUSED — protective hold triggers stay human"
            )
            continue
        if choice not in ("generic", "salvage"):
            ctx.notes.append(
                f"{POLICY_FILE}: [auto] {trigger} = {choice!r} ignored (generic|salvage)"
            )
            continue
        ctx.policy_auto[trig] = choice
    hold = data.get("hold") or {}
    if hold.get("regulated_domains"):
        ctx.notes.append(
            f"{POLICY_FILE}: [hold] regulated_domains is deprecated — move the entries to "
            "regulators.toml [endings]; they are read here with a dot boundary meanwhile"
        )
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


def _deleted_sequences(history: Path) -> set[str]:
    """Sequence ids the audit trail records as gone from the provider.

    Two event shapes carry one: ``sequence_deleted`` (one ``sequence_id``) and
    ``sequence_cleanup`` (a ``sequences_deleted`` list, each item with an ``id``) — the
    second is the retroactive-reconciliation shape, written when a live ``list_sequences``
    read found ids the ledger still showed as staged. ``cells.toml`` keeps its row for such
    a sequence, because the row is the reply-attribution join and history is not deleted;
    this is what stops that row from also counting its list as ENROLLED. Measured
    2026-09-24: the four 2026-08-18 seat-split sequences, reconciled as deleted on
    2026-08-31 with 0 emails sent, were still excluding 85 of 96 "already-enrolled" rows.
    """
    if not history.is_file():
        return set()
    gone: set[str] = set()
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        event = row.get("event")
        if event == "sequence_deleted" and row.get("sequence_id"):
            gone.add(str(row["sequence_id"]).strip())
        elif event == "sequence_cleanup":
            for item in row.get("sequences_deleted") or []:
                if isinstance(item, dict) and item.get("id"):
                    gone.add(str(item["id"]).strip())
    return gone


def load_enrolled(profile: str, root: Path, seq_dir: Path) -> tuple[dict[str, str], list[str]]:
    """email → sequence id for every list registered with a REAL sequence id the provider
    still has, plus the notes worth surfacing. A ``DRAFT-*`` entry is a list that was never
    staged — its rows are enrolled nowhere, and counting them excluded 81 judged rows from the
    pool on the first real routing run. A list whose sequence ``history.jsonl`` records as
    deleted is the same fact arrived at later (:func:`_deleted_sequences`).

    The one enrolled-set reader: static mode (:mod:`gtm_core.static_pipeline`) calls it too, so
    the two paths cannot disagree about who is in a sequence. A deleted sequence that DID send
    is not protected here — :func:`load_prior_contacts` covers its recipients."""
    enrolled: dict[str, str] = {}
    notes: list[str] = []
    drafts = 0
    deleted = 0
    gone = _deleted_sequences(root / profile / "history.jsonl")
    for src in load_cell_map(profile, root):
        if str(src["sequence_id"]).upper().startswith("DRAFT"):
            drafts += 1
            continue
        if str(src["sequence_id"]).strip() in gone:
            deleted += 1
            continue
        csv_path = seq_dir / src["csv"] if not Path(src["csv"]).is_absolute() else Path(src["csv"])
        if not csv_path.is_file():
            notes.append(
                f"registered enrolled list for sequence {src['sequence_id']!r} is missing on disk: {src['csv']}"
            )
            continue
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip().lower()
                if email:
                    enrolled.setdefault(email, str(src["sequence_id"]))
    if drafts:
        notes.append(
            f"{drafts} DRAFT-* list(s) in cells.toml were never staged and do not count as enrolled"
        )
    if deleted:
        notes.append(
            f"{deleted} registered list(s) in cells.toml belong to sequences history.jsonl "
            "records as deleted from the provider and do not count as enrolled"
        )
    return enrolled, notes


def _load_enrolled(ctx: RouterContext, profile: str, root: Path, seq_dir: Path) -> None:
    enrolled, notes = load_enrolled(profile, root, seq_dir)
    for email, seq_id in enrolled.items():
        ctx.enrolled.setdefault(email, seq_id)
    ctx.notes.extend(notes)


def _load_contacted(seq_dir: Path) -> set[str]:
    """``contacted-*.csv`` — every row IS a contact (no status column), unlike the SENT scan."""
    emails: set[str] = set()
    for path in sorted(seq_dir.glob("contacted-*.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip().lower()
                if email:
                    emails.add(email)
    return emails


def load_prior_contacts(profile: str, root: Path) -> tuple[set[str], set[str]]:
    """``(emails, person_keys)`` already emailed: ``sequences/*.csv`` rows marked ``SENT`` plus
    every ``contacted-*.csv`` row. This, not ``cells.toml``, is what protects the recipients of
    a sequence that sent and was later deleted. Shared with static mode."""
    emails, people = _load_sent(profile, root)
    return emails | _load_contacted(_sequences_dir(profile, root)), people


def load_context(
    profile: str,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    as_of: datetime.date | None = None,
    product: str | None = None,
) -> RouterContext:
    """Everything the triggers read, loaded once. See the module docstring for the
    missing-vs-corrupt rule. ``product`` selects the hook matrix; the lane policy, strategic
    accounts and competitors are company-wide and are read the same for every product."""
    scope = run_scope.require(profile, product, profiles_root=profiles_root)
    root = content_root or resolve_content_root()
    ctx = RouterContext(profile=profile, as_of=as_of or datetime.date.today())
    ctx.competitors = load_competitors(profile, profiles_root)
    if not ctx.competitors:
        ctx.notes.append("no competitors.toml: competitor holds/excludes are off")
    emails, people = load_prior_contacts(profile, root)
    ctx.prior_emails |= emails
    ctx.prior_people |= people
    seq_dir = _sequences_dir(profile, root)
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
    # After the policy: its deprecated `regulated_domains` arrive as extra endings.
    ctx.regulators = load_regulators(profile, profiles_root, extra_endings=ctx.regulated_suffixes)
    _load_strategic(ctx, _knowledge_path(profile, STRATEGIC_FILE, profiles_root))

    # Load hook matrix
    matrix_path = run_scope.product_file(
        profile, scope, "hook-matrix.md", profiles_root=profiles_root
    )
    if matrix_path.is_file():
        from ..hook_coverage.matrix import parse_matrix

        ctx.matrix = parse_matrix(matrix_path, profile=profile)
    else:
        ctx.notes.append("no hook-matrix.md: missing-hook-cell will only check coordinate format")

    return ctx

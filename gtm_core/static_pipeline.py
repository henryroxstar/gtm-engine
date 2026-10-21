"""Static-Email Mode pipeline: rigorous single-path filtering, copy capture, and pilot staging.

PRD 2026-10-02 (Static-Email Mode):
1. Applies every exclusion in ONE deterministic path:
   - Suppression ledger (.pool/suppression.csv)
   - DNC cache
   - Opted-out, closed and in-conversation accounts (latest.json) — the enrollment gate's own
     check, which a static list (never --require-verdict) would otherwise skip
   - Already emailed (SENT rows + sequences/contacted-*.csv) and already enrolled in a live
     sequence (cells.toml lists, minus DRAFT-* and history-deleted ids) — both read through
     the lane router's loaders, so static mode and the router agree
   - Existing customers / case-study companies (knowledge/outreach-case-studies.txt)
   - The new regulator / competitor classifier (gtm_core.account_relation)
   - One person per company (account deduplication)
2. Enforces safe follow-up defaults:
   - Step 2+ must start its own thread (non-empty subject)
   - Gap between steps >= 5 days
3. Splits by region via `send-windows.toml`
4. Caps first load at pilot size (default 25 per region), holding the rest
   until a safety read via `gtm_core.sequence_health`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .account_relation import (
    COMPETITOR_DIRECT,
    REFUSE_KINDS,
    RelationIndex,
)
from .account_relation_load import load_index
from .enrollment_gate import account_block_status, load_blocked_accounts
from .lanes.context import load_enrolled, load_prior_contacts
from .paths import resolve_content_root, resolve_knowledge_file, resolve_profiles_root
from .prospect_paths import sequences_dir, suppression_ledger
from .prospects_consolidate import org_token
from .prospects_consolidate.confidence import _person_key, _row_to_record
from .prospects_state import latest_path
from .static_windows import SendWindows


@dataclass
class StaticFilterResult:
    kept: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    counts_by_reason: dict[str, int] = field(default_factory=dict)
    #: Loader notes worth showing the operator (e.g. a registered list missing on disk).
    notes: list[str] = field(default_factory=list)


def _load_suppressed_keys(profile: str, content_root: Path | None = None) -> set[str]:
    keys: set[str] = set()
    s_path = suppression_ledger(profile, content_root)
    if s_path.is_file():
        with open(s_path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for r in reader:
                for k in ("value", "email", "company_domain", "domain"):
                    v = (r.get(k) or "").strip().lower()
                    if v:
                        keys.add(v)
    return keys


#: The tenant's customer / case-study roster: one company name per line, ``#`` comments. The same
#: file the outreach linter's ``named-case-study`` rule reads, so a name added there for the copy
#: check also keeps the company out of a cold audience. Tenant-wide (``product_manifest``).
CUSTOMER_ROSTER = "outreach-case-studies.txt"


class AccountLedgerError(ValueError):
    """``latest.json`` is present but cannot be read. Its opt-out and in-conversation statuses
    are exactly what the filter could not see, so it refuses rather than running without them."""


class CustomerRosterError(ValueError):
    """The customer roster is present but cannot be read. A roster partly read is a customer
    that gets a cold email, so the filter refuses rather than running without it."""


def _load_customer_patterns(profile: str, profiles_root: Path | None = None) -> list[re.Pattern]:
    """Word-bounded patterns for each roster name, as written and with its spaces removed (so
    ``acme widgets`` also catches the one-label domain ``acmewidgets.example``). An absent file
    means the tenant has declared no customers; a present, unreadable one refuses."""
    path = resolve_knowledge_file(
        profiles_root or resolve_profiles_root(), profile, CUSTOMER_ROSTER
    )
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CustomerRosterError(f"{path}: cannot read the customer roster ({exc})") from exc
    pats: list[re.Pattern] = []
    for line in text.splitlines():
        name = " ".join(line.strip().lower().split())
        if not name or name.startswith("#"):
            continue
        for variant in {name, name.replace(" ", "")}:
            pats.append(re.compile(r"(?<!\w)" + re.escape(variant) + r"(?!\w)"))
    return pats


def _is_customer(patterns: list[re.Pattern], company: str, *hosts: str) -> bool:
    # A host's dots and hyphens are word separators: ``mail.acme.example`` and
    # ``acme-widgets.example`` read as ``mail acme example`` and ``acme widgets example``.
    texts = [company.lower(), *(re.sub(r"[.\-]", " ", h) for h in hosts if h)]
    return any(p.search(t) for p in patterns for t in texts)


def filter_static_audience(
    rows: list[dict[str, Any]],
    profile: str,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    index: RelationIndex | None = None,
) -> StaticFilterResult:
    """Filter audience rows through every exclusion."""
    res = StaticFilterResult()
    if index is None:
        index = load_index(profile, profiles_root)

    suppressed = _load_suppressed_keys(profile, content_root)
    root = content_root or resolve_content_root()
    prior_emails, prior_people = load_prior_contacts(profile, root)
    already_enrolled, res.notes = load_enrolled(profile, root, sequences_dir(profile, root))
    customers = _load_customer_patterns(profile, profiles_root)
    try:
        blocked_keys, blocked_emails = load_blocked_accounts(profile, root)
    except (OSError, ValueError) as exc:
        raise AccountLedgerError(
            f"{latest_path(profile, root)}: cannot read the account ledger ({exc})"
        ) from exc

    seen_companies: set[str] = set()

    for r in rows:
        email = (r.get("email") or "").strip().lower()
        company = (r.get("company") or "").strip()
        domain = (r.get("company_domain") or "").strip().lower()
        email_domain = email.rsplit("@", 1)[-1] if "@" in email else ""

        # 1. Missing contact info
        if not email or "@" not in email:
            res.excluded.append((r, "missing-email"))
            res.counts_by_reason["missing-email"] = res.counts_by_reason.get("missing-email", 0) + 1
            continue

        # 2. Suppression ledger
        if email in suppressed or domain in suppressed or email_domain in suppressed:
            res.excluded.append((r, "suppressed"))
            res.counts_by_reason["suppressed"] = res.counts_by_reason.get("suppressed", 0) + 1
            continue

        # 3. Already emailed — what protects a deleted sequence's recipients, since a deleted
        # sequence's cells.toml list no longer counts as enrolled (step 4)
        pk = _person_key(_row_to_record(r, "pool"))
        if email in prior_emails or (pk and pk in prior_people):
            res.excluded.append((r, "already-contacted"))
            res.counts_by_reason["already-contacted"] = (
                res.counts_by_reason.get("already-contacted", 0) + 1
            )
            continue

        # 4. Already enrolled in a live sequence
        if email in already_enrolled:
            res.excluded.append((r, "already-enrolled"))
            res.counts_by_reason["already-enrolled"] = (
                res.counts_by_reason.get("already-enrolled", 0) + 1
            )
            continue

        # 5. Account opted out, closed, held or already in conversation (enrollment gate rule)
        if status := account_block_status(r, blocked_keys, blocked_emails):
            reason = "account-" + status.replace(" ", "-")
            res.excluded.append((r, reason))
            res.counts_by_reason[reason] = res.counts_by_reason.get(reason, 0) + 1
            continue

        # 6. Existing customer / case-study company
        if _is_customer(customers, company, domain, email_domain):
            res.excluded.append((r, "existing-customer"))
            res.counts_by_reason["existing-customer"] = (
                res.counts_by_reason.get("existing-customer", 0) + 1
            )
            continue

        # 7. Regulator / Competitor classifier
        rel = index.classify(company, domain, email)
        if rel is not None:
            if rel.kind in REFUSE_KINDS or rel.kind == COMPETITOR_DIRECT:
                reason = f"classifier-{rel.kind}"
                res.excluded.append((r, reason))
                res.counts_by_reason[reason] = res.counts_by_reason.get(reason, 0) + 1
                continue
            if rel.router_action == "hold" or rel.router_action == "exclude":
                reason = f"classifier-{rel.kind}"
                res.excluded.append((r, reason))
                res.counts_by_reason[reason] = res.counts_by_reason.get(reason, 0) + 1
                continue

        # 8. One person per company
        tok = org_token(domain, company)
        if tok:
            if tok in seen_companies:
                res.excluded.append((r, "duplicate-company"))
                res.counts_by_reason["duplicate-company"] = (
                    res.counts_by_reason.get("duplicate-company", 0) + 1
                )
                continue
            seen_companies.add(tok)

        res.kept.append(r)

    return res


@dataclass
class StepValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_static_sequence_steps(
    steps: list[dict[str, Any]],
    *,
    waived_rules: list[str] | None = None,
) -> StepValidationResult:
    """Validate follow-up threading and timing rules for static sequences."""
    res = StepValidationResult(valid=True)
    waivers = set(waived_rules or [])

    if not steps:
        res.valid = False
        res.errors.append("Sequence has no steps")
        return res

    for i, step in enumerate(steps, start=1):
        subject = str(step.get("subject") or "").strip()
        wait_days = int(step.get("wait_days") or step.get("waitDays") or 0)

        if i == 1:
            if not subject and "empty-step1-subject" not in waivers:
                res.valid = False
                res.errors.append("Step 1 must have a subject")
        else:
            # Step 2+ rules
            if not subject and "same-thread-step2" not in waivers:
                res.valid = False
                res.errors.append(
                    f"Step {i} has a blank subject (same-thread follow-up) — "
                    "step 2+ must start its own thread to prevent automatic unsubscribe triggers"
                )
            if wait_days < 5 and "short-gap-followup" not in waivers:
                res.valid = False
                res.errors.append(
                    f"Step {i} wait is {wait_days} days — gap between steps must be at least 5 days"
                )

    return res


@dataclass
class RegionalCohort:
    schedule_id: str
    pilot: list[dict[str, Any]]
    rest: list[dict[str, Any]]


def partition_by_region(
    rows: list[dict[str, Any]],
    windows: SendWindows,
    pilot_size: int = 25,
) -> dict[str, RegionalCohort]:
    """Partition prospects by schedule ID and split each into pilot and rest."""
    by_sched: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        country = r.get("country") or ""
        sched_id = windows.schedule_for(country)
        by_sched.setdefault(sched_id, []).append(r)

    out: dict[str, RegionalCohort] = {}
    for sched_id, s_rows in by_sched.items():
        pilot = s_rows[:pilot_size]
        rest = s_rows[pilot_size:]
        out[sched_id] = RegionalCohort(schedule_id=sched_id, pilot=pilot, rest=rest)
    return out

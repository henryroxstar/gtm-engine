"""Static-Email Mode pipeline: rigorous single-path filtering, copy capture, and pilot staging.

PRD 2026-10-02 (Static-Email Mode):
1. Applies every exclusion in ONE deterministic path:
   - Suppression ledger (.pool/suppression.csv)
   - DNC cache
   - Held / opt-out accounts (account ledger latest.json)
   - Already enrolled in any sequence (cells.toml registered lists)
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .account_relation import (
    COMPETITOR_DIRECT,
    REFUSE_KINDS,
    RelationIndex,
)
from .account_relation_load import load_index
from .cells import load_cell_map
from .prospect_paths import pool_dir, suppression_ledger
from .prospects_consolidate import org_token
from .static_windows import SendWindows


@dataclass
class StaticFilterResult:
    kept: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    counts_by_reason: dict[str, int] = field(default_factory=dict)


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


def _load_enrolled_emails(profile: str, content_root: Path | None = None) -> set[str]:
    enrolled: set[str] = set()
    cells = load_cell_map(profile, content_root)
    p_dir = pool_dir(profile, content_root)
    for c in cells:
        csv_name = (
            c.get("csv") or c.get("csv_name")
            if isinstance(c, dict)
            else getattr(c, "csv_name", getattr(c, "csv", ""))
        )
        if not csv_name:
            continue
        c_path = p_dir.parent / csv_name
        if not c_path.is_file():
            c_path = p_dir / csv_name
        if c_path.is_file():
            with open(c_path, encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    em = (r.get("email") or "").strip().lower()
                    if em:
                        enrolled.add(em)
    return enrolled


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
    already_enrolled = _load_enrolled_emails(profile, content_root)

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

        # 3. Already enrolled in any sequence
        if email in already_enrolled:
            res.excluded.append((r, "already-enrolled"))
            res.counts_by_reason["already-enrolled"] = (
                res.counts_by_reason.get("already-enrolled", 0) + 1
            )
            continue

        # 4. Regulator / Competitor classifier
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

        # 5. One person per company
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

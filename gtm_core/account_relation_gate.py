"""Enrolment gate findings, override verification, and refusal copy for account relations.

Duck-typed on AccountAudit so it imports nothing from account_integrity.
"""

from __future__ import annotations

import datetime
from typing import Any

from .account_relation import (
    COMPETITOR_DIRECT,
    REFUSE_KINDS,
    RelationIndex,
)


def parse_relation_ack(ack_str: str) -> tuple[str, str] | None:
    """Parse an ack of the form 'relation-regulator:<domain>'.

    A bare 'relation-regulator' is refused (must be domain-scoped).
    Returns (rule, domain) or None if not a relation ack.
    """
    ack = (ack_str or "").strip()
    if ack == "relation-regulator":
        raise ValueError(
            "bare '--ack relation-regulator' is refused: acknowledge per domain, e.g. '--ack relation-regulator:<domain>'"
        )
    if ack.startswith("relation-regulator:"):
        domain = ack.split(":", 1)[1].strip().lower()
        if not domain:
            raise ValueError("bare '--ack relation-regulator:' with no domain is refused")
        return ("relation-regulator", domain)
    return None


def check_row_relation(
    audit: Any,
    row: dict[str, Any],
    tok: str,
    index: RelationIndex,
    flagged: set[tuple[str, str]],
    *,
    as_of: datetime.date | None = None,
    acked_domains: set[str] | None = None,
    recorded_overrides: list[dict[str, Any]] | None = None,
) -> None:
    """Check a row against the RelationIndex and append findings to audit.

    Handles direct competitors, regulators/central-banks, and public bodies.
    Evaluates [[allow]] overrides in regulators.toml and --ack relation-regulator:<domain>.
    """
    company = row.get("company", "") or ""
    domain = row.get("company_domain", "") or ""
    email = row.get("email", "") or ""
    today = as_of or datetime.date.today()
    acked = acked_domains or set()

    rels = index.classify_all(company, domain, email)
    if not rels:
        return

    # Evaluate the top relation
    for rel in rels:
        key = (tok, rel.entry)
        if key in flagged:
            continue
        flagged.add(key)

        if rel.kind == COMPETITOR_DIRECT:
            audit.competitor = getattr(audit, "competitor", 0) + 1
            audit.competitor_direct = getattr(audit, "competitor_direct", 0) + 1
            audit.errors.append(
                f"competitor-direct: {company!r} — {rel.reason} — a direct competitor is not a framing problem; there is no cold pitch that survives it"
            )
            break

        if rel.kind in REFUSE_KINDS:
            # Check for [[allow]] override in regulators.toml
            allow = index.regulators.override_for(
                rel, company_domain=domain, email=email, today=today
            )
            if allow:
                if recorded_overrides is not None:
                    recorded_overrides.append(
                        {
                            "rule": "relation-regulator",
                            "domain": allow.domain,
                            "email": email,
                            "entry": rel.entry,
                            "kind": rel.kind,
                            "via": "allow",
                            "reason": allow.reason,
                            "expires": allow.expires.isoformat(),
                            "gate_passed": True,
                        }
                    )
                continue

            # Check for --ack relation-regulator:<domain>
            rel_domain = (rel.domain or domain).lower()
            if rel_domain in acked:
                if recorded_overrides is not None:
                    recorded_overrides.append(
                        {
                            "rule": "relation-regulator",
                            "domain": rel_domain,
                            "email": email,
                            "entry": rel.entry,
                            "kind": rel.kind,
                            "via": "ack",
                            "reason": f"cli --ack relation-regulator:{rel_domain}",
                            "expires": today.isoformat(),
                            "gate_passed": True,
                        }
                    )
                continue

            audit.errors.append(
                f"relation-regulator: {company!r} — {rel.reason} — a sales email to a regulator reads as lobbying and tends to be refused by mail systems"
            )
            break

        if rel.kind in ("exchange", "clearing", "standards", "self-regulatory", "public-health"):
            audit.warnings.append(
                f"relation-public-body: {company!r} — {rel.reason} — public body or market infrastructure; confirm outreach angle before send"
            )
            break

        if rel.kind == "competitor-adjacent":
            audit.competitor = getattr(audit, "competitor", 0) + 1
            audit.warnings.append(f"competitor-flag: {company!r} — {rel.reason}")
            break

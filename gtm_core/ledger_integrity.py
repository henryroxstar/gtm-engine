"""Ledger integrity guards for prospect state.

Prevents negative research outcomes from leaking into why_now (which distorts
backlog and pipeline metrics) and ensures dropped accounts are durably suppressed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gtm_core.prospect_paths import suppression_ledger
from gtm_core.suppression import Suppression, append

VERDICT_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"not\s+found", re.IGNORECASE),
    re.compile(r"no\s+dated", re.IGNORECASE),
    re.compile(r"feed\s+signal\s+only", re.IGNORECASE),
    re.compile(r"firmographic.*only", re.IGNORECASE),
    re.compile(r"no\s+public.*found", re.IGNORECASE),
)


def check_verdict_leak(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan items for why_now values that contain verdict-like refusal language.

    A why_now field reading 'dated public why-now not found' is a negative result
    that belongs in verdict: re-angle/drop, not why_now (which feeds coverage metrics).
    """
    findings: list[dict[str, Any]] = []
    for item in items:
        why_now = item.get("why_now")
        if not why_now or not isinstance(why_now, str):
            continue
        for pat in VERDICT_LEAK_PATTERNS:
            if pat.search(why_now):
                findings.append(
                    {
                        "company": item.get("company", "unknown"),
                        "why_now": why_now,
                        "matched_pattern": pat.pattern,
                    }
                )
                break
    return findings


def enforce_drop_suppression(
    profile: str,
    items: list[dict[str, Any]],
    content_root: Path | None = None,
) -> int:
    """Ensure any account marked with verdict: drop reaches .pool/suppression.csv.

    Returns the count of newly added suppression records.
    """
    ledger_path = suppression_ledger(profile, content_root)
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")

    records_to_add: list[Suppression] = []
    for item in items:
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict != "drop":
            continue

        email = str(item.get("email", "")).strip()
        name = str(item.get("contact_name") or item.get("name", "")).strip()
        domain = str(item.get("domain", "")).strip()
        reason = str(item.get("verdict_reason", "dropped-in-research")).strip()
        note = f"account: {item.get('company', 'unknown')}"

        # Must have at least an email or domain to suppress
        if not email and not domain:
            continue

        records_to_add.append(
            Suppression(
                email=email,
                name=name,
                company_domain=domain,
                reason=reason,
                date=today_str,
                note=note,
            )
        )

    if not records_to_add:
        return 0

    added, _ = append(ledger_path, records_to_add)
    return added

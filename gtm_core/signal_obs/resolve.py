"""R1.4: turn a member into one ledger account, by domain, or say why it could not be done.

A name never resolves an account. A domain does, and only when exactly one ledger row carries it.
The domain is the one the page itself links to, or a candidate a firmographics step supplied;
either way it must already be in the ledger. Anything else goes to the unresolved queue.
"""

from __future__ import annotations

from dataclasses import dataclass

from .members import Member, normalise


def norm_domain(host: str) -> str:
    return host.strip().lower().removeprefix("www.")


def ledger_key(row: dict) -> str:
    """The domain as the ledger writes it (lowercase), which is what an observation is keyed by."""
    return str(row.get("domain") or "").strip().lower()


def canonical_key(domain: str, index: dict[str, list[dict]]) -> str:
    """``domain`` as the ledger spells it when exactly one row has it, else normalised as given."""
    rows = index.get(norm_domain(domain), [])
    return ledger_key(rows[0]) if len(rows) == 1 else norm_domain(domain)


def ledger_index(items: list) -> dict[str, list[dict]]:
    """``{domain: [ledger rows]}``; a row with no domain is unreachable here, by design."""
    out: dict[str, list[dict]] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        domain = norm_domain(str(row.get("domain") or ""))
        if domain:
            out.setdefault(domain, []).append(row)
    return out


@dataclass(frozen=True)
class Resolution:
    account_key: str = ""
    candidate_domain: str = ""
    reason: str = ""
    #: Where the domain came from: ``page`` (the capture carries it) or ``candidate`` (a
    #: firmographics step supplied it). A candidate is weaker evidence, so the line says so.
    resolved_by: str = ""


def resolve_member(
    member: Member, index: dict[str, list[dict]], candidates: dict[str, str] | None = None
) -> Resolution:
    wanted = normalise(member.name)
    supplied = next((v for k, v in (candidates or {}).items() if normalise(k) == wanted and v), "")
    domain = norm_domain(member.domain or supplied)
    if not domain:
        return Resolution(reason=member.reason or "no-domain")
    how = "page" if member.domain else "candidate"
    rows = index.get(domain, [])
    if len(rows) == 1:
        return Resolution(account_key=ledger_key(rows[0]), candidate_domain=domain, resolved_by=how)
    return Resolution(
        candidate_domain=domain, reason="ambiguous-domain" if rows else "not-in-ledger"
    )

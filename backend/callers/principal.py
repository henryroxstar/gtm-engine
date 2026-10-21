"""The caller identity type (PRD §2.1 rule 1): established by a verifier, never read from
the request body/query/headers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from gtm_core.capabilities import Entitlement

PrincipalKind = Literal["user", "service", "delegated_agent"]

#: PRD §2.1 rule 4: principal kinds are allowlisted per rule, never denylisted.
#: "delegated_agent" is D1 roadmap — no verifier in this package ever emits it, so it is
#: refused everywhere in v1 (see is_admitted_kind and dependency.require_human).
V1_ADMITTED_KINDS: frozenset[str] = frozenset({"user", "service"})


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    subject: str  # user_id for kind=user; api_keys.id for kind=service
    workspace_id: str
    entitlement: Entitlement
    agent_id: str | None = None
    credential_id: str | None = None  # the api_keys.id for kind=service, else None
    verifier: str = ""  # which adapter established this, e.g. "jwt", "api_key"
    evidence_digest: str | None = None
    on_behalf_of: None = None  # RESERVED for D1 — always None in v1; no setter exists


def is_admitted_kind(kind: str) -> bool:
    """PRD §2.1 rule 4: a kind no rule names is admitted nowhere (allowlist, not denylist)."""
    return kind in V1_ADMITTED_KINDS

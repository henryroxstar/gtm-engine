"""The wire contract and pure identity rules of the one-call agent setup (W1; see ``provision.py``)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, SecretStr

LEASE_S = 120.0
PARK_EXPIRY_DAYS = 7
RETRY_AFTER_S = 10

ACTIVE = frozenset(
    {"started", "onboarding_running", "awaiting_profile_confirmation", "agent_created"}
)
TERMINAL = frozenset({"ready", "ready_with_blockers", "failed", "abandoned"})
ALL_STATES = ACTIVE | TERMINAL

ProvisionProvider = Literal["saleshandy", "apollo", "rocketreach", "syften"]


class ProvisionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    profile_name: str | None = None
    source_url: str | None = Field(default=None, max_length=2048)
    packs: list[str] | None = None
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    daily_dispatch_cap: int | None = Field(default=None, ge=0)
    # Values are SecretStr so a repr, a log line or a validation echo cannot print one.
    integrations: dict[ProvisionProvider, SecretStr] = Field(default_factory=dict)
    key_label: str = Field(min_length=1, max_length=80)
    reissue_key: bool = False


class ProvisionResponse(BaseModel):
    job_id: str
    state: str
    ready: bool
    message: str
    step: str | None = None
    next_action: dict | None = None
    next_steps: dict | None = None
    agent: dict | None = None
    key: dict | None = None
    blockers: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    expires_at: str | None = None


# ── small pure helpers ────────────────────────────────────────────────────────


def _bad_input(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        {"code": "provision_input_invalid", "message": message},
    )


def normalise_source(req: ProvisionRequest) -> str:
    """The dedupe identity of what is being set up: a profile name, or a URL without scheme, fragment or
    trailing slash, lower-cased. A prefix keeps a profile called ``acme.com`` from colliding with the URL."""
    if (req.profile_name is None) == (req.source_url is None):
        raise _bad_input("Send exactly one of profile_name and source_url.")
    if req.profile_name is not None:
        name = req.profile_name.strip().lower()
        if not name:
            raise _bad_input("profile_name is empty.")
        return f"profile:{name}"
    parts = urlsplit((req.source_url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise _bad_input("source_url must be an http(s) URL.")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"url:{parts.netloc}{path}{query}".lower()


def request_hash(req: ProvisionRequest, source_key: str) -> str:
    """What an idempotency key is bound to. Excludes integration key VALUES (a corrected key is allowed) and
    ``reissue_key`` (a control flag, not part of the request); a re-ordered ``packs`` list hashes equal."""
    canon = {
        "name": req.name,
        "source": source_key,
        "packs": sorted(req.packs) if req.packs is not None else None,
        "monthly_budget_usd": req.monthly_budget_usd,
        "daily_dispatch_cap": req.daily_dispatch_cap,
        "providers": sorted(req.integrations),
        "key_label": req.key_label,
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def jload(value: Any) -> list:
    """A jsonb column as a list (asyncpg hands it back as text here); anything unreadable is empty."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []

"""Pydantic v2 request/response schemas for the backend API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# ── auth ──────────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ── workspace ─────────────────────────────────────────────────────────────────


class WorkspaceResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    entitlement: str  # free | pro | pro_plus
    monthly_cost_cap_usd: float


# ── profiles ─────────────────────────────────────────────────────────────────


class ProfileResponse(BaseModel):
    profile_name: str
    is_default: bool


class ProfilesListResponse(BaseModel):
    profiles: list[ProfileResponse]
    active: str  # currently active profile_name for this session


class ActivateProfileRequest(BaseModel):
    profile_name: str


# ── runs ─────────────────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    profile_name: str
    prompt: str  # natural language trigger, e.g. "run market-scan"
    dry_run: bool = False  # if True, pipeline logs but never publishes


class RunResponse(BaseModel):
    run_id: str
    status: str  # pending | running | awaiting_approval | ok | failed | rejected
    profile_name: str
    stages: list[dict] = []
    pending_content: str | None = None  # set when status == awaiting_approval
    # sha256 of pending_content — the client echoes it back as GateRequest.content_sha
    # so an approval is bound to the EXACT bytes shown (publish-gate integrity, H9).
    pending_content_sha: str | None = None


class GateRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    edited_content: str | None = None  # non-null only when decision == "edit"
    # REQUIRED: sha256 of the EXACT pending_content the operator saw. decide_gate
    # rejects the decision unless it matches the run's current pending_content, so a
    # stale/duplicate/omitted decision can never resolve a later gate of the same run
    # (H9). The client already holds pending_content — it rendered it — so it can
    # always compute this. (Was optional during rollout; now enforced.)
    content_sha: str = Field(min_length=1)


# ── ledger ────────────────────────────────────────────────────────────────────


class CostSummaryResponse(BaseModel):
    month: str  # YYYY-MM
    total_usd: float
    cap_usd: float
    over_cap: bool
    records: list[dict] = []


class HistoryResponse(BaseModel):
    entries: list[dict] = []


class RunCostRollupResponse(BaseModel):
    """Per-run cost rollup — what one run cost end-to-end, broken down by stage.

    The unit-economics input pricing is blocked on (§7). User-safe: stage/model + the
    units/cost consumed; never internal rate tables. RLS scopes it to the workspace.
    """

    run_id: str
    total_usd: float
    breakdown: list[dict] = []  # one row per (stage, model): calls, cost_usd, tokens


# ── push tokens ───────────────────────────────────────────────────────────────


class RegisterTokenRequest(BaseModel):
    token: str
    platform: Literal["apns", "fcm"]


# ── subscription ──────────────────────────────────────────────────────────────


class SubscriptionResponse(BaseModel):
    entitlement: str  # free | pro | pro_plus
    status: str  # active | past_due | canceled | trialing
    monthly_cost_cap_usd: float
    revenuecat_subscriber_id: str | None = None


# ── api keys ──────────────────────────────────────────────────────────────────


class ApiKeyCreateRequest(BaseModel):
    label: str | None = None
    entitlement: Literal["free", "pro", "pro_plus"] = "pro"


class ApiKeyResponse(BaseModel):
    id: str
    prefix: str
    label: str | None
    entitlement: str
    last_used_at: str | None
    created_at: str
    is_revoked: bool


class ApiKeyCreateResponse(ApiKeyResponse):
    raw_key: str  # shown once at creation; never stored


# ── account ───────────────────────────────────────────────────────────────────


class PatchAccountRequest(BaseModel):
    display_name: str | None = None
    new_password: str | None = Field(None, min_length=8)
    current_password: str | None = None  # required when new_password is set


class DeleteAccountRequest(BaseModel):
    # Step-up re-auth for an irreversible, destructive action (account + ALL data).
    current_password: str = Field(min_length=1)


# ── cost cap ──────────────────────────────────────────────────────────────────


class PatchCostCapRequest(BaseModel):
    monthly_cost_cap_usd: float = Field(gt=0, le=500)


# ── entitlement sync (service-to-service: the billing service → gtm-engine) ─────────


class EntitlementSyncRequest(BaseModel):
    # entitlement drives capability gating; cap_usd is the billing service's priced spend ceiling
    # (gtm-engine stores + enforces it, never decides it). sync_id dedups; version orders.
    entitlement: Literal["free", "pro", "pro_plus"]
    cap_usd: float = Field(ge=0, le=500)
    sync_id: str = Field(min_length=1, max_length=200)
    version: int | None = None
    status: Literal["active", "past_due", "canceled", "trialing"] | None = None


class EntitlementSyncResponse(BaseModel):
    applied: bool
    outcome: str  # new | duplicate | stale | unknown


# ── usage (lean spend-vs-cap read for the billing service paywall/overage UI) ──────────────


class UsageResponse(BaseModel):
    period: str  # e.g. "current_month"
    period_start: str  # ISO-8601 UTC
    spent_usd: float
    cap_usd: float
    over_cap: bool


# ── errors ────────────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    detail: str


# ── Onboarding (profile-onboard-ingestion, PRD 2026-06-20) ───────────────────


class OnboardIngestRequest(BaseModel):
    """POST /v1/onboard/ingest request body."""

    source_type: Literal["url", "file", "text"]
    source: str = Field(min_length=1, description="URL, file path, or raw text")
    company_confirmation: str | None = Field(
        None, description="Operator-confirmed company name (overrides brain extraction)"
    )
    additional_notes: str | None = None


class OnboardIngestResponse(BaseModel):
    """POST /v1/onboard/ingest response."""

    draft_id: str
    slug: str
    staged_files: list[str]
    confidence: str
    gaps: list[str]
    flags: dict[str, str] = Field(default_factory=dict)


class OnboardProductExtractRequest(BaseModel):
    """POST /v1/onboard/{id}/product/{slug}/extract request body."""

    source_type: Literal["url", "file", "text"]
    source: str = Field(min_length=1)


class FileDiff(BaseModel):
    """One file's diff entry."""

    old: str | None
    new: str


class OnboardDiffResponse(BaseModel):
    """GET /v1/onboard/{id}/diff response."""

    draft_id: str
    slug: str
    diffs: dict[str, FileDiff]


class OnboardPromoteRequest(BaseModel):
    """POST /v1/onboard/{id}/promote request body — ops form fields."""

    # Required operator confirmation (tenant boundary safeguard — PRD §7)
    confirmed_company_name: str = Field(min_length=1)

    # Ops form overrides
    telegram_chat_id: int | None = None
    monthly_tool_budget_usd: float | None = None
    per_run_cap_usd: float | None = None
    additional_notes: str | None = None

"""Pydantic v2 request/response schemas for the backend API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

from .types import UuidStr

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


class ExchangeRequest(BaseModel):
    """POST /v1/auth/exchange — an external IdP's JWT (A3). Size/shape are enforced
    in backend/oidc.py so every rejection is a uniform 401, not a 422 oracle."""

    token: str = Field(min_length=1)


class LogoutResponse(BaseModel):
    status: Literal["logged_out"] = "logged_out"


# ── workspace ─────────────────────────────────────────────────────────────────


class WorkspaceResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    entitlement: str  # free | pro | pro_plus
    monthly_cost_cap_usd: float
    publish_enabled: bool = False
    schedule_enabled: bool = False


class PatchWorkspaceRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200, description="Workspace display name")


# ── profiles ─────────────────────────────────────────────────────────────────


class ProfileResponse(BaseModel):
    profile_name: str
    is_default: bool


class ProfilesListResponse(BaseModel):
    profiles: list[ProfileResponse]
    active: str  # currently active profile_name for this session


class ActivateProfileRequest(BaseModel):
    # Same shape as the DB's own CHECK (profiles.profile_name, V001__data_spine.sql) —
    # enforced here so a bad name is a 422 from request validation, never a raw
    # asyncpg CheckViolationError surfacing as a 500 (RT-14).
    profile_name: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")


# ── agents (A4) ──────────────────────────────────────────────────────────────

# RFC 5646 language-tag SHAPE (syntax only, per the A7 boundary rule): primary
# subtag + optional alnum subtags, length-capped. Not a registry validation.
_BCP47_SHAPE = r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$"


class AgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    profile_name: str
    packs: list[str] | None = None  # None = every pack the profile activates
    language: str | None = Field(default=None, pattern=_BCP47_SHAPE, max_length=35)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    # V026: mirrors the DB's own NOT NULL DEFAULT 'own' / CHECK(>= 0) constraints so a
    # bad value is a clean 422 here, never a raw asyncpg CheckViolationError (RT-14).
    read_scope: Literal["own", "workspace"] = "own"
    daily_dispatch_cap: int | None = Field(default=None, ge=0)


class AgentUpdateRequest(BaseModel):
    """PATCH /v1/agents/{id} — partial update. ``status`` may only move between
    active and paused here; archiving is DELETE's job (and is irreversible)."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    packs: list[str] | None = None
    language: str | None = Field(default=None, pattern=_BCP47_SHAPE, max_length=35)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    status: Literal["active", "paused"] | None = None
    read_scope: Literal["own", "workspace"] | None = None
    daily_dispatch_cap: int | None = Field(default=None, ge=0)


class AgentResponse(BaseModel):
    agent_id: str
    workspace_id: str
    name: str
    profile_name: str
    packs: list[str] | None = None
    language: str | None = None
    monthly_budget_usd: float | None = None
    status: str  # active | paused | archived (open-world for clients)
    is_default: bool = False
    read_scope: str = "own"
    daily_dispatch_cap: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


# ── runs ─────────────────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    """POST /v1/runs — two modes, one body (additive; prompt mode unchanged).

    Prompt mode: {profile_name, prompt}                — the pre-A1 behaviour.
    Pack mode:   {profile_name, pack, variant, inputs} — dispatches the graph
                 runner over packs/<pack>/graphs/<variant>.toml; `inputs` is the
                 v1 flat string map covering the variant's `ask` settings
                 (pack-descriptor contract).

    ``agent_id`` (A4, optional): run AS the named agent — the agent's profile
    binds the run (``profile_name`` may then be omitted; if given it must match),
    and the agent's pack subset / budget / paused state are enforced. Absent:
    ``profile_name`` is required as before, and the run is attributed to the
    workspace's default agent (attribution only — no narrowing).
    """

    profile_name: str | None = None
    prompt: str | None = None  # natural language trigger, e.g. "run market-scan"
    dry_run: bool = False  # if True, pipeline logs but never publishes
    pack: str | None = None
    variant: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    # Validated as a UUID at the boundary; carried downstream as its canonical string.
    agent_id: UuidStr | None = None
    # A7: BCP-47 output language. Precedence request > agent > profile. Shape-
    # validated HERE so a malformed tag 422s at the boundary and never reaches
    # the session env (registry validity is not checked — shape + length only).
    language: str | None = Field(default=None, pattern=_BCP47_SHAPE, max_length=35)
    # RT-04: an optional client-chosen idempotency key. A retry after a lost 202 (the
    # realistic case on a mobile network) sends the SAME id — the server returns the
    # ORIGINAL run instead of creating a duplicate that would burn a second concurrency
    # slot and spend budget twice. Absent (the default) is byte-identical to pre-RT-04
    # behaviour: every id-less request is its own run, as today.
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)
    # Fleet Phase B (G1): external correlation id.
    external_ref: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    # Fleet Phase C (G2): run-scoped documents the caller supplies.
    context: dict[str, str] | None = None

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> RunRequest:
        if self.pack is not None:
            if not self.variant:
                raise ValueError("pack mode requires 'variant'")
            if self.prompt:
                raise ValueError("provide either 'prompt' or 'pack'/'variant', not both")
        elif not (self.prompt or "").strip():
            raise ValueError("'prompt' is required (or 'pack' + 'variant' for pack mode)")
        if self.agent_id is None and not (self.profile_name or "").strip():
            raise ValueError("'profile_name' is required when no 'agent_id' is given")
        return self


class RunResponse(BaseModel):
    run_id: str
    # queued | pending | running | awaiting_approval | ok | failed | rejected. A bare
    # `str`, not an enum, precisely so a new value is additive: A5 made `queued` the
    # state POST /v1/runs now returns (the run is durably enqueued, not yet claimed by a
    # worker), and `pending` remains legal for rows written before it.
    status: str
    profile_name: str
    # A4: attribution — the agent this run executes as (None on pre-A4 rows).
    agent_id: str | None = None
    # The recipe this run dispatched — None for prompt-mode runs and pre-A1 rows.
    # Sourced from the durable `payload` column (A5), so no new write path is needed.
    pack: str | None = None
    variant: str | None = None
    inputs: dict[str, Any] | None = Field(default=None, description="Runtime input parameters")
    # ISO-8601 UTC; when the row was enqueued. None only for pre-V005 rows.
    created_at: str | None = None
    stages: list[dict] = []
    # M-07: error code from closed set (services/runs/persistence.py RunErrorCode).
    error_code: str | None = None
    pending_gate: str | None = None
    pending_content: str | None = None  # set when status == awaiting_approval
    # sha256 of pending_content — the client echoes it back as GateRequest.content_sha
    # so an approval is bound to the EXACT bytes shown (publish-gate integrity, H9).
    pending_content_sha: str | None = None
    # The open gate's parsed kind (plan | publish | email_enroll | review) and the id of
    # the paused pack node — the same vocabulary as the `awaiting_approval` stream event
    # and the push payload, now also on the poll path (client issue #240). None once the
    # gate is decided, on a prompt run (no graph node), or on a run that was never gated.
    # `pending_gate` above stays the legacy sentinel — prefer these two instead.
    gate: str | None = None
    pending_node_id: str | None = None
    # Protocol-1 additive polling fields (pack-mode runs; None on prompt runs and
    # pre-A2 rows): same vocabulary as the SSE snapshot's nodes[]/content[].
    nodes: list[dict] | None = None
    content: list[dict] | None = None
    # Fleet Phase A (V026, additive): who started this run — "user" or "service", and
    # that principal's own subject (a users.id or an api_keys.id — never a secret).
    # None on every pre-Fleet-Phase-A row.
    principal_kind: str | None = None
    principal_id: str | None = None
    # A6 (2026-09-20): graceful degradation fallback. Set to "checklist" if a write
    # tool was denied but the agent successfully generated manual steps for the operator.
    fallback_mode: str | None = None
    # Fleet Phase B (G1, additive): external correlation id.
    external_ref: str | None = None


class GateRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    edited_content: str | None = None  # non-null only when decision == "edit"
    # REQUIRED: sha256 of the EXACT pending_content the operator saw. decide_gate
    # rejects the decision unless it matches the run's current pending_content, so a
    # stale/duplicate/omitted decision can never resolve a later gate of the same run
    # (H9). The client already holds pending_content — it rendered it — so it can
    # always compute this. (Was optional during rollout; now enforced.)
    content_sha: str = Field(min_length=1)

    @model_validator(mode="after")
    def _edit_requires_edited_content(self) -> GateRequest:
        if self.decision == "edit" and self.edited_content is None:
            raise ValueError("edited_content is required when decision is 'edit'")
        return self


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


class AgentCostRollupItem(BaseModel):
    agent_id: str | None = None
    principal_kind: str | None = None
    principal_id: str | None = None
    runs: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class AgentCostRollupResponse(BaseModel):
    """Per-agent and per-principal cost rollup across a workspace (Fleet PRD §3 G6)."""

    total_usd: float = 0.0
    items: list[AgentCostRollupItem] = Field(default_factory=list)


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
    # Fleet Phase A (V026): bind this key to a gtm-engine agent at mint time — a run
    # made with the key becomes a "service" Principal narrowed to that agent's own
    # pack subset/budget/cap (backend/callers/). None (the default) mints an unbound
    # key — today's pre-Fleet-Phase-A behaviour, unchanged.
    agent_id: UuidStr | None = None


class ApiKeyResponse(BaseModel):
    id: str
    prefix: str
    label: str | None
    entitlement: str
    last_used_at: str | None
    created_at: str
    is_revoked: bool
    agent_id: str | None = None
    principal_kind: Literal["service", "tool"] = Field(
        "tool",
        description=(
            "Distinguishes machine 'service' principal tokens (bound to an agent) from "
            "MCP 'tool' keys. These are machine tokens, never human credentials (RT-13)."
        ),
    )


class ApiKeyCreateResponse(ApiKeyResponse):
    raw_key: str  # shown once at creation; never stored


# ── account ───────────────────────────────────────────────────────────────────


class AccountResponse(BaseModel):
    id: str
    email: str
    primary_workspace_id: str
    tier: str
    role: str
    display_name: str | None = None


class PatchAccountRequest(BaseModel):
    display_name: str | None = None
    new_password: str | None = Field(None, min_length=8)
    current_password: str | None = None  # required when new_password is set


class DeleteAccountRequest(BaseModel):
    """Step-up re-auth for an irreversible, destructive action (account + ALL data).

    Both fields are optional here because which one is REQUIRED depends on the account
    type, which is only known once the user row is loaded — the handler enforces it.
    """

    current_password: str | None = Field(None, min_length=1)  # password accounts
    idp_token: str | None = None  # federated accounts: a fresh token from the IdP


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


class PublishSettingsSyncRequest(BaseModel):
    # A5: per-workspace publish destination, set ONLY by the service (require_service_auth),
    # never a user JWT and never client run inputs. `secret_ref` is an env-var NAME
    # (PUBLISH_*), never the secret value — resolved at dispatch. `url` must be https.
    enabled: bool = False
    url: str | None = Field(None, max_length=2000)
    secret_ref: str | None = Field(None, max_length=200)
    # V020: scheduling gets its OWN opt-in, on top of `enabled` — mirrors the VPS
    # HERMES_SCHEDULE_ENABLED kill switch, but per workspace. Closed by default.
    #
    # ``None`` (the default, and what a caller predating V020 sends by simply never
    # including the field) means "leave this workspace's current value alone" — the
    # route merges it against the existing row rather than overwriting. A caller
    # that omitted these fields before V020 existed must not silently flip a
    # previously-enabled schedule back off on its next routine sync call.
    schedule_enabled: bool | None = None
    schedule_max_horizon_days: int | None = Field(None, ge=1, le=3650)


class PublishSettingsSyncResponse(BaseModel):
    applied: bool
    outcome: str  # set | cleared | unknown


# ── usage (lean spend-vs-cap read for the billing service paywall/overage UI) ──────────────


class UsageResponse(BaseModel):
    period: str  # e.g. "current_month"
    period_start: str  # ISO-8601 UTC
    spent_usd: float
    cap_usd: float
    over_cap: bool


# ── errors ────────────────────────────────────────────────────────────────────


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    detail: Any = None


# Canonical OpenAPI error response declarations (M-03).
# Applied to all routers so generated client SDKs receive typed error envelopes
# ({error: {code, message, details}, detail}) instead of legacy HTTPValidationError.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Bad Request"},
    401: {"model": ErrorResponse, "description": "Unauthorized"},
    403: {"model": ErrorResponse, "description": "Forbidden"},
    404: {"model": ErrorResponse, "description": "Not Found"},
    409: {"model": ErrorResponse, "description": "Conflict"},
    422: {"model": ErrorResponse, "description": "Validation Error"},
    429: {"model": ErrorResponse, "description": "Too Many Requests"},
    500: {"model": ErrorResponse, "description": "Internal Server Error"},
}


# ── Onboarding (profile-onboard-ingestion, PRD 2026-06-20) ───────────────────


class OnboardIngestRequest(BaseModel):
    """POST /v1/onboard request body (the ingest step)."""

    # B2: "file" is deliberately NOT accepted on the network API — a remote client
    # cannot meaningfully reference a server-local path, and doing so let any tenant
    # read arbitrary server files back through the diff endpoint. The CLI/VPS
    # onboarding path (agent.onboard.ingest) keeps "file" for the operator on-box.
    source_type: Literal["url", "text"]
    source: str = Field(min_length=1, description="URL or raw text")


class OnboardIngestResponse(BaseModel):
    """POST /v1/onboard response."""

    draft_id: str
    slug: str
    staged_files: list[str]
    confidence: str
    gaps: list[str]
    flags: dict[str, str] = Field(default_factory=dict)


class OnboardJobError(BaseModel):
    """Why an onboarding job failed: the status and envelope the synchronous route returned."""

    status: int
    code: str
    message: str


class OnboardJobResponse(BaseModel):
    """202 from POST /v1/onboard and the product re-extract; GET /v1/onboard/jobs/{id}.

    Poll until ``status`` is ``succeeded`` (``result`` holds the draft) or ``failed``
    (``error`` holds the code).
    """

    job_id: str
    kind: Literal["ingest", "product_extract"]
    status: Literal["pending", "running", "succeeded", "failed"]
    result: OnboardIngestResponse | None = None
    error: OnboardJobError | None = None
    created_at: str
    updated_at: str


class OnboardProductExtractRequest(BaseModel):
    """POST /v1/onboard/{id}/product/{slug}/extract request body."""

    # B2: no "file" on the network API (see OnboardIngestRequest).
    source_type: Literal["url", "text"]
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


# ── webhooks ──────────────────────────────────────────────────────────────────


class WebhookResponse(BaseModel):
    """POST /v1/webhooks/{provider}/{workspace_id} response."""

    run_id: str | None = None
    status: str  # "queued" | "duplicate"
    message: str | None = None

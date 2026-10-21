"""One-call, repeatable agent setup (W1, PRD 2026-10-19a).

``POST /v1/agents/provision`` turns "seven calls in the right order" into one request that is safe to repeat.
A call advances a durable job (``provisioning_jobs``, V047) as far as it can and says where it stopped:

    precheck (every call) -> integrations -> profile -> agent -> readiness -> key

It pauses at exactly one place a person must act: ``awaiting_profile_confirmation``, the existing profile
review. Nothing runs in the background while parked; the caller repeats the SAME request (same
``Idempotency-Key``) after confirming, and the job resumes from its recorded state.

Properties that hold by construction:

* **Repeat is safe.** ``(workspace, idempotency_key)`` is unique, so a repeat finds its own job. The key is
  bound to a hash of the request that first used it; a different request is ``409 idempotency_key_reused``.
  Integration key VALUES are not in the hash, so a corrected key may be sent under the same idempotency key.
* **One paid onboarding per source.** A partial unique index allows one ACTIVE job per source; a second
  idempotency key for the same site or profile is handed the first job and starts nothing.
* **No spend before the checks.** ``precheck`` (entitlement, a wallet with credit, the §R2 cap) and the
  provider-key checks come before the onboarding job that spends.
* **One caller at a time.** A 120 s lease; a caller that died is taken over from the recorded state.
* **The raw key is returned exactly once**, in the response of the call whose transaction commits it, and is
  never stored or logged. ``agent -> key`` are two transactions; ``key_id`` is written in the SAME transaction
  that mints the key. A lost response is repaired with ``reissue_key``, which revokes only the key THIS job
  minted.
* **No new writers.** Every step calls the function the existing route calls (``insert_agent``,
  ``insert_api_key``, ``upsert_credential``, the onboarding job, the readiness checker); none is reached over
  HTTP.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, status

from ..agents import agent_name_taken, fetch_agent, insert_agent
from ..database import workspace_scope
from ..deps import WorkspaceCtx
from . import onboard_jobs
from .api_keys import insert_api_key
from .integrations_verify import verify_provider_credential
from .provision_checks import compute_blockers, precheck, store_integrations
from .provision_model import (
    ACTIVE,
    ALL_STATES,
    TERMINAL,
    ProvisionRequest,
    ProvisionResponse,
    jload,
    normalise_source,
    request_hash,
)
from .provision_store import (
    _COLS,
    _acquire_lease,
    _get_or_create,
    _release_lease,
    _update,
)
from .provision_view import _view

log = logging.getLogger(__name__)

__all__ = [
    "ACTIVE",
    "ALL_STATES",
    "TERMINAL",
    "ProvisionRequest",
    "ProvisionResponse",
    "abandon",
    "compute_blockers",
    "get_job_view",
    "normalise_source",
    "precheck",
    "provision",
    "request_hash",
    "verify_provider_credential",
]


def _checkpoint(name: str) -> None:
    """A named point between two commits. A no-op; the crash tests replace it to die exactly here."""


async def _fail(pool: Any, ws_id: str, job: dict, code: str, message: str) -> dict:
    async with workspace_scope(pool, ws_id) as conn:
        return await _update(
            conn, ws_id, job["id"], state="failed", errors=[{"code": code, "message": message}]
        )


def _current_draft_id(cfg: Any, slug: str) -> str | None:
    """The draft id staged for ``slug`` NOW, read from its staging directory (a re-extract replaces the id)."""
    from agent.onboard.slug import _STAGING_DIR, slugify

    try:
        if slugify(slug) != slug:
            return None
        meta = json.loads(
            (cfg.profiles_root / _STAGING_DIR / slug / ".onboard-meta.json").read_text("utf-8")
        )
    except (OSError, ValueError):
        return None
    draft_id = meta.get("draft_id") if isinstance(meta, dict) else None
    return draft_id if isinstance(draft_id, str) else None


async def _profile_promoted(pool: Any, ws_id: str, slug: str) -> bool:
    async with workspace_scope(pool, ws_id) as conn:
        return bool(
            await conn.fetchval(
                "SELECT 1 FROM profiles WHERE workspace_id = $1::uuid AND profile_name = $2",
                ws_id,
                slug,
            )
        )


async def _start_onboarding(
    pool: Any, cfg: Any, ws: WorkspaceCtx, job: dict, req: ProvisionRequest
) -> dict:
    from ..routers import onboard as onboard_router
    from ..schemas import OnboardIngestRequest

    ws_id = ws.workspace_id
    body = OnboardIngestRequest(source_type="url", source=req.source_url or "")
    key = onboard_jobs.source_key("ingest", "url", body.source)
    record, created = onboard_jobs.create_job(cfg, ws_id, "ingest", key)
    if created:
        onboard_jobs.spawn(cfg, record, lambda: onboard_router._ingest(body, ws, cfg))
    _checkpoint("onboarding_started")
    async with workspace_scope(pool, ws_id) as conn:
        return await _update(
            conn,
            ws_id,
            job["id"],
            state="onboarding_running",
            step="profile",
            onboard_job_id=record["job_id"],
        )


async def _create_agent(
    pool: Any, cfg: Any, ws: WorkspaceCtx, job: dict, req: ProvisionRequest, profile_name: str
) -> dict:
    from ..routers.agents import _validate_narrowing

    ws_id = ws.workspace_id
    try:
        async with workspace_scope(pool, ws_id) as conn:
            await _validate_narrowing(
                conn,
                cfg.repo_root,
                ws,
                profile_name=profile_name,
                packs=req.packs,
                monthly_budget_usd=req.monthly_budget_usd,
            )
            if await agent_name_taken(conn, ws_id, req.name):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {"code": "agent_name_taken", "message": "Agent name is already taken"},
                )
            agent = await insert_agent(
                conn,
                ws_id,
                name=req.name,
                profile_name=profile_name,
                packs=req.packs,
                language=None,
                monthly_budget_usd=req.monthly_budget_usd,
                read_scope="own",
                daily_dispatch_cap=req.daily_dispatch_cap,
            )
            job = await _update(
                conn,
                ws_id,
                job["id"],
                state="agent_created",
                step="readiness",
                agent_id=agent["agent_id"],
                profile_slug=profile_name,
            )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        await _fail(
            pool,
            ws_id,
            job,
            str(detail.get("code", "agent_create_failed")),
            str(detail.get("message", "")),
        )
        raise
    _checkpoint("agent_committed")
    return job


async def _mint_key(
    pool: Any,
    ws: WorkspaceCtx,
    job: dict,
    req: ProvisionRequest,
    blockers: list[dict],
    *,
    replace: bool,
) -> tuple[dict, str]:
    """Mint the agent's key and record it on the job in ONE transaction. ``replace`` revokes the key this job
    minted before (and no other)."""
    ws_id = ws.workspace_id
    state = "ready_with_blockers" if blockers else "ready"
    async with workspace_scope(pool, ws_id) as conn:
        if replace and job.get("key_id"):
            await conn.execute(
                "UPDATE api_keys SET revoked_at = now() WHERE id = $1::uuid AND workspace_id = $2::uuid "
                "AND revoked_at IS NULL",
                job["key_id"],
                ws_id,
            )
        row, raw = await insert_api_key(
            conn,
            ws_id,
            label=req.key_label,
            requested_entitlement=ws.entitlement.value,
            workspace_entitlement=ws.entitlement.value,
            agent_id=job["agent_id"],
        )
        job = await _update(
            conn,
            ws_id,
            job["id"],
            state=state,
            step="done",
            key_id=row["id"],
            blockers=blockers,
        )
    _checkpoint("key_committed")
    return job, raw


class _Run:
    """What one call carries between steps: the lease-holder's identity, the request and the call's outputs."""

    def __init__(
        self, pool: Any, cfg: Any, ws: WorkspaceCtx, req: ProvisionRequest, on_changed: Any
    ):
        self.pool, self.cfg, self.ws, self.req, self.on_changed = pool, cfg, ws, req, on_changed
        self.warnings: list[str] = []
        self.raw_key: str | None = None
        self.draft_id: str | None = None

    def view(self, job: dict) -> tuple[int, dict]:
        return _view(job, raw_key=self.raw_key, warnings=self.warnings, draft_id=self.draft_id)


async def _step_terminal(r: _Run, job: dict) -> tuple[dict, tuple[int, dict]]:
    """A finished job is re-checked, not re-run: readiness is live, so ``ready_with_blockers`` can become
    ``ready`` once the caller fixes what was named. ``reissue_key`` replaces THIS job's key and no other."""
    ws_id = r.ws.workspace_id
    if job["state"] in ("ready", "ready_with_blockers") and job.get("agent_id"):
        async with workspace_scope(r.pool, ws_id) as conn:
            agent = await fetch_agent(conn, ws_id, job["agent_id"])
        blockers = await compute_blockers(r.pool, r.cfg, ws_id, agent) if agent else []
        if r.req.reissue_key and job.get("key_id"):
            job, r.raw_key = await _mint_key(r.pool, r.ws, job, r.req, blockers, replace=True)
        else:
            want = "ready_with_blockers" if blockers else "ready"
            if want != job["state"] or blockers != jload(job["blockers"]):
                async with workspace_scope(r.pool, ws_id) as conn:
                    job = await _update(conn, ws_id, job["id"], state=want, blockers=blockers)
    return job, r.view(job)


async def _step_started(r: _Run, job: dict) -> tuple[dict, tuple[int, dict] | None]:
    """Name free, provider keys verified and stored, then either the agent (a profile that exists) or the
    onboarding job (a URL). Nothing that spends runs before the first two."""
    ws_id, req = r.ws.workspace_id, r.req
    async with workspace_scope(r.pool, ws_id) as conn:
        taken = await agent_name_taken(conn, ws_id, req.name)
    if taken:
        # The job ends here: left active it would hold the source's slot and hand its stale state to the
        # next key that asks for the same source.
        await _fail(r.pool, ws_id, job, "agent_name_taken", "Agent name is already taken")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "agent_name_taken", "message": "Agent name is already taken"},
        )
    if req.integrations:
        errors, r.warnings, stored = await store_integrations(
            r.pool, ws_id, req.integrations, r.on_changed, verify_provider_credential
        )
        async with workspace_scope(r.pool, ws_id) as conn:
            job = await _update(
                conn,
                ws_id,
                job["id"],
                step="integrations",
                integrations=sorted(set(job.get("integrations") or []) | set(stored)),
                errors=errors,
            )
        if errors:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {
                    "code": "provider_keys_invalid",
                    "message": "Some provider keys were not accepted. The valid ones were saved; "
                    "fix the others and repeat this request with the same Idempotency-Key.",
                    "errors": errors,
                    "job_id": job["id"],
                },
            )
    if req.profile_name is not None:
        return await _create_agent(r.pool, r.cfg, r.ws, job, req, req.profile_name.strip()), None
    job = await _start_onboarding(r.pool, r.cfg, r.ws, job, req)
    return job, r.view(job)


async def _step_onboarding(r: _Run, job: dict) -> tuple[dict, tuple[int, dict] | None]:
    """Follow the real onboarding job. The draft is read from its staging directory NOW, never from an id
    stored earlier (a product re-extract replaces the draft id)."""
    pool, ws_id = r.pool, r.ws.workspace_id
    rec = onboard_jobs.get_job(r.cfg, ws_id, job["onboard_job_id"] or "")
    if rec is None:
        return await _fail(
            pool, ws_id, job, "onboarding_lost", "The onboarding job could not be found."
        ), None
    if rec["status"] in ("pending", "running"):
        return job, r.view(job)
    if rec["status"] == "failed":
        err = rec.get("error") or {}
        return (
            await _fail(
                pool,
                ws_id,
                job,
                str(err.get("code", "onboarding_failed")),
                str(err.get("message", "Onboarding failed.")),
            ),
            None,
        )
    slug = ((rec.get("result") or {}).get("slug")) or ""
    if await _profile_promoted(pool, ws_id, slug):
        return await _create_agent(pool, r.cfg, r.ws, job, r.req, slug), None
    r.draft_id = _current_draft_id(r.cfg, slug)
    if r.draft_id is None:
        return (
            await _fail(
                pool,
                ws_id,
                job,
                "draft_discarded",
                "The profile draft was discarded before it was confirmed.",
            ),
            None,
        )
    if job["state"] != "awaiting_profile_confirmation" or job["profile_slug"] != slug:
        async with workspace_scope(pool, ws_id) as conn:
            job = await _update(
                conn, ws_id, job["id"], state="awaiting_profile_confirmation", profile_slug=slug
            )
    return job, r.view(job)


async def _step_agent_created(r: _Run, job: dict) -> tuple[dict, None]:
    ws_id = r.ws.workspace_id
    async with workspace_scope(r.pool, ws_id) as conn:
        agent = await fetch_agent(conn, ws_id, job["agent_id"])
    blockers = await compute_blockers(r.pool, r.cfg, ws_id, agent)
    job, r.raw_key = await _mint_key(r.pool, r.ws, job, r.req, blockers, replace=False)
    return job, None


async def _advance(
    pool: Any, cfg: Any, ws: WorkspaceCtx, job: dict, req: ProvisionRequest, on_changed: Any
) -> tuple[int, dict]:
    r = _Run(pool, cfg, ws, req, on_changed)
    while True:
        state = job["state"]
        if state in TERMINAL:
            return (await _step_terminal(r, job))[1]
        if state == "started":
            job, done = await _step_started(r, job)
        elif state in ("onboarding_running", "awaiting_profile_confirmation"):
            job, done = await _step_onboarding(r, job)
        elif state == "agent_created":
            job, done = await _step_agent_created(r, job)
        else:
            raise RuntimeError(f"unknown provisioning state: {state!r}")  # never read as 'fine'
        if done is not None:
            return done


# ── public API ────────────────────────────────────────────────────────────────


async def provision(
    pool: Any,
    cfg: Any,
    ws: WorkspaceCtx,
    *,
    idempotency_key: str,
    req: ProvisionRequest,
    on_credentials_changed: Any = None,
) -> tuple[int, dict]:
    """Advance (or replay) the setup for ``idempotency_key``. Returns ``(http_status, ProvisionResponse dict)``."""
    source_key = normalise_source(req)  # 422 on bad input, before anything else
    req_hash = request_hash(req, source_key)
    await precheck(pool, ws)
    job, own = await _get_or_create(
        pool, ws.workspace_id, idempotency_key, req_hash, source_key, req
    )
    if not own:
        return _view(job)  # another key's active job for this source: shown, never advanced
    if job["state"] in ("failed", "abandoned"):
        return _view(job)  # a replay of a finished-without-an-agent job: shown, never advanced
    job = await _acquire_lease(pool, ws.workspace_id, job)
    try:
        return await _advance(pool, cfg, ws, job, req, on_credentials_changed)
    finally:
        await _release_lease(pool, ws.workspace_id, job["id"])


async def get_job_view(pool: Any, ws_id: str, job_id: str) -> tuple[int, dict] | None:
    async with workspace_scope(pool, ws_id) as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM provisioning_jobs j "  # noqa: S608 # nosec B608 — module constant
            "WHERE j.id = $1::uuid AND j.workspace_id = $2::uuid",
            job_id,
            ws_id,
        )
    if row is None:
        return None
    job = dict(row)
    if job["state"] in ACTIVE and job["expires_at"] <= _now_utc():
        job["state"] = "abandoned"  # read-side only; the next write path persists it
    return _view(job)


def _now_utc():
    from datetime import UTC, datetime

    return datetime.now(UTC)


async def abandon(pool: Any, ws_id: str, job_id: str) -> tuple[int, dict] | None:
    """Abandon a job that has not minted a key (frees its source slot). 409 once a key exists."""
    async with workspace_scope(pool, ws_id) as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM provisioning_jobs j "  # noqa: S608 # nosec B608 — module constant
            "WHERE j.id = $1::uuid AND j.workspace_id = $2::uuid FOR UPDATE",
            job_id,
            ws_id,
        )
        if row is None:
            return None
        job = dict(row)
        if job.get("key_id"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "provision_key_issued",
                    "message": "This setup already issued an API key, so it can no longer be cancelled. "
                    "Revoke the key (DELETE /v1/api-keys/{id}) or archive the agent instead.",
                },
            )
        if job["state"] in ACTIVE:
            job = await _update(conn, ws_id, job_id, state="abandoned")
    return _view(job)

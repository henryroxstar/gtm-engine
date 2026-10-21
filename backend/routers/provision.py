"""One-call agent setup — ``POST /v1/agents/provision`` (W1, PRD 2026-10-19a).

The state machine is ``backend/services/provision.py``; this module is the HTTP shell: the kill switch, the
header, the status code, and the rate limit. User-only on purpose — the route is NOT on the
``require_principal`` allowlist, so a ``service`` principal's ``sk-`` token fails the user-JWT decode and
gets 401: a machine cannot mint itself a key.

Closed by default (``GTM_AGENT_PROVISION_ENABLED``): a shut route answers 404, as though it did not exist,
and the individual routes (``POST /v1/agents``, ``/v1/api-keys``, ``/v1/onboard``) keep working.
"""

from __future__ import annotations

import os
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from ..deps import WorkspaceCtx, require_auth
from ..ratelimit import limiter
from ..schemas import ERROR_RESPONSES
from ..services import provision as svc
from ..services.provision import ProvisionRequest, ProvisionResponse
from ..types import UuidStr

router = APIRouter(prefix="/agents/provision", tags=["agents"], responses=ERROR_RESPONSES)

SETTING = "GTM_AGENT_PROVISION_ENABLED"
_ON = frozenset({"1", "true", "yes", "on"})
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def provision_enabled() -> bool:
    """True only for an unambiguous 'on'. Anything else (unset, empty, a typo) leaves the route shut."""
    return (os.environ.get(SETTING) or "").strip().lower() in _ON


async def require_enabled() -> None:
    if not provision_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


def valid_idempotency_key(value: str | None) -> str:
    if value is None or not _KEY_SHAPE.fullmatch(value):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "code": "idempotency_key_invalid",
                "message": "Send an Idempotency-Key header of 1 to 128 letters, digits or . _ : -",
            },
        )
    return value


async def workspace_cfg(pool, base_cfg, workspace_id: str):
    """The config scoped to one workspace's tree (what ``/v1/onboard`` builds per request)."""
    from ..services.integrations import get_workspace_credentials
    from ..session import _workspace_scoped_config

    creds = await get_workspace_credentials(pool, workspace_id)
    return _workspace_scoped_config(base_cfg, workspace_id, base_cfg.repo_root, credentials=creds)


@router.post(
    "",
    response_model=ProvisionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_enabled)],
)
@limiter.limit("10/minute")
async def provision_agent(
    body: ProvisionRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ProvisionResponse:
    """Set up a working agent in one repeatable call.

    Advances as far as it can and says where it stopped (``state``, ``message``, ``next_action``). Repeat the
    SAME request with the SAME ``Idempotency-Key`` to resume. ``202`` while waiting or parked for the profile
    review, ``200`` when finished. The raw API key is in the response of the call that finishes setup and
    nowhere else; ``reissue_key`` replaces it if that response was lost.
    """
    key = valid_idempotency_key(idempotency_key)
    svc.normalise_source(body)  # 422 on a malformed request before any database work
    pool = request.app.state.pool
    cfg = await workspace_cfg(pool, request.app.state.cfg, ws.workspace_id)

    async def _evict() -> None:
        # New provider keys: warm sessions must not keep the old ones (as PUT /v1/integrations does).
        if hasattr(request.app.state, "sessions"):
            await request.app.state.sessions.evict_workspace(ws.workspace_id, close_in_use=False)
        from ..broker import publish_workspace_evict

        await publish_workspace_evict(ws.workspace_id, reason="credentials_rotated")

    code, out = await svc.provision(
        pool, cfg, ws, idempotency_key=key, req=body, on_credentials_changed=_evict
    )
    response.status_code = code
    return ProvisionResponse(**out)


@router.get(
    "/{job_id}",
    response_model=ProvisionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_enabled)],
)
async def get_provision_job(
    job_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> ProvisionResponse:
    """A setup job's state. Never carries the raw key. 404 for an unknown job or another workspace's."""
    found = await svc.get_job_view(request.app.state.pool, ws.workspace_id, job_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provisioning job")
    return ProvisionResponse(**found[1])


@router.delete(
    "/{job_id}",
    response_model=ProvisionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_enabled)],
)
async def abandon_provision_job(
    job_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> ProvisionResponse:
    """Abandon a setup that has not issued a key (no spend; frees its slot). 409 once a key exists."""
    found = await svc.abandon(request.app.state.pool, ws.workspace_id, job_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provisioning job")
    return ProvisionResponse(**found[1])

"""Service-to-service per-workspace publish-destination sync (A5).

Sets where a workspace's approved Gate-2 posts go. Auth: the shared service secret
(``BILLING_SYNC_SECRET``) via ``require_service_auth`` — **NOT** a user JWT. A tenant
able to set its own publish URL would be an arbitrary-egress/exfil hole strictly worse
than the process-global destination this replaces, so the write is service-only.

The secret is never sent here: ``secret_ref`` names a ``PUBLISH_*`` env var
(Doppler-injected) resolved at dispatch, so no credential is stored in the row. ``url``
must be https (and, when ``PUBLISH_URL_ALLOWLIST`` is set, one of its hosts). Destination
pinning stays server-side and is not representable in anything the brain or client produces.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Annotated, Any
from urllib.parse import urlparse

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..database import workspace_scope
from ..deps import require_service_auth
from ..schemas import PublishSettingsSyncRequest, PublishSettingsSyncResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/publish-settings", tags=["publish-settings"])

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
# Must match backend.publish_dispatch._SECRET_REF_RE — the dispatch-side guard.
_SECRET_REF_RE = re.compile(r"^PUBLISH_[A-Z0-9_]+$")


def _validate_url(url: str) -> None:
    """https-only, plus an optional host allowlist (`PUBLISH_URL_ALLOWLIST`, comma-sep)."""
    if not url.startswith("https://"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "url_must_be_https"})
    allow = (os.getenv("PUBLISH_URL_ALLOWLIST") or "").strip()
    if allow:
        hosts = {h.strip() for h in allow.split(",") if h.strip()}
        if urlparse(url).hostname not in hosts:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "url_not_allowlisted"}
            )


@router.put("/{workspace_id}", status_code=status.HTTP_200_OK)
async def sync_publish_settings(
    workspace_id: str,
    body: PublishSettingsSyncRequest,
    request: Request,
    _: Annotated[None, Depends(require_service_auth)],
) -> PublishSettingsSyncResponse:
    """Set (or clear) a workspace's publish destination. Service-authed only.

    Enabling requires a complete, valid destination (https url + PUBLISH_*-namespaced
    secret_ref). Disabling (``enabled=false``) clears it — the workspace then does not
    publish. Upsert runs under ``workspace_scope`` so RLS ties the row to this workspace.
    """
    if not _UUID_RE.match(workspace_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "workspace_id must be a UUID")

    if body.enabled:
        if not body.url or not body.secret_ref:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "url_and_secret_ref_required_when_enabled"},
            )
        _validate_url(body.url)
        if not _SECRET_REF_RE.match(body.secret_ref):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "secret_ref_must_be_PUBLISH_namespaced"},
            )

    pool: Any = request.app.state.pool
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            await conn.execute(
                """
                INSERT INTO workspace_publish_settings(workspace_id, enabled, url, secret_ref,
                                                       schedule_enabled,
                                                       schedule_max_horizon_days, updated_at)
                VALUES($1::uuid, $2, $3, $4, COALESCE($5, false), COALESCE($6, 90), now())
                ON CONFLICT (workspace_id) DO UPDATE
                   SET enabled                   = EXCLUDED.enabled,
                       url                       = EXCLUDED.url,
                       secret_ref                = EXCLUDED.secret_ref,
                       -- $5/$6 NULL (the field omitted from the request body — the shape any
                       -- caller written before V020 sends) means "leave it alone": fall back to
                       -- the row's CURRENT value, not a fresh overwrite. A caller that sends an
                       -- explicit true/false or a specific horizon still sets it, same as before.
                       schedule_enabled          = COALESCE(
                                                        $5, workspace_publish_settings.schedule_enabled
                                                    ),
                       schedule_max_horizon_days = COALESCE(
                                                        $6,
                                                        workspace_publish_settings.schedule_max_horizon_days
                                                    ),
                       updated_at                = now()
                """,
                workspace_id,
                body.enabled,
                body.url,
                body.secret_ref,
                body.schedule_enabled,
                body.schedule_max_horizon_days,
            )
    except asyncpg.ForeignKeyViolationError:
        return PublishSettingsSyncResponse(applied=False, outcome="unknown")
    return PublishSettingsSyncResponse(applied=True, outcome="set" if body.enabled else "cleared")

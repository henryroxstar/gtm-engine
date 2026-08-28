"""Backend Gate-2 dispatch (Track A A10).

The backend's half of the publish gate: when an operator approves a run's
publish-gated node, the *exact approved bytes* are handed to the same shared
helper the Telegram cockpit uses (``agent.publish_dispatch``), which performs the
approval-binding check, the durable idempotency read, the publisher call, and the
history write. Nothing here decides *where* the post goes — the destination is
pinned inside :class:`agent.publish.LinkedInPublisher` from server-side settings
and is not representable in anything the brain (or the client) produces.

Invariants this module preserves:
  - ``autopublish: false`` — a dispatch happens only on an explicit approve/edit
    decision for a node that DECLARES ``external_effect`` (A10 metadata), never
    because a node happens to be named "publish".
  - ``dry_run`` never dispatches (enforced twice: here, and in the shared helper).
  - reject / cancel / gate timeout never dispatch — they never reach this call.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

# A5: a workspace's publish secret is referenced by an env-var NAME stored in the DB
# (never the value). Namespaced so a compromised/mistaken service write can only point
# at a publish-designated secret, never an arbitrary one (e.g. BACKEND_JWT_SECRET).
_SECRET_REF_RE = re.compile(r"^PUBLISH_[A-Z0-9_]+$")


async def _resolve_workspace_publish_settings(pool: Any, workspace_id: str) -> Any | None:
    """Per-workspace publish settings (A5), or None ⇒ do NOT publish.

    Fail-closed by construction: a workspace with no row, a disabled row, a missing
    url/secret_ref, a non-namespaced secret_ref, or an unset secret env var all yield
    None — the dispatch then leaves the run alone. There is deliberately NO fallback to
    a process-global/operator destination (that fallback WAS the A5 leak). The single-
    tenant VPS/cockpit path keeps env-based settings via agent.publish_dispatch, untouched.

    V020: also carries the workspace's OWN scheduling opt-in (schedule_enabled,
    schedule_max_horizon_days) — mirrors HERMES_SCHEDULE_ENABLED, but per workspace
    since one backend process serves every tenant. Missing columns (pre-migration)
    default via the row's own DEFAULT, never crash the resolver.
    """
    from agent.publish import PublishSettings

    from .database import workspace_scope

    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT enabled, url, secret_ref, schedule_enabled, schedule_max_horizon_days "
            "FROM workspace_publish_settings WHERE workspace_id = $1::uuid",
            workspace_id,
        )
    if row is None or not row["enabled"]:
        return None
    url = (row["url"] or "").strip() or None
    secret_ref = (row["secret_ref"] or "").strip()
    if not url or not _SECRET_REF_RE.match(secret_ref):
        return None  # half-configured destination is inert, never a partial publish
    secret = os.getenv(secret_ref) or None
    if not secret:
        return None
    # .get() with defaults (not row["..."]) so a row/mock predating the V020 columns
    # (or a test double that only sets the fields it cares about) degrades to the
    # closed-by-default posture rather than raising — real asyncpg.Record and plain
    # dicts both support .get().
    return PublishSettings(
        url=url,
        secret=secret,
        enabled=True,
        schedule_enabled=bool(row.get("schedule_enabled", False)),
        max_horizon_days=row.get("schedule_max_horizon_days") or 90,
    )


def publish_gated_node(graph: Any, node_id: str) -> bool:
    """True if ``node_id`` declares an external effect in this run's engine graph.

    Drives Gate 2 off DECLARED metadata (A10) rather than the node's name, so a
    pack that calls its dispatch node something else still gates correctly and a
    pack node merely *named* "publish" without the declaration never dispatches.
    """
    for node in getattr(graph, "nodes", ()):
        if node.id == node_id:
            return bool(getattr(node, "external_effect", None))
    return False


async def dispatch_backend_publish(
    cfg: Any,
    profile: str,
    *,
    pool: Any,
    workspace_id: str,
    content: str,
    dry_run: bool = False,
) -> Any:
    """Publish the approved bytes for a backend run. Never raises.

    A5: the destination is resolved PER WORKSPACE (``workspace_publish_settings``),
    never from a process-global env destination — so one tenant's approval can never
    publish to another tenant's (or the operator's) account. Returns the shared
    helper's :class:`~agent.publish_dispatch.DispatchOutcome`, or None when this
    workspace has no enabled destination (unconfigured ⇒ leave the run's status alone,
    fail-closed — no shared-account fallback).

    ``content`` is parsed the same way the cockpit parses a skill's turn
    (:func:`agent.publish.parse_publish_block`) rather than posted verbatim: a
    client's approve flow may hand back the model's full turn text — including the
    ``⟦GATE:publish⟧``/``⟦POST⟧``/``⟦SCHEDULE⟧`` wrapper — as ``edited_content``, and
    posting that unparsed would put the literal sentinels in the live post. When no
    gate block is present, ``content`` is treated as already-clean post text (the
    plain-edit path) — unchanged from the historical behaviour, and
    ``validate_post`` (run inside ``LinkedInPublisher.publish``) still refuses any
    stray control sentinel that slips through outside a well-formed block.

    Scheduling parity (V020): a ``⟦SCHEDULE⟧`` in the parsed block is forwarded to
    the shared dispatch helper exactly as the cockpit forwards it — subject to this
    WORKSPACE's own ``schedule_enabled`` (resolved above), never a global switch.

    Disclosure parity (§6.2, Article 50): the backend has no separate staging step
    the way the cockpit does, so the ONLY enforcement point for a synthetic-identity
    post on this path is the shared ``dispatch_approved_publish`` helper's own
    check — the disclosure line is resolved from THIS workspace's own tenant-scoped
    profile tree (``workspace_profiles_root``), never the single-tenant VPS
    ``profiles/`` tree ``cfg.profiles_root`` points at (that would be a
    cross-tenant read of the wrong brand kit).
    """
    try:
        from agent.ledgers import Ledgers
        from agent.publish import LinkedInPublisher, candidate_disclosure_lines, parse_publish_block
        from agent.publish_dispatch import dispatch_approved_publish
        from gtm_core.publish_hash import approval_hash

        settings = await _resolve_workspace_publish_settings(pool, workspace_id)
        if settings is None:
            log.info(
                "backend publish: no enabled destination for workspace=%s — not publishing",
                workspace_id,
            )
            return None
        publisher = LinkedInPublisher(settings=settings)

        draft = parse_publish_block(content)
        post = draft.post if draft is not None else content
        media = draft.media_urls if draft is not None else ()
        scheduled_at = draft.scheduled_at if draft is not None else None
        identity_used = draft.identity_used if draft is not None else ()

        disclosure_lines: tuple[str, ...] = ()
        if identity_used:
            from gtm_core.paths import workspace_profiles_root

            profiles_root = workspace_profiles_root(workspace_id, cfg.repo_root)
            disclosure_lines = tuple(candidate_disclosure_lines(profiles_root, profile))

        # There is no separate staging step on the backend the way the cockpit has
        # one (preview shown, hash recorded, THEN a later button press re-verifies
        # it) — the operator's approve decision on POST /runs/{id}/gate is the one
        # and only authorization event, checked before this function is ever
        # called. So the hash is derived from the SAME parsed values it is compared
        # against; this call cannot itself detect tampering (there is nothing
        # earlier to tamper with) — it exists so the shared helper's shape stays
        # identical across both callers.
        return await dispatch_approved_publish(
            publisher,
            Ledgers(cfg, profile),
            post=post,
            media_urls=media,
            staged_hash=approval_hash(post, media, scheduled_at),
            dry_run=dry_run,
            scheduled_at=scheduled_at,
            identity_used=identity_used,
            disclosure_lines=disclosure_lines,
        )
    except Exception:  # noqa: BLE001 — a dispatch failure must not crash the run task
        log.exception("backend publish dispatch failed for profile=%s", profile)
        return None

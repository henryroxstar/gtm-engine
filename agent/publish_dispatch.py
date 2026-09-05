"""Shared operator-approved publish dispatch (Track A A10).

Extracted verbatim in behaviour from ``cockpit/gates.py:_do_publish`` so the
Telegram cockpit and the backend Gate-2 approval run the SAME sequence:

  1. **Approval binding** — recompute the content hash and compare it with the
     hash staged at approval time. A mismatch refuses the publish outright: an
     approval is bound to the exact bytes the operator saw.
  1b. **Disclosure (§6.2, Article 50)** — a post behind a synthetic likeness/voice
     must carry the profile's configured disclosure line, checked HERE so BOTH
     callers get it, not only the one that happens to also check it earlier. The
     cockpit still checks at staging too (fail fast, nicer UX) — this is the
     fail-closed gate that actually holds for every caller, since the backend has
     no separate staging step to hang its own copy off of.
  2. **Durable idempotency** — read the profile's ``history.jsonl`` published
     hashes as the publisher's ``is_published`` predicate, so "publish at most
     once" survives a restart (the publisher's in-memory set does not). A ledger
     read failure never blocks a human-approved publish.
  3. **Dispatch** — call :meth:`agent.publish.LinkedInPublisher.publish`. The
     destination is pinned server-side inside the publisher and is not
     representable in anything the brain produces.
  4. **Audit** — append a ``published``/``publish_failed`` history event, written
     by the component that actually published, never trusted to the model.

``dry_run`` never dispatches — it stops before (3) and reports ``dry_run``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent.permissions import publish_context
from agent.publish import validate_disclosure
from gtm_core.publish_hash import approval_hash, content_hash

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchOutcome:
    """What the dispatch did. ``result`` is the publisher's PublishResult, or None
    when we never reached the publisher (integrity failure / dry run)."""

    ok: bool
    status: (
        str  # published | scheduled | publish_failed | hash_mismatch | disclosure_missing | dry_run
    )
    result: Any = None

    def operator_line(self) -> str:
        if self.status == "hash_mismatch":
            return "⚠️ Draft integrity check failed — not published."
        if self.status == "disclosure_missing":
            return (
                "⚠️ Synthetic likeness/voice used but not disclosed — not published. Add the "
                "profile's disclosure line (BRAND.toml [disclosure].line) to the post and retry."
            )
        if self.status == "dry_run":
            return "🧪 Dry run — nothing was sent."
        return self.result.operator_line() if self.result is not None else "❌ Publish failed."


async def dispatch_approved_publish(
    publisher,
    ledgers,
    *,
    post: str,
    media_urls: tuple[str, ...] | list[str] = (),
    staged_hash: str,
    dry_run: bool = False,
    scheduled_at: str | None = None,
    identity_used: tuple[str, ...] = (),
    disclosure_lines: tuple[str, ...] | list[str] = (),
) -> DispatchOutcome:
    """Publish exactly the approved bytes, or refuse. Never raises.

    ``publisher`` is an :class:`agent.publish.LinkedInPublisher`; ``ledgers`` an
    :class:`agent.ledgers.Ledgers` bound to the publishing profile. ``identity_used``
    and ``disclosure_lines`` default to empty, so a caller that never resolved them
    (there was none until this was added) is byte-identical to before: an empty
    ``identity_used`` always clears :func:`agent.publish.validate_disclosure`.
    """
    media = tuple(media_urls)

    # (1) Approval binding — the staged hash must match the staged content AND the
    #     staged send time. approval_hash falls back to the plain content hash when
    #     there is no schedule, so unscheduled callers are byte-identical to before;
    #     with one, a draft re-staged for a different hour no longer matches an
    #     approval the operator gave for the original slot.
    if approval_hash(post, media, scheduled_at) != staged_hash:
        log.warning(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "Publish hash mismatch — refusing dispatch."
        )
        return DispatchOutcome(ok=False, status="hash_mismatch")

    # (1b) Disclosure — checked here, not only at the cockpit's staging step, so a
    #      caller with no staging phase (the backend) still gets a fail-closed gate.
    if validate_disclosure(post, identity_used, disclosure_lines):
        log.warning("Publish refused — synthetic identity used without disclosure.")
        return DispatchOutcome(ok=False, status="disclosure_missing")

    # A dry run stops here: it must be structurally impossible for it to dispatch.
    if dry_run:
        return DispatchOutcome(ok=False, status="dry_run")

    # (2) Durable idempotency across restarts.
    try:
        published = ledgers.published_content_hashes()
        is_published = published.__contains__
    except Exception:  # noqa: BLE001 — never let a ledger read block an approved publish
        is_published = None

    # (3) Dispatch — destination pinned inside the publisher. The publish-context flag
    #     permits Reap publish/schedule verbs only during this approved dispatch window.
    with publish_context():
        result = await publisher.publish(
            post, media, is_published=is_published, scheduled_at=scheduled_at
        )

    # Computed once, used for both the audit event AND the returned outcome's status —
    # they must never disagree (a stale outcome.status previously always said
    # "published" for a scheduled dispatch, even though the ledger correctly said
    # "scheduled").
    if not result.ok:
        event = "publish_failed"
    elif result.status == "scheduled":
        # NOT "published" — it is booked, not live. Reporting a scheduled post as
        # published would make the outcomes sync look for metrics on something
        # that has not gone out yet. `published_content_hashes` counts this event
        # too, so idempotency still holds across a restart.
        event = "scheduled"
    else:
        event = "published"

    # (4) Audit, written by the component that published.
    try:
        ledgers.append_history(
            {
                "event": event,
                "platform": "linkedin",
                "skill": "content-publish",
                "status": result.status,
                "post_id": result.post_id,
                # The IDEMPOTENCY key — content only. This is the field
                # `published_content_hashes` reads back and compares against the
                # publisher's key, so it must be the content hash even when the
                # approval was bound to a content+time hash. Recording `staged_hash`
                # here would silently break dedupe for every scheduled post.
                "content_sha256": content_hash(post, media),
                # The APPROVAL binding — content+time when scheduled. Audit only.
                "approval_sha256": staged_hash,
                # getattr, not attribute access: `publisher` is duck-typed at the
                # backend boundary, and the whole append is inside a try/except that
                # swallows — so an older result object missing this OPTIONAL field
                # would silently drop the entire audit record, not just the field.
                "scheduled_at": getattr(result, "scheduled_at", None),
                "chars": len(post),
                "media_count": len(media),
            }
        )
    except Exception:  # noqa: BLE001 — a ledger write must never break the reply
        log.exception("Failed to write publish history")

    return DispatchOutcome(ok=result.ok, status=event, result=result)

"""Push notification abstraction for gate approval events.

When a pipeline run hits a gate (⟦GATE:plan⟧ or ⟦GATE:publish⟧), the mobile
client needs to know immediately so the operator can approve from their phone.

This module fetches all push tokens for the workspace from the DB and dispatches
a notification. The transport is selected by ``PUSH_PROVIDER``; unset ⇒ the call
is a no-op and the client falls back to polling GET /v1/runs/{id}.

**FCM HTTP v1** (Track A A6, 2026-08-09). The previous implementation targeted
FCM's *legacy* endpoint (`POST /fcm/send` with `Authorization: key=…`), which
Google removed in mid-2024 — it could never have delivered a notification. The
current path is `POST /v1/projects/{project}/messages:send`, authorized by a
short-lived OAuth2 access token minted from a service-account JSON: we sign a
JWT (RS256, PyJWT — no new dependency) and exchange it at the account's token
URI. Tokens are cached until shortly before expiry.

**Contentless by design.** The payload carries the run id and gate kind — never
the gate's content. The device fetches the actual bytes over the authenticated
API, so a notification intercepted at the OS/provider layer leaks no customer
content (the same reasoning as the publish gate's server-pinned destination).

Transport config (all from Doppler / environment):
  PUSH_PROVIDER                    = "fcm" | unset (no-op) — "apns" is refused at
                                      registration (backend/routers/push_tokens.py), not here
  PUSH_FCM_SERVICE_ACCOUNT_JSON    = the service-account JSON (raw, or a path to it)
  PUSH_TITLE                       = notification title shown in the system tray (ST-03);
                                      defaults to "GTM — action needed"

**Sent on two occasions** (:func:`send_gate_push`, :func:`send_run_done_push`): a run
pausing at a gate, and a run reaching a terminal 'ok' or 'failed' status — 'failed'
covers every RunErrorCode, gate_timeout included, since a client that only opens the
app when pushed would otherwise never learn a run it approved a gate on then died.
Never rejected or canceled: those are the operator's own action, not news to them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

from .database import workspace_scope

log = logging.getLogger(__name__)

# nosec B105 — a public OAuth2 scope identifier, not a credential (the credential is
# the Doppler-injected service-account JSON).
_TOKEN_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"  # nosec B105
_TOKEN_TTL_S = 3600
_TOKEN_REFRESH_MARGIN_S = 120  # mint a new token this long before expiry
_HTTP_OK = 200
_HTTP_CLIENT_ERROR = 400
_HTTP_SERVER_ERROR = 500

#: ST-13: exactly one retry, after this backoff, on a 5xx or a transport exception
#: (timeout, connection reset) — common and transient. A 4xx never retries: the caller
#: already knows how to read it (dead token vs. hard failure). Module-level so tests can
#: zero it out without a real sleep.
_RETRY_BACKOFF_S = 0.2

# Transport: (method, url, headers, json_body|data) -> (status_code, parsed_body).
# Injectable so tests never touch the network (gtm_core/calendly_poll.py house style).
Transport = Callable[..., Awaitable[tuple[int, Any]]]


def _provider() -> str:
    """Read at call time (not import time) so tests and Doppler reloads take effect."""
    return os.getenv("PUSH_PROVIDER", "").lower()


async def _httpx_transport(
    method: str, url: str, *, headers: dict, json_body: dict | None = None, data: dict | None = None
) -> tuple[int, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        resp = await client.request(method, url, headers=headers, json=json_body, data=data)
        try:
            body: Any = resp.json()
        except Exception:  # noqa: BLE001
            body = {"raw": resp.text[:500]}
        return resp.status_code, body


# ── service account + OAuth2 access token ────────────────────────────────────


def _service_account() -> dict | None:
    """Parse ``PUSH_FCM_SERVICE_ACCOUNT_JSON`` (raw JSON or a path to a JSON file)."""
    raw = os.getenv("PUSH_FCM_SERVICE_ACCOUNT_JSON")
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if not text.startswith("{"):
        from pathlib import Path

        path = Path(text)
        if not path.is_file():
            log.error("push: PUSH_FCM_SERVICE_ACCOUNT_JSON is neither JSON nor a readable file")
            return None
        text = path.read_text(encoding="utf-8")
    try:
        account = json.loads(text)
    except json.JSONDecodeError:
        log.error("push: PUSH_FCM_SERVICE_ACCOUNT_JSON is not valid JSON")
        return None
    missing = [k for k in ("client_email", "private_key", "project_id") if not account.get(k)]
    if missing:
        log.error("push: service account JSON is missing %s", ", ".join(missing))
        return None
    return account


_token_cache: dict[str, tuple[str, float]] = {}  # client_email -> (access_token, expires_at)


def reset_token_cache() -> None:
    """Test hook: forget every cached OAuth2 access token."""
    _token_cache.clear()


async def _access_token(account: dict, *, transport: Transport | None = None) -> str | None:
    """Mint (or reuse) a short-lived OAuth2 access token for the messaging scope.

    The assertion is a JWT signed with the service account's private key — the key
    never leaves this process and is never logged. Failures return None; the caller
    treats that as "cannot dispatch" rather than raising into the gate path.
    """
    email = account["client_email"]
    cached = _token_cache.get(email)
    now = time.time()
    if cached and cached[1] - _TOKEN_REFRESH_MARGIN_S > now:
        return cached[0]

    import jwt

    token_uri = account.get("token_uri") or "https://oauth2.googleapis.com/token"
    issued = int(now)
    try:
        assertion = jwt.encode(
            {
                "iss": email,
                "scope": _TOKEN_SCOPE,
                "aud": token_uri,
                "iat": issued,
                "exp": issued + _TOKEN_TTL_S,
            },
            account["private_key"],
            algorithm="RS256",
        )
    except Exception:  # noqa: BLE001 — a malformed key must not raise into the gate path
        log.exception("push: failed to sign the FCM token assertion")
        return None

    result = await _post_retrying(
        transport or _httpx_transport,
        token_uri,
        "the FCM token exchange",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    if result is None:
        return None
    status_code, body = result
    if status_code != _HTTP_OK or not isinstance(body, dict) or not body.get("access_token"):
        # Never log the body: a token response carries a bearer credential.
        log.error(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "push: FCM token exchange failed (status %s)", status_code
        )
        return None

    token = body["access_token"]
    expires_in = int(body.get("expires_in") or _TOKEN_TTL_S)
    _token_cache[email] = (token, now + expires_in)
    return token


# ── dispatch ─────────────────────────────────────────────────────────────────


async def send_gate_push(
    pool: asyncpg.Pool,
    workspace_id: str,
    run_id: str,
    gate_type: str,
    *,
    node_id: str | None = None,
    transport: Transport | None = None,
) -> int:
    """Notify all registered devices for a workspace that a gate needs approval.

    Returns the number of tokens the notification reached (0 if none are
    registered, the provider is unset, or the transport is unavailable).
    Safe to call and ignore — a push failure never blocks the gate.
    """
    payload = _build_payload(run_id, gate_type, node_id)
    return await _dispatch(pool, workspace_id, run_id, payload, transport=transport)


async def send_run_done_push(
    pool: asyncpg.Pool,
    workspace_id: str,
    run_id: str,
    status: str,
    *,
    transport: Transport | None = None,
) -> int:
    """ST-02: notify every registered device that a run finished — 'ok' or 'failed'
    only, 'failed' covering every RunErrorCode including gate_timeout. Never rejected
    or canceled: those are the operator's own action, not news to them. Same
    contentless, best-effort contract as :func:`send_gate_push` — a push failure never
    fails the run.
    """
    payload = _build_done_payload(run_id, status)
    return await _dispatch(pool, workspace_id, run_id, payload, transport=transport)


async def _dispatch(
    pool: asyncpg.Pool,
    workspace_id: str,
    run_id: str,
    payload: dict,
    *,
    transport: Transport | None = None,
) -> int:
    """Shared FCM v1 delivery: fetch the workspace's tokens, mint an access token, send
    to every registered ``fcm`` device (ST-13: one bounded retry per token on a 5xx or a
    transport exception), prune dead tokens. Returns the number of tokens reached."""
    try:
        tokens = await _fetch_tokens(pool, workspace_id)
    except Exception:
        log.exception(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "push: failed to fetch tokens for workspace %s", workspace_id
        )
        return 0

    if not tokens:
        return 0

    provider = _provider()

    if not provider:
        log.info(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "push: PUSH_PROVIDER not set — would notify %d token(s) for run %s",
            len(tokens),
            run_id,
        )
        return 0

    if provider != "fcm":
        log.error("push: provider %r is not implemented (only 'fcm')", provider)
        return 0

    account = _service_account()
    if account is None:
        log.error("push: PUSH_PROVIDER=fcm but no usable service account — not dispatching")
        return 0
    access_token = await _access_token(account, transport=transport)
    if access_token is None:
        return 0

    url = f"https://fcm.googleapis.com/v1/projects/{account['project_id']}/messages:send"
    dispatched = 0
    dead: list[str] = []
    for token, platform in tokens:
        if platform != "fcm":
            log.debug("push: token platform %r not handled by provider 'fcm'", platform)
            continue
        result = await _post_retrying(
            transport or _httpx_transport,
            url,
            f"dispatch to a {platform} token for run {run_id}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json_body=_fcm_v1_message(token, payload),
        )
        if result is None:
            continue  # neither counted nor pruned
        status_code, body = result
        if status_code == _HTTP_OK:
            dispatched += 1
        elif _is_dead_token(status_code, body):
            dead.append(token)
        else:
            log.error("push: FCM rejected a send (status %s) for run %s", status_code, run_id)

    if dead:
        await _delete_tokens(pool, workspace_id, dead)

    log.info(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        "push: dispatched to %d/%d tokens for run %s (%d dead token(s) pruned)",
        dispatched,
        len(tokens),
        run_id,
        len(dead),
    )
    return dispatched


async def _post_retrying(
    send: Transport, url: str, what: str, **kwargs: Any
) -> tuple[int, Any] | None:
    """ST-13's bounded retry, for every POST on the FCM path (the token exchange and each
    send): a 5xx or a transport exception (timeout, connection reset) retries exactly
    once, after :data:`_RETRY_BACKOFF_S`; a 4xx never retries. None when both attempts
    raised — logged here, never raised."""
    for attempt in range(2):
        try:
            status_code, body = await send("POST", url, **kwargs)
        except Exception:  # noqa: BLE001 — a push failure never propagates
            if attempt == 0:
                await asyncio.sleep(_RETRY_BACKOFF_S)
                continue
            log.exception(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                "push: %s failed (after retry)", what
            )
            return None
        if status_code >= _HTTP_SERVER_ERROR and attempt == 0:
            await asyncio.sleep(_RETRY_BACKOFF_S)
            continue
        return status_code, body
    return None  # unreachable — the loop always returns or continues once


def _fcm_v1_message(token: str, payload: dict) -> dict:
    """The HTTP v1 envelope. `data` values must be strings (v1 rejects non-strings)."""
    return {
        "message": {
            "token": token,
            "notification": {"title": payload["title"], "body": payload["body"]},
            "data": {k: str(v) for k, v in payload["data"].items()},
            "android": {"priority": "high"},
            "apns": {"headers": {"apns-priority": "10"}},
        }
    }


def _is_dead_token(status_code: int, body: Any) -> bool:
    """A permanently invalid registration token — prune it rather than retry forever.

    FCM v1 signals this as 404 UNREGISTERED, or 400 INVALID_ARGUMENT on a malformed
    token. A 5xx is transient (the device may be fine) and must never prune.
    """
    if status_code >= _HTTP_SERVER_ERROR or status_code < _HTTP_CLIENT_ERROR:
        return False
    detail = json.dumps(body) if not isinstance(body, str) else body
    return "UNREGISTERED" in detail or "INVALID_ARGUMENT" in detail


async def _fetch_tokens(pool: asyncpg.Pool, workspace_id: str) -> list[tuple[str, str]]:
    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(
            "SELECT token, platform FROM push_tokens WHERE workspace_id = $1::uuid",
            workspace_id,
        )
    return [(r["token"], r["platform"]) for r in rows]


async def _delete_tokens(pool: asyncpg.Pool, workspace_id: str, tokens: list[str]) -> None:
    """Prune dead tokens. Best-effort: cleanup must never fail the gate path."""
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            await conn.execute(
                "DELETE FROM push_tokens WHERE workspace_id = $1::uuid AND token = ANY($2::text[])",
                workspace_id,
                tokens,
            )
    except Exception:  # noqa: BLE001
        log.exception(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "push: failed to prune %d dead token(s) for workspace %s", len(tokens), workspace_id
        )


#: ST-03: the FCM `notification` block is always present (never data-only), so Android
#: renders this verbatim in the system tray and the app cannot rewrite it. An operator
#: env var lets a deployment show its own product name instead of the gtm-engine default.
_DEFAULT_TITLE = "GTM — action needed"


def _title() -> str:
    """Read at call time (not import time) so tests and Doppler reloads take effect."""
    return os.getenv("PUSH_TITLE") or _DEFAULT_TITLE


#: Notification body per gate kind — the same kinds the stream's ``awaiting_approval.gate``
#: carries (``schemas/run-event.schema.json``). A kind outside this map reads as ``review``.
_GATE_LABELS = {
    "plan": "Plan ready for review",
    "publish": "Post ready to approve",
    "email_enroll": "Contacts ready to load into your sender",
    "review": "Ready for your review",
}

#: ST-02: notification body per terminal status this fires for — 'ok' and 'failed' only.
#: send_run_done_push's docstring says why rejected/canceled are excluded, and why a
#: gate_timeout failure is included.
_DONE_LABELS = {
    "ok": "Your run is complete",
    "failed": "Your run failed",
}


def _gate_kind(gate_type: str) -> str:
    """The wire gate kind. Accepts the kind itself (what the run lifecycle passes) or a raw
    ``⟦GATE:plan⟧``/``⟦GATE:publish⟧`` sentinel, so a client reads one vocabulary everywhere."""
    if "GATE:" in gate_type:  # a raw sentinel, not a kind
        gate_type = "publish" if "publish" in gate_type else "plan"
    return gate_type if gate_type in _GATE_LABELS else "review"


def _build_payload(run_id: str, gate_type: str, node_id: str | None = None) -> dict:
    """Contentless: the run id, gate kind and — for a pack run — the gated node's id. The
    device fetches the bytes over the authenticated API — a notification never carries gate
    content. ``node_id`` names which step is waiting, so a pack that pauses more than once
    (plan, then pre-render) is distinguishable without a fetch."""
    kind = _gate_kind(gate_type)
    data = {"run_id": run_id, "gate": kind}
    if node_id:
        data["node_id"] = node_id
    return {"title": _title(), "body": _GATE_LABELS[kind], "data": data}


def _build_done_payload(run_id: str, status: str) -> dict:
    """Contentless, same as :func:`_build_payload`: the run id and its terminal status.
    An unrecognised status (defensive — callers only ever pass 'ok'/'failed') reads as a
    generic 'finished' rather than raising into the run's completion path."""
    body = _DONE_LABELS.get(status, "Your run finished")
    return {"title": _title(), "body": body, "data": {"run_id": run_id, "status": status}}

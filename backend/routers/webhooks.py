"""Inbound Webhook Ingestion Endpoint (PRD 2026-09-18, Issue #273).

Accepts incoming webhook notifications from external sequencers (Saleshandy, Apollo, etc.)
and services, verifies cryptographic signatures or bearer tokens, deduplicates deliveries,
resolves the tenant workspace and active profile, and enqueues a run of the inbound pack (inbound-reply).

Invariants:
- Untrusted content is treated strictly as data (§R5).
- Webhook routes are rate-limited via slowapi (backend/ratelimit.py).
- Payload size is capped at 256 KB to protect against DoS.
- Replays within 72 hours return 200/202 {"status": "duplicate"} without creating runs.
- Tenant isolation: Deduplication and credentials are strictly scoped by workspace_id.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from ..callers.principal import Principal
from ..database import workspace_scope
from ..deps import fetch_entitlement
from ..ratelimit import limiter
from ..schemas import ERROR_RESPONSES, RunRequest, WebhookResponse
from ..services.runs.admission import insert_run_row
from ..vault import decrypt, get_kek

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"], responses=ERROR_RESPONSES)

_MAX_PAYLOAD_BYTES = 256 * 1024  # 256 KB
_DEDUP_WINDOW_SECONDS = 72 * 3600  # 72 hours

# Fast in-memory deduplication cache: (workspace_id, provider, event_id) -> timestamp
_recent_events: dict[tuple[str, str, str], float] = {}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _purge_old_cache(now: float) -> None:
    """Evict in-memory entries older than the dedup window."""
    cutoff = now - _DEDUP_WINDOW_SECONDS
    expired = [k for k, t in _recent_events.items() if t < cutoff]
    for k in expired:
        _recent_events.pop(k, None)


def _extract_candidate_signature(request: Request) -> str | None:
    """Extract HMAC signature from supported provider headers."""
    headers = (
        "x-saleshandy-signature",
        "x-webhook-signature",
        "x-client-signature",
        "x-signature",
        "x-hub-signature-256",
    )
    for h in headers:
        val = request.headers.get(h)
        if val:
            sig = val.strip()
            if sig.lower().startswith("sha256="):
                sig = sig[7:].strip()
            return sig
    return None


async def _resolve_provider_secrets(pool: Any, workspace_id: str, provider: str) -> list[str]:
    """Resolve HMAC secrets for provider: workspace-scoped BYOK first, then Doppler env."""
    secrets: list[str] = []

    # 1. Check workspace encrypted_credentials if vault/DB is available
    kek = get_kek()
    if kek and pool:
        try:
            async with workspace_scope(pool, workspace_id) as conn:
                rows = await conn.fetch(
                    "SELECT * FROM encrypted_credentials WHERE workspace_id = $1::uuid AND provider = $2",
                    workspace_id,
                    provider,
                )
                for row in rows:
                    try:
                        pt = decrypt(dict(row), kek)
                        data = json.loads(pt)
                        sec = data.get("webhook_secret") or data.get("api_key")
                        if sec and sec not in secrets:
                            secrets.append(sec)
                    except Exception:  # noqa: BLE001 # nosec B110
                        pass
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed checking vault records for workspace %s: %s", workspace_id, exc)

    # 2. Doppler / server env fallback: e.g. SALESHANDY_WEBHOOK_SECRET
    env_key = f"{provider.upper()}_WEBHOOK_SECRET"
    env_secret = os.getenv(env_key)
    if env_secret and env_secret not in secrets:
        secrets.append(env_secret)

    return secrets


def _verify_webhook_auth(request: Request, body_bytes: bytes, secrets: list[str]) -> bool:
    """Verify request via HMAC-SHA256 signature or shared bearer/secret token."""
    if not secrets:
        return False

    # Check HMAC signature headers
    candidate_sig = _extract_candidate_signature(request)
    if candidate_sig:
        for secret in secrets:
            sec_bytes = secret.encode("utf-8")
            expected_hex = hmac.new(sec_bytes, body_bytes, hashlib.sha256).hexdigest()
            if hmac.compare_digest(candidate_sig.lower(), expected_hex.lower()):
                return True
            expected_b64 = base64.b64encode(
                hmac.new(sec_bytes, body_bytes, hashlib.sha256).digest()
            ).decode("utf-8")
            if hmac.compare_digest(candidate_sig, expected_b64):
                return True

    # Check Bearer token auth (PRD §3.2)
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
        for secret in secrets:
            if hmac.compare_digest(bearer_token, secret):
                return True

    # Check X-Webhook-Secret / X-Provider-Secret
    secret_hdr = request.headers.get("x-webhook-secret") or request.headers.get(
        "x-saleshandy-secret"
    )
    if secret_hdr:
        for secret in secrets:
            if hmac.compare_digest(secret_hdr.strip(), secret):
                return True

    return False


def _check_payload_size(request: Request, body_bytes: bytes) -> None:
    """Enforce payload size cap (max 256 KB)."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_PAYLOAD_BYTES:
                raise HTTPException(413, detail="Payload exceeds 256 KB limit")
        except ValueError:
            pass

    if len(body_bytes) > _MAX_PAYLOAD_BYTES:
        raise HTTPException(413, detail="Payload exceeds 256 KB limit")


def _parse_and_extract_payload(body_bytes: bytes) -> tuple[dict, str]:
    """Parse JSON body and extract event_id."""
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Payload must be a JSON object")
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "malformed_json", "message": f"Malformed JSON: {exc}"},
        ) from exc

    raw_event_id = (
        payload.get("event_id")
        or payload.get("id")
        or (isinstance(payload.get("data"), dict) and payload["data"].get("id"))
        or payload.get("eventId")
    )
    if not raw_event_id:
        conv_id = payload.get("conversationId") or (
            isinstance(payload.get("data"), dict) and payload["data"].get("conversationId")
        )
        ts = (
            payload.get("replyReceiveAt")
            or payload.get("receivedAt")
            or payload.get("timestamp")
            or (isinstance(payload.get("data"), dict) and payload["data"].get("replyReceiveAt"))
        )
        if conv_id and ts:
            raw_event_id = f"{conv_id}_{ts}"
        elif conv_id:
            body_digest = hashlib.sha256(body_bytes).hexdigest()[:16]
            raw_event_id = f"{conv_id}_{body_digest}"
        else:
            raw_event_id = hashlib.sha256(body_bytes).hexdigest()[:32]

    return payload, str(raw_event_id)


async def _check_dedup(
    pool: Any, workspace_id: str, provider: str, event_id: str, now: float
) -> str | None:
    """Check replay deduplication in-memory and DB. Returns existing run_id if duplicate."""
    cache_key = (workspace_id, provider, event_id)
    if cache_key in _recent_events:
        return "duplicate"

    if pool:
        try:
            async with workspace_scope(pool, workspace_id) as conn:
                existing = await conn.fetchrow(
                    """
                    SELECT run_id, received_at FROM webhook_events
                    WHERE workspace_id = $1::uuid AND provider = $2 AND event_id = $3
                      AND run_id IS NOT NULL
                    """,
                    workspace_id,
                    provider,
                    event_id,
                )
                if existing is not None:
                    _recent_events[cache_key] = now
                    return str(existing["run_id"]) if existing["run_id"] else "duplicate"
        except Exception as exc:  # noqa: BLE001
            log.warning("Error checking webhook_events deduplication in DB: %s", exc)

    return None


async def _resolve_workspace_profile(pool: Any, workspace_id: str) -> str:
    """Verify workspace exists and resolve active profile name."""
    if not pool:
        return "default"

    async with workspace_scope(pool, workspace_id) as conn:
        ws_exists = await conn.fetchval(
            "SELECT 1 FROM workspaces WHERE id = $1::uuid",
            workspace_id,
        )
        if not ws_exists:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "workspace_not_found",
                    "message": f"Workspace {workspace_id} not found",
                },
            )

        profile_row = await conn.fetchrow(
            """
            SELECT profile_name FROM profiles
            WHERE workspace_id = $1::uuid
            ORDER BY is_default DESC, profile_name
            LIMIT 1
            """,
            workspace_id,
        )
        if profile_row and profile_row["profile_name"]:
            return profile_row["profile_name"]

    return "default"


def _build_run_request(
    payload: dict, provider: str, event_id: str, profile_name: str
) -> RunRequest:
    """Construct sanitized RunRequest for the inbound pack run."""
    data_dict = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    from_email = str(
        payload.get("from_email")
        or payload.get("from")
        or payload.get("sender")
        or data_dict.get("from_email")
        or (isinstance(payload.get("prospect"), dict) and payload["prospect"].get("email"))
        or (isinstance(data_dict.get("prospect"), dict) and data_dict["prospect"].get("email"))
        or ""
    )
    subject = str(payload.get("subject") or data_dict.get("subject") or "")
    raw_body = str(
        payload.get("body")
        or payload.get("text")
        or payload.get("snippet")
        or data_dict.get("body")
        or payload.get("receivedReplyMessage")
        or data_dict.get("receivedReplyMessage")
        or ""
    )
    category = str(
        payload.get("reply_category")
        or payload.get("category")
        or payload.get("intent")
        or "inbound_reply"
    )

    safe_event_id = re.sub(r"[^A-Za-z0-9._:-]", "_", event_id)[:100]
    external_ref = f"{provider}:{safe_event_id}"[:128]
    client_request_id = f"webhook:{provider}:{safe_event_id}"[:128]

    thread_id = str(
        payload.get("conversationId")
        or payload.get("thread_id")
        or data_dict.get("conversationId")
        or data_dict.get("thread_id")
        or ""
    )

    inputs = {
        "reply_category": category,
        "from_email": from_email,
        "subject": subject,
        "body_snippet": raw_body[:500],
    }
    if thread_id:
        inputs["thread_id"] = thread_id

    return RunRequest(
        profile_name=profile_name,
        pack="inbound",
        variant="inbound-reply",
        client_request_id=client_request_id,
        external_ref=external_ref,
        inputs=inputs,
        context={"raw_payload": json.dumps(payload)},
    )


async def _enqueue_inbound_run(
    pool: Any,
    workspace_id: str,
    provider: str,
    event_id: str,
    profile_name: str,
    run_req: RunRequest,
) -> tuple[str, bool]:
    """Dispatch run admission and record run_id on webhook_events.

    Returns (effective_run_id, is_duplicate).
    """
    run_id = str(uuid.uuid4())
    effective_run_id = run_id
    is_dup = False

    if pool:
        entitlement = await fetch_entitlement(pool, workspace_id)
        principal = Principal(
            kind="service",
            subject=f"webhook:{provider}",
            workspace_id=workspace_id,
            entitlement=entitlement,
            verifier=f"webhook_{provider}",
        )
        effective_run_id, is_dup, _, _, _ = await insert_run_row(
            pool,
            workspace_id,
            run_id,
            profile_name=profile_name,
            agent_row=None,
            body=run_req,
            principal=principal,
        )
        try:
            async with workspace_scope(pool, workspace_id) as conn:
                await conn.execute(
                    """
                    INSERT INTO webhook_events (workspace_id, provider, event_id, run_id)
                    VALUES ($1::uuid, $2, $3, $4::uuid)
                    ON CONFLICT (workspace_id, provider, event_id)
                    DO UPDATE SET run_id = EXCLUDED.run_id, received_at = now()
                    """,
                    workspace_id,
                    provider,
                    event_id,
                    effective_run_id,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed recording webhook_events with run_id: %s", exc)

    return effective_run_id, is_dup


_REVENUECAT_CREDIT_PRODUCTS: dict[str, float] = {
    "credits_10_usd": 10.00,
    "credits_25_usd": 25.00,
    "credits_50_usd": 50.00,
}


def _map_revenuecat_product_credits(product_id: str, event: dict[str, Any]) -> float:
    """Map RevenueCat product_id or price to USD credit amount."""
    pid = (product_id or "").lower().strip()
    if pid in _REVENUECAT_CREDIT_PRODUCTS:
        return _REVENUECAT_CREDIT_PRODUCTS[pid]
    m = re.search(r"(?:credits|pro_monthly)_(\d+)_usd", pid)
    if m:
        return float(m.group(1))
    price = event.get("price_in_purchased_currency")
    if price is not None:
        try:
            return float(price)
        except (ValueError, TypeError):
            pass
    return 0.0


async def _resolve_revenuecat_secrets(pool: Any, workspace_id: str | None) -> list[str]:
    """Resolve webhook secrets for RevenueCat: Doppler env + workspace vault."""
    secrets: list[str] = []
    env_secret = os.getenv("REVENUECAT_WEBHOOK_SECRET")
    if env_secret:
        secrets.append(env_secret)
    if pool and workspace_id and _UUID_RE.match(workspace_id):
        ws_secrets = await _resolve_provider_secrets(pool, workspace_id, "revenuecat")
        for s in ws_secrets:
            if s and s not in secrets:
                secrets.append(s)
    return secrets


async def apply_wallet_top_up(
    pool: Any,
    workspace_id: str,
    amount_usd: float,
    provider: str,
    external_transaction_id: str,
    credits: float | None = None,
) -> bool:
    """Idempotently record top-up audit row and increment workspace_wallets.balance_credits and balance_usd.

    Returns True if transaction was applied, False if duplicate or invalid.
    """
    if not pool or amount_usd <= 0:
        return False

    credits_deposited = credits if credits is not None else (amount_usd * 1000.0)

    async with workspace_scope(pool, workspace_id) as conn:
        if external_transaction_id:
            existing = await conn.fetchrow(
                """
                SELECT id FROM wallet_transactions
                WHERE provider = $1 AND external_transaction_id = $2
                """,
                provider,
                external_transaction_id,
            )
            if existing is not None:
                return False

        tx_row = await conn.fetchrow(
            """
            INSERT INTO wallet_transactions (workspace_id, amount_usd, credits_deposited, provider, external_transaction_id)
            VALUES ($1::uuid, $2, $3, $4, $5)
            ON CONFLICT (provider, external_transaction_id) DO NOTHING
            RETURNING id
            """,
            workspace_id,
            amount_usd,
            credits_deposited,
            provider,
            external_transaction_id,
        )
        if tx_row is None:
            return False

        await conn.execute(
            """
            INSERT INTO workspace_wallets (workspace_id, balance_credits, balance_usd, updated_at)
            VALUES ($1::uuid, $2, $3, now())
            ON CONFLICT (workspace_id)
            DO UPDATE SET balance_credits = workspace_wallets.balance_credits + EXCLUDED.balance_credits,
                          balance_usd = workspace_wallets.balance_usd + EXCLUDED.balance_usd,
                          updated_at = now()
            """,
            workspace_id,
            credits_deposited,
            amount_usd,
        )
        return True


@router.post(
    "/revenuecat",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def handle_revenuecat_webhook(
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Ingest RevenueCat webhook, verifying secret and handling NON_RENEWING_PURCHASE top-ups."""
    body_bytes = await request.body()
    _check_payload_size(request, body_bytes)

    payload, _ = _parse_and_extract_payload(body_bytes)
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    ev_type = str(event.get("type") or payload.get("type") or "")

    raw_ws_id = str(event.get("app_user_id") or payload.get("app_user_id") or "")
    workspace_id = raw_ws_id if _UUID_RE.match(raw_ws_id) else None

    pool = getattr(request.app.state, "pool", None)
    secrets = await _resolve_revenuecat_secrets(pool, workspace_id)

    if not _verify_webhook_auth(request, body_bytes, secrets):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_signature", "message": "Invalid or missing webhook signature"},
        )

    if ev_type == "TEST":
        return {"status": "ok", "message": "Test webhook verified"}

    if ev_type not in ("NON_RENEWING_PURCHASE", "INITIAL_PURCHASE", "RENEWAL"):
        return {"status": "ignored", "reason": f"unhandled_event_type: {ev_type}"}

    if not workspace_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_workspace_id", "message": "app_user_id must be a valid UUID"},
        )

    product_id = str(event.get("product_id") or payload.get("product_id") or "")
    amount_usd = _map_revenuecat_product_credits(product_id, event)
    if amount_usd <= 0:
        return {"status": "ignored", "reason": f"unknown_or_free_product: {product_id}"}

    ext_tx_id = str(
        event.get("id")
        or event.get("transaction_id")
        or payload.get("id")
        or hashlib.sha256(body_bytes).hexdigest()[:32]
    )

    credits_to_deposit = amount_usd * 1000.0

    applied = await apply_wallet_top_up(
        pool=pool,
        workspace_id=workspace_id,
        amount_usd=amount_usd,
        provider="revenuecat_stripe",
        external_transaction_id=ext_tx_id,
        credits=credits_to_deposit,
    )

    if not applied:
        return {
            "status": "duplicate",
            "applied": False,
            "message": "Transaction already processed",
        }

    return {
        "status": "ok",
        "applied": True,
        "workspace_id": workspace_id,
        "amount_usd": amount_usd,
        "credits": credits_to_deposit,
    }


@router.post(
    "/{provider}/{workspace_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookResponse,
)
@limiter.limit("60/minute")
async def handle_webhook(
    provider: str,
    workspace_id: str,
    request: Request,
    response: Response,
) -> WebhookResponse:
    """Ingest external webhook, verify signature, deduplicate, and enqueue inbound run."""
    provider = provider.lower()
    body_bytes = await request.body()
    _check_payload_size(request, body_bytes)

    if not _UUID_RE.match(workspace_id):
        raise HTTPException(
            422,
            detail={"code": "invalid_workspace_id", "message": "workspace_id must be a valid UUID"},
        )

    pool = getattr(request.app.state, "pool", None)
    secrets = await _resolve_provider_secrets(pool, workspace_id, provider)

    if not _verify_webhook_auth(request, body_bytes, secrets):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_signature", "message": "Invalid or missing webhook signature"},
        )

    payload, event_id = _parse_and_extract_payload(body_bytes)

    if provider == "revenuecat":
        event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        ev_type = str(event.get("type") or payload.get("type") or "")
        if ev_type in ("NON_RENEWING_PURCHASE", "INITIAL_PURCHASE", "RENEWAL"):
            product_id = str(event.get("product_id") or payload.get("product_id") or "")
            amount_usd = _map_revenuecat_product_credits(product_id, event)
            ext_tx_id = str(event.get("id") or event.get("transaction_id") or event_id)
            credits_to_deposit = amount_usd * 1000.0
            applied = await apply_wallet_top_up(
                pool=pool,
                workspace_id=workspace_id,
                amount_usd=amount_usd,
                provider="revenuecat_stripe",
                external_transaction_id=ext_tx_id,
                credits=credits_to_deposit,
            )
            response.status_code = status.HTTP_200_OK
            if not applied:
                return WebhookResponse(status="duplicate", message="Transaction already processed")
            return WebhookResponse(status="ok", message=f"Credited ${amount_usd:.2f}")

    event_type = str(payload.get("event") or payload.get("type") or "").lower()
    if not any(k in event_type for k in ("reply", "replied")):
        response.status_code = status.HTTP_200_OK
        return WebhookResponse(status="ignored", message="Non-reply event ignored")

    category = str(payload.get("reply_category") or payload.get("category") or "")
    normalized_category = category.lower().replace(" ", "-").replace("_", "-")
    if normalized_category in ("out-of-office", "do-not-contact", "unsubscribed"):
        response.status_code = status.HTTP_200_OK
        return WebhookResponse(status="ignored", message="Ignored category")

    now = time.time()
    _purge_old_cache(now)

    dup_run = await _check_dedup(pool, workspace_id, provider, event_id, now)
    if dup_run is not None:
        response.status_code = status.HTTP_200_OK
        run_id_val = None if dup_run == "duplicate" else dup_run
        return WebhookResponse(
            run_id=run_id_val, status="duplicate", message="Event already processed"
        )

    profile_name = await _resolve_workspace_profile(pool, workspace_id)
    run_req = _build_run_request(payload, provider, event_id, profile_name)
    effective_run_id, is_duplicate = await _enqueue_inbound_run(
        pool, workspace_id, provider, event_id, profile_name, run_req
    )

    _recent_events[(workspace_id, provider, event_id)] = now

    if is_duplicate:
        response.status_code = status.HTTP_200_OK
        return WebhookResponse(
            run_id=effective_run_id, status="duplicate", message="Event already processed"
        )

    return WebhookResponse(run_id=effective_run_id, status="queued")

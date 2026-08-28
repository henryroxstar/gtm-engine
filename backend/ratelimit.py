"""Shared inbound rate limiter (H4).

A single ``slowapi`` limiter keyed by the real client IP. Behind the Cloudflare
Tunnel the immediate peer is the tunnel, so we prefer Cloudflare's trustworthy
``CF-Connecting-IP`` header, then a leading ``X-Forwarded-For`` hop, then the
socket peer. Per-endpoint limits are applied with ``@limiter.limit(...)`` on the
sensitive routes (auth, runs; onboard in Phase 4).

The limiter auto-disables under pytest — the mocked suites reuse one TestClient
peer IP and would otherwise trip a shared limit; a dedicated test re-enables it
(``limiter.enabled = True``) to prove enforcement. In real runtime it is on unless
``RATELIMIT_ENABLED`` is set falsy.

Storage: in-process ``memory://`` by default (fine for one uvicorn worker). Set
``RATELIMIT_STORAGE_URI`` (or ``REDIS_URL``) to a ``redis://…`` URI to share limit
counters across workers — REQUIRED before running more than one uvicorn worker,
else each worker keeps its own counter and the effective limit is N× too loose.
NOTE: the rate limiter is only one of the per-worker states; the run-gate and SSE
subscriber registries in ``backend/routers/runs.py`` are still in-process, so a
true multi-worker deploy also needs those moved to a shared backend — tracked in
the hardening PRD (item d).
"""

from __future__ import annotations

import hmac
import os
import sys

from slowapi import Limiter
from slowapi.util import get_remote_address

# When set, the client-supplied CF-Connecting-IP / X-Forwarded-For headers are
# trusted ONLY on requests that also carry the matching secret header — one a
# Cloudflare edge Transform Rule injects on real tunnel traffic and a direct-to-
# origin attacker (who bypasses CF) cannot reproduce. Unset ⇒ headers trusted as
# before (local dev / when CF is verifiably the only ingress). See deploy/.env.example.
_PROXY_SECRET = os.getenv("RATELIMIT_PROXY_SECRET") or None
_PROXY_SECRET_HEADER = "x-gtm-proxy-secret"


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def client_key(request) -> str:
    """Real client IP for rate-limit bucketing.

    Behind the Cloudflare Tunnel the socket peer is the tunnel, so the real client is
    in CF-Connecting-IP / X-Forwarded-For. Those headers are client-settable, so an
    attacker reaching the origin DIRECTLY could forge a unique key per request and
    evade every per-IP limit (e.g. unlimited login attempts). We therefore trust the
    proxy headers only when they are proven to come from our edge — via the shared
    secret header — and otherwise fall back to the UNSPOOFABLE socket peer.
    """
    if _PROXY_SECRET is None or hmac.compare_digest(
        request.headers.get(_PROXY_SECRET_HEADER, ""), _PROXY_SECRET
    ):
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return get_remote_address(request)


_ENABLED = "pytest" not in sys.modules and _truthy(os.getenv("RATELIMIT_ENABLED", "true"))


def _resolve_storage_uri() -> str:
    """Pick the rate-limit storage URI: RATELIMIT_STORAGE_URI → REDIS_URL → memory://.

    Shared (redis://) storage is REQUIRED before running >1 uvicorn worker; the
    in-process ``memory://`` default keeps today's zero-dependency single-worker
    behaviour. Pure + env-driven so the precedence is unit-testable without
    constructing a Limiter (which eagerly connects to the backend).
    """
    return os.getenv("RATELIMIT_STORAGE_URI") or os.getenv("REDIS_URL") or "memory://"


def _build_limiter() -> Limiter:
    """Construct the Limiter, falling back to memory:// if the configured storage
    can't initialize (e.g. REDIS_URL is set but the ``redis`` package isn't
    installed, or the server is unreachable at boot). A missing dependency must not
    turn a rate-limit config into a hard boot crash — the limiter still enforces,
    just per-process, and the misconfig is surfaced as a warning."""
    uri = _resolve_storage_uri()
    try:
        return Limiter(key_func=client_key, enabled=_ENABLED, storage_uri=uri)
    except Exception as exc:  # noqa: BLE001 — any storage-init failure → safe fallback
        if uri != "memory://":
            import warnings

            warnings.warn(
                f"Rate-limit storage {uri!r} unavailable ({exc}); falling back to "
                "in-process memory:// (single-worker only). Install the backend and "
                "point >1 worker at a reachable redis:// before scaling.",
                stacklevel=2,
            )
        return Limiter(key_func=client_key, enabled=_ENABLED, storage_uri="memory://")


_STORAGE_URI = _resolve_storage_uri()
limiter = _build_limiter()

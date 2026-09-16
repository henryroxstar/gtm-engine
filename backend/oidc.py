"""External-IdP federation: trusted-issuer config + JWKS verification (Track A A3).

``POST /v1/auth/exchange`` verifies an external IdP's JWT against this module and
mints the backend workspace JWT. Generic by construction — no issuer is named in
code; ``TRUSTED_ISSUERS`` (env/Doppler, JSON) is the only source:

    TRUSTED_ISSUERS='[{"issuer": "https://idp-a.example",
                       "jwks_url": "https://idp-a.example/.well-known/jwks.json",
                       "audience": "gtm-backend",
                       "algs": ["RS256"],
                       "subject_claim": "sub"}]'

The five normative verification rules (Track A PRD §A3) live here:
  1. Per issuer an explicit ASYMMETRIC ``algs`` list; ``none``/``HS*`` are rejected
     at config load, and a token's ``alg`` is checked against the list BEFORE any
     key lookup — a JWKS public key must never be usable as an HMAC secret.
  2. Keys resolve only by ``kid`` against the configured JWKS URL (https, validated
     at boot); fetches go only to boot-validated URLs, never anything token-derived.
  3. ``iss``/``aud`` must match the config exactly; ``exp``/``nbf`` enforced with
     ≤ 60 s skew. A caller that needs a FRESH token (the account step-up, with
     ``STEP_UP_MAX_AGE_S``) passes ``max_age_s``, which also requires an integer ``iat``
     that old at most; the exchange does not, so any unexpired token still exchanges.
  4. The identity key is (issuer, subject) — never subject alone.
  5. JWKS responses are cached with a bounded TTL; rotation recovers via re-fetch
     on unknown ``kid`` (rate-limited) without a restart.

Transport is injectable (the ``gtm_core/calendly_poll.py`` house style) so tests
never touch the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import HTTPException, status

# Only asymmetric JWS algorithms may appear in an issuer's ``algs`` (rule 1).
_ALLOWED_ALGS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}
)

CLOCK_SKEW_S = 60  # rule 3: exp/nbf leeway (and the iat leeway under max_age_s)
STEP_UP_MAX_AGE_S = 300  # a step-up token must have been issued (iat) within the last 5 min
INVALID_TOKEN_DETAIL = "Invalid federated token"  # nosec B105 — an error message, not a credential
MAX_TOKEN_BYTES = 8192  # oversized-token guard, checked before any parsing
JWKS_TTL_S = 300.0  # rule 5: bounded cache TTL
JWKS_REFETCH_MIN_INTERVAL_S = 30.0  # rule 5: rate limit on unknown-kid re-fetch
_HTTP_OK = 200

_ENV_VAR = "TRUSTED_ISSUERS"


class ExchangeError(ValueError):
    """A federated token failed verification. The reason is for logs/tests only —
    the route returns a generic 401 so a probe can't map which rule tripped."""


@dataclass(frozen=True)
class IssuerConfig:
    issuer: str
    jwks_url: str
    audience: str
    algs: tuple[str, ...]
    subject_claim: str = "sub"


def parse_trusted_issuers(raw: str | None) -> dict[str, IssuerConfig]:
    """Parse + boot-validate ``TRUSTED_ISSUERS``. Empty/absent → {} (feature off).

    Raises ``ValueError`` on any invalid entry — the caller (app lifespan) turns
    that into a loud boot failure rather than a lenient runtime surprise.
    """
    if not raw or not raw.strip():
        return {}
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_ENV_VAR} is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise ValueError(f"{_ENV_VAR} must be a JSON array of issuer objects")

    issuers: dict[str, IssuerConfig] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{_ENV_VAR}[{i}] must be an object")
        issuer = entry.get("issuer")
        jwks_url = entry.get("jwks_url")
        audience = entry.get("audience")
        algs = entry.get("algs")
        subject_claim = entry.get("subject_claim", "sub")
        if not issuer or not isinstance(issuer, str):
            raise ValueError(f"{_ENV_VAR}[{i}]: 'issuer' is required")
        if issuer in issuers:
            raise ValueError(f"{_ENV_VAR}: duplicate issuer {issuer!r}")
        if not isinstance(jwks_url, str) or not jwks_url.startswith("https://"):
            raise ValueError(f"{_ENV_VAR}[{i}] ({issuer}): 'jwks_url' must be an https:// URL")
        if not audience or not isinstance(audience, str):
            raise ValueError(f"{_ENV_VAR}[{i}] ({issuer}): 'audience' is required")
        if not isinstance(algs, list) or not algs:
            raise ValueError(f"{_ENV_VAR}[{i}] ({issuer}): 'algs' must be a non-empty list")
        bad = [a for a in algs if a not in _ALLOWED_ALGS]
        if bad:
            raise ValueError(
                f"{_ENV_VAR}[{i}] ({issuer}): non-asymmetric alg(s) {bad!r} — "
                f"allowed: {sorted(_ALLOWED_ALGS)}"
            )
        if not subject_claim or not isinstance(subject_claim, str):
            raise ValueError(f"{_ENV_VAR}[{i}] ({issuer}): 'subject_claim' must be a string")
        issuers[issuer] = IssuerConfig(
            issuer=issuer,
            jwks_url=jwks_url,
            audience=audience,
            algs=tuple(algs),
            subject_claim=subject_claim,
        )
    return issuers


# ── module state (config + JWKS cache; single-worker, like runs.py gate state) ─

_issuers_cache: dict[str, IssuerConfig] | None = None


def get_issuers() -> dict[str, IssuerConfig]:
    """The parsed trusted-issuer map, cached after first read."""
    global _issuers_cache
    if _issuers_cache is None:
        _issuers_cache = parse_trusted_issuers(os.getenv(_ENV_VAR))
    return _issuers_cache


def boot_validate() -> dict[str, IssuerConfig]:
    """Parse eagerly at app startup — invalid config fails the boot, loudly."""
    global _issuers_cache
    _issuers_cache = parse_trusted_issuers(os.getenv(_ENV_VAR))
    return _issuers_cache


def reset_state() -> None:
    """Test hook: forget parsed config and every cached JWKS."""
    global _issuers_cache
    _issuers_cache = None
    _jwks_cache.clear()


# ── JWKS fetch + cache ────────────────────────────────────────────────────────

# Transport: (url, timeout) -> (status_code, parsed_json_body). Injectable for tests.
Transport = Callable[..., Awaitable[tuple[int, Any]]]

_TIMEOUT_S = 10.0
# B4: a JWKS is a few KB; cap the body so a hostile/misconfigured issuer endpoint
# can't stream an unbounded response into memory. Stream + count so the cap bounds
# the actual download, not just a post-hoc len() of an already-buffered body.
_MAX_JWKS_BYTES = 1 * 1024 * 1024


async def _httpx_transport(url: str, *, timeout: float) -> tuple[int, Any]:
    import json as _json

    import httpx  # lazy — keeps the module import-light

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        async with client.stream("GET", url) as resp:
            total = 0
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > _MAX_JWKS_BYTES:
                    return resp.status_code, None  # oversized JWKS — refuse to parse
                chunks.append(chunk)
        try:
            body: Any = _json.loads(b"".join(chunks))
        except Exception:  # noqa: BLE001
            body = None
        return resp.status_code, body


@dataclass
class _JwksState:
    keys: dict[str, Any] = field(default_factory=dict)  # kid -> verification key object
    fetched_at: float = 0.0
    last_fetch_attempt: float = 0.0
    attempted: bool = False  # B4: has a fetch EVER been tried? gates the cold-start allowance


_jwks_cache: dict[str, _JwksState] = {}


def _parse_jwks(body: Any) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    for jwk_dict in (body or {}).get("keys", []) if isinstance(body, dict) else []:
        kid = jwk_dict.get("kid")
        if not kid:
            continue
        try:
            keys[str(kid)] = jwt.PyJWK(jwk_dict).key
        except Exception:  # noqa: BLE001
            continue  # nosec B112 — skip unparsable JWKS entries, keep the rest
    return keys


async def get_signing_key(
    cfg: IssuerConfig, kid: str, *, transport: Transport | None = None
) -> Any | None:
    """Resolve ``kid`` against the issuer's JWKS (rule 2), with the rule-5 cache.

    Fresh cache hit → return. Unknown ``kid`` or stale cache → re-fetch from the
    boot-validated URL, rate-limited to one attempt per ``JWKS_REFETCH_MIN_INTERVAL_S``
    so an attacker spraying random kids can't turn us into a JWKS hammer. A failed
    fetch keeps the previous keys (stale-but-present beats an outage-wide reject).
    """
    send = transport or _httpx_transport
    state = _jwks_cache.setdefault(cfg.issuer, _JwksState())
    now = time.monotonic()

    fresh = state.keys and (now - state.fetched_at) < JWKS_TTL_S
    if fresh and kid in state.keys:
        return state.keys[kid]

    # B4: rate-limit EVERY refetch, including the empty-cache case. The old
    # `or not state.keys` clause let any request bypass the limiter whenever keys
    # were empty (IdP down, 5xx, or an unparseable body) — so during an outage a
    # kid-spray turned each /auth/exchange into a fresh outbound GET, hammering the
    # IdP and holding connections open. The first attempt is always allowed (cold
    # start); after that a refetch waits out JWKS_REFETCH_MIN_INTERVAL_S whether or
    # not the previous attempt populated keys.
    if not state.attempted or (now - state.last_fetch_attempt) >= JWKS_REFETCH_MIN_INTERVAL_S:
        state.attempted = True
        state.last_fetch_attempt = now
        try:
            status_code, body = await send(cfg.jwks_url, timeout=_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — network failure: fall back to cached keys
            status_code, body = 0, None
        if status_code == _HTTP_OK and isinstance(body, dict):
            state.keys = _parse_jwks(body)
            state.fetched_at = time.monotonic()

    return state.keys.get(kid)


# ── token verification ────────────────────────────────────────────────────────


def _claim_path(claims: dict, path: str) -> Any:
    """Resolve a dotted claim path (``subject_claim`` may be nested)."""
    cur: Any = claims
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _require_fresh_iat(claims: dict, max_age_s: int) -> None:
    """Freshness for a step-up: ``iat`` must be an integer no older than ``max_age_s`` and
    not in the future, each with the rule-3 ``CLOCK_SKEW_S`` leeway. A token without ``iat``
    proves nothing about WHEN the user authenticated, so it is refused, never assumed fresh."""
    iat = claims.get("iat")
    if isinstance(iat, bool) or not isinstance(iat, int):
        raise ExchangeError("iat_missing_or_not_integer")
    age = int(time.time()) - iat
    if age > max_age_s + CLOCK_SKEW_S:
        raise ExchangeError(f"token_too_old: {age}s")
    if age < -CLOCK_SKEW_S:
        raise ExchangeError("iat_in_future")


async def verify_external_token(
    token: str,
    *,
    issuers: dict[str, IssuerConfig],
    transport: Transport | None = None,
    max_age_s: int | None = None,
) -> tuple[IssuerConfig, str, dict]:
    """Apply the five normative rules; return (issuer_cfg, subject, claims).

    Every rejection raises :class:`ExchangeError` with an internal reason. The
    unverified ``iss`` read below only SELECTS the issuer config — the verified
    decode still enforces ``issuer=`` cryptographically, so a lying ``iss`` fails
    signature/issuer validation. ``max_age_s`` additionally requires a fresh ``iat``
    (:func:`_require_fresh_iat`); omitted, verification is exactly the five rules.
    """
    if len(token.encode()) > MAX_TOKEN_BYTES:
        raise ExchangeError("token_too_large")

    try:
        header = jwt.get_unverified_header(token)
        # nosemgrep: python.jwt.security.unverified-jwt-decode.unverified-jwt-decode -- reads `iss` only to SELECT the issuer config; the verified decode below enforces issuer/audience/algs cryptographically, so a lying `iss` fails there.
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError as exc:
        raise ExchangeError(f"malformed_token: {exc}") from exc

    iss = unverified.get("iss")
    cfg = issuers.get(iss) if isinstance(iss, str) else None
    if cfg is None:
        raise ExchangeError("unknown_issuer")

    alg = header.get("alg")
    if alg not in cfg.algs:  # rule 1 — before ANY key lookup
        raise ExchangeError(f"alg_not_allowed: {alg!r}")

    kid = header.get("kid")
    if not kid or not isinstance(kid, str):  # rule 2
        raise ExchangeError("missing_kid")

    key = await get_signing_key(cfg, kid, transport=transport)
    if key is None:
        raise ExchangeError("unknown_kid")

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(cfg.algs),
            audience=cfg.audience,
            issuer=cfg.issuer,
            leeway=CLOCK_SKEW_S,  # rule 3
            options={"require": ["exp", "iss", "aud"]},
        )
    # TypeError too: PyJWT's iat check does int(payload["iat"]) and catches only ValueError,
    # so a correctly signed token carrying `"iat": null` would otherwise escape as a 500.
    except (jwt.InvalidTokenError, TypeError) as exc:
        raise ExchangeError(f"verification_failed: {exc}") from exc
    if max_age_s is not None:
        _require_fresh_iat(claims, max_age_s)

    subject = _claim_path(claims, cfg.subject_claim)
    if not subject or not isinstance(subject, str):
        raise ExchangeError("missing_subject")

    return cfg, subject, claims


async def require_federated_identity(
    token: str | None, *, max_age_s: int | None = None
) -> tuple[IssuerConfig, str, dict]:
    """The route-facing check both federated entry points share — POST /v1/auth/exchange
    and the password-less DELETE /v1/account step-up. Returns (issuer_cfg, subject, claims)
    or raises the HTTP error those routes return: 503 ``federation_not_configured`` when no
    issuer is trusted, else the one generic 401 for every credential failure, so a probe
    can't map which rule tripped."""
    issuers = get_issuers()
    if not issuers:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "federation_not_configured"}
        )
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "federated_token_invalid", "message": INVALID_TOKEN_DETAIL},
        )
    try:
        return await verify_external_token(token, issuers=issuers, max_age_s=max_age_s)
    except ExchangeError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "federated_token_invalid", "message": INVALID_TOKEN_DETAIL},
        ) from exc


def synthetic_email(issuer: str, subject: str) -> str:
    """Deterministic, collision-free ``users.email`` for a federated identity.

    Never links to a real mailbox (``.invalid`` is reserved) and never collides
    with a native account — an IdP-asserted email claim must NOT auto-link to an
    existing password account (account-takeover vector), so we don't store it as
    the identity at all. Hash binds BOTH issuer and subject (rule 4): the same
    subject at two issuers yields two users, two workspaces.
    """
    digest = hashlib.sha256(f"{issuer}\n{subject}".encode()).hexdigest()[:24]
    return f"fed-{digest}@federated.invalid"

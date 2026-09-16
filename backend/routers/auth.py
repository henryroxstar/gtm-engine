"""Auth endpoints: register, login, refresh, and the federated exchange (A3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from .. import auth as _auth
from .. import oidc
from ..ratelimit import limiter
from ..schemas import (
    ExchangeRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(body: RegisterRequest, request: Request) -> TokenResponse:
    """Create a new user + workspace. The V001 bootstrap trigger handles the rest."""
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        # Check email not already taken
        existing = await conn.fetchval("SELECT id FROM users WHERE email = $1", body.email)
        if existing:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"code": "already_exists", "message": "Email already registered"},
            )

        password_hash = _auth.hash_password(body.password)

        # Insert user — V001 bootstrap trigger creates workspace + subscription
        user_id = await conn.fetchval(
            """INSERT INTO users(email, display_name, password_hash)
               VALUES($1, $2, $3) RETURNING id::text""",
            body.email,
            body.display_name or body.email.split("@")[0],
            password_hash,
        )

        # The V001 AFTER-INSERT trigger (SECURITY DEFINER per V009) has now created
        # the workspace + owner membership + subscription. Resolve the workspace id
        # via the bootstrap helper: a plain SELECT on `workspaces` returns nothing
        # under FORCE RLS + gtm_api because no workspace context exists yet.
        workspace_id = await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", user_id)

    return TokenResponse(**_auth.token_pair(user_id, workspace_id))


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    """Verify credentials and return a token pair."""
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, password_hash FROM users WHERE email = $1", body.email
        )

    # A NULL hash is a federated (password-less) user — native login fails closed
    # on it (V016), identically to a wrong password (no account-type oracle).
    if (
        row is None
        or row["password_hash"] is None
        or not _auth.verify_password(body.password, row["password_hash"])
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "invalid_credentials", "message": "Invalid credentials"},
        )

    user_id = row["id"]
    async with pool.acquire() as conn:
        # Bootstrap helper (SECURITY DEFINER, V009): a plain SELECT on `workspaces`
        # is empty under FORCE RLS + gtm_api since there is no workspace context.
        workspace_id = await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", user_id)

    if not workspace_id:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "No workspace found")

    return TokenResponse(**_auth.token_pair(user_id, workspace_id))


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")  # rate-limited like /login and /exchange: this route mints credentials
async def refresh(body: RefreshRequest, request: Request) -> TokenResponse:
    """Exchange a refresh token for a new access + refresh pair.

    Beyond the signature/type check, the token is rejected if it was issued before the
    user's last password change — otherwise a stolen refresh token would survive a
    password reset for its full TTL (VULN-0003). A missing user row (deleted account)
    also 401s.
    """
    import jwt as _jwt

    try:
        payload = _auth.decode_token(body.refresh_token, expected_type="refresh")
    except _jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "refresh_token_expired", "message": "Refresh token expired"},
        ) from exc
    except _jwt.InvalidTokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "token_invalid", "message": "Invalid refresh token"},
        ) from exc

    # `users` is not RLS-scoped — read it on the plain pool, as login does.
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_changed_at FROM users WHERE id = $1::uuid", payload["sub"]
        )
    if row is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "token_revoked", "message": "User no longer exists"},
        )
    if _auth.token_predates_password_change(payload, row["password_changed_at"]):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "token_revoked", "message": "Token invalidated by password change"},
        )

    return TokenResponse(**_auth.token_pair(payload["sub"], payload["workspace_id"]))


@router.post("/exchange", response_model=TokenResponse)
@limiter.limit("10/minute")  # rate-limited like login (A3 acceptance)
async def exchange(body: ExchangeRequest, request: Request) -> TokenResponse:
    """Verify an external IdP's JWT and mint the backend workspace token pair.

    Verification = the five normative A3 rules (backend/oidc.py). Every rejection
    is a generic 401 — the internal reason never reaches the response, so a probe
    can't map which rule tripped. First exchange auto-bootstraps a password-less
    user (+ workspace via the V001 trigger); the identity key is (issuer, subject),
    stored in external_identities — an IdP-asserted email never links accounts.
    """
    cfg, subject, claims = await oidc.require_federated_identity(body.token)

    pool = request.app.state.pool
    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "SELECT user_id::text FROM external_identities WHERE issuer = $1 AND subject = $2",
            cfg.issuer,
            subject,
        )
        if user_id is None:
            # First exchange: create the password-less user. The synthetic email is
            # deterministic per (issuer, subject), so a concurrent first exchange
            # converges via ON CONFLICT instead of racing (both INSERTs are no-ops
            # for the loser; the trailing SELECT is authoritative either way).
            email = oidc.synthetic_email(cfg.issuer, subject)
            display = claims.get("name") or claims.get("email") or subject
            await conn.execute(
                """INSERT INTO users(email, display_name, password_hash)
                   VALUES($1, $2, NULL) ON CONFLICT (email) DO NOTHING""",
                email,
                str(display)[:200],
            )
            new_user_id = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
            await conn.execute(
                """INSERT INTO external_identities(issuer, subject, user_id)
                   VALUES($1, $2, $3::uuid) ON CONFLICT (issuer, subject) DO NOTHING""",
                cfg.issuer,
                subject,
                str(new_user_id),
            )
            user_id = await conn.fetchval(
                "SELECT user_id::text FROM external_identities WHERE issuer = $1 AND subject = $2",
                cfg.issuer,
                subject,
            )

        # Bootstrap helper (SECURITY DEFINER, V009) — same as login/register.
        workspace_id = await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", user_id)

    if not user_id or not workspace_id:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "No workspace found")

    return TokenResponse(**_auth.token_pair(user_id, workspace_id))

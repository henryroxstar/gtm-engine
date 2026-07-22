"""GTM Backend API — FastAPI multi-tenant server.

Versioned at /v1. All routes behind require_auth except /v1/auth/*.
The app is mounted behind a Cloudflare Tunnel (see deploy/cloudflare/).

Startup sequence (lifespan):
  1. Create asyncpg connection pool.
  2. Run schema migrations (idempotent).
  3. Create the BackendSessionStore.
  4. Start an idle-session eviction task.

Shutdown:
  1. Cancel the eviction task.
  2. Close all agent sessions.
  3. Close the DB pool.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import (
    assert_runtime_role_least_privilege,
    create_pool,
    ensure_runtime_role_password,
    migrate,
)
from .routers import (
    account,
    api_keys,
    auth,
    entitlement,
    ledger,
    onboard,
    profiles,
    push_tokens,
    runs,
    workspaces,
)
from .session import BackendSessionStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ───────────────────────────────────────────────────────────────
    # Two-role split (V009): the MIGRATION pool connects as the owner role
    # (POSTGRES_MIGRATION_URL, e.g. gtm_app) for DDL + role/grant management, then
    # closes — it never serves a request. The RUNTIME pool connects as the
    # non-owner gtm_api role (DATABASE_URL) so FORCE RLS actually applies.
    runtime_dsn = os.environ["DATABASE_URL"]
    migration_dsn = os.getenv("POSTGRES_MIGRATION_URL")
    is_prod = os.getenv("ENV", "production") == "production"

    # The two-role split is mandatory in production: DDL runs as the OWNER
    # (POSTGRES_MIGRATION_URL), traffic as the non-owner gtm_api (DATABASE_URL).
    # Silently falling back to the runtime DSN would run migrations as gtm_api and
    # fail with an opaque "permission denied for CREATE …". Fail with a clear message
    # instead. Locally (ENV != production) a single superuser DSN for both is fine.
    if migration_dsn is None:
        if is_prod:
            raise RuntimeError(
                "POSTGRES_MIGRATION_URL is required in production (the owner role used "
                "for DDL). DATABASE_URL is the non-owner gtm_api runtime role and cannot "
                "run migrations — see backend/schema/V009__rls_force_and_role.sql."
            )
        migration_dsn = runtime_dsn

    migration_pool = await create_pool(
        migration_dsn, min_size=1, max_size=2, application_name="gtm-backend-migrate"
    )
    try:
        await migrate(migration_pool)
        # Set gtm_api's password (Doppler) before the runtime pool connects. V009
        # creates gtm_api WITHOUT a password; in production it MUST be set here, else
        # the runtime pool can't authenticate — surface that as a clear boot error
        # rather than an opaque connection failure a few lines down.
        password_set = await ensure_runtime_role_password(migration_pool)
        if is_prod and not password_set:
            raise RuntimeError(
                "POSTGRES_GTMAPI_PASSWORD is required in production so the gtm_api "
                "runtime role can authenticate (V009 creates it without a password)."
            )
    finally:
        await migration_pool.close()

    pool = await create_pool(runtime_dsn)
    # Loud boot failure if the runtime role can bypass RLS (mis-deploy guard).
    await assert_runtime_role_least_privilege(pool)
    app.state.pool = pool

    repo_root = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parents[1]))
    app.state.sessions = BackendSessionStore(repo_root)

    from agent.config import Config

    app.state.cfg = Config.from_env(repo_root=repo_root)

    # Cost-reservation crash sweep (follow-up (f), Phase 1). Only active when
    # COST_RESERVATION_ENABLED. RESERVATION_TTL_SECONDS MUST exceed the max run wall time
    # INCLUDING the 24h gate wait (runs.py gate timeout = 86400s), so the sweep never
    # releases an actively-gated run's reservation — hence a >24h default (25h).
    from .routers.runs import _reservation_enabled

    reservation_ttl_s = int(os.getenv("RESERVATION_TTL_SECONDS", "90000"))

    # Background task: evict idle agent sessions every 60 s; sweep stale reservations.
    async def _evict():
        while True:
            await asyncio.sleep(60)
            await app.state.sessions.close_idle()
            if _reservation_enabled():
                # release_stale_reservations is SECURITY DEFINER (owned by gtm_bootstrap),
                # so this cross-tenant sweep runs without a workspace context under gtm_api.
                try:
                    async with pool.acquire() as conn:
                        await conn.fetchval(
                            "SELECT release_stale_reservations($1)", reservation_ttl_s
                        )
                except Exception:  # noqa: BLE001
                    pass

    evict_task = asyncio.create_task(_evict())

    yield

    # ── shutdown ──────────────────────────────────────────────────────────────
    evict_task.cancel()
    # Drain tracked pipeline tasks before tearing down sessions + pool.
    from .routers.runs import drain_background_tasks

    await drain_background_tasks()
    await app.state.sessions.close_all()
    await pool.close()


def create_app() -> FastAPI:
    # Error tracking (optional): active only when SENTRY_DSN is set and the SDK is
    # installed. Init before app creation so startup/lifespan errors are captured.
    from .observability import init_sentry

    init_sentry()

    app = FastAPI(
        title="GTM Content OS — Backend API",
        version="1.0.0",
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
        lifespan=lifespan,
    )

    # Rate limiting (H4): register the shared limiter + 429 handler. Per-route
    # limits live on the sensitive endpoints (auth, runs, …); disabled under pytest.
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from .ratelimit import limiter

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS: allow the mobile app origin(s) and the local dashboard.
    # In production, restrict to your actual app domain.
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Mount all routers under /v1
    for rtr in (
        auth.router,
        workspaces.router,
        profiles.router,
        runs.router,
        ledger.router,
        push_tokens.router,
        api_keys.router,
        account.router,
        onboard.router,
        # Service-to-service: the billing service → entitlement sync (service-secret auth,
        # not a user JWT — see backend/routers/entitlement.py). Replaces the RC webhook.
        entitlement.router,
    ):
        app.include_router(rtr, prefix="/v1")

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    return app


app = create_app()


def main():
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",  # nosec B104 — in-container bind; Cloudflare Tunnel is the only inbound path, no host port exposed
        port=int(os.getenv("BACKEND_PORT", "8000")),
        reload=os.getenv("ENV", "production") == "development",
        log_level="info",
    )


if __name__ == "__main__":
    main()

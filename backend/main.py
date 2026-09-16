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
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gtm_core.deploy_env import is_development, is_hardened

from .broker import connect as broker_connect
from .broker import require_broker, set_broker, worker_count
from .database import (
    advisory_singleton,
    assert_runtime_role_least_privilege,
    create_pool,
    ensure_runtime_role_password,
    migrate,
)
from .routers import (
    account,
    agents,
    api_keys,
    auth,
    entitlement,
    integrations,
    ledger,
    onboard,
    packs,
    profiles,
    publish_settings,
    push_tokens,
    runs,
    workspaces,
)
from .services.runs.fake import flag_truthy
from .session import BackendSessionStore

# Distinct advisory-lock keys for the two lifespan steps that must run ONCE per
# deployment rather than once per forked uvicorn worker (A5 §2.2).
_MIGRATE_LOCK_KEY = 0x67746D5F6D696772  # "gtm_migr"
_RECONCILE_LOCK_KEY = 0x67746D5F72636E63  # "gtm_rcnc"


def check_cors_origins(raw: str | None, env: str) -> list[str]:
    """Pure CORS-origin resolution + the production guard (A9). Always enforces.

    ``allow_credentials=True`` + a wildcard origin is the classic footgun: any
    site could drive an authenticated cross-origin request against this API.
    Browsers reject that exact combination, but a *server* shipping it is a
    misconfiguration we must not deploy — so in production an unset or wildcard
    ``CORS_ORIGINS`` raises rather than silently serving a permissive policy.
    Non-production (local dev) keeps the permissive default.

    Pure + fully parameterised, so the guard is unit-testable without building an
    app or mutating the process environment.
    """
    origins = [o.strip() for o in (raw or "*").split(",") if o.strip()]
    if is_hardened(env) and (not raw or "*" in origins):
        raise RuntimeError(
            "CORS_ORIGINS must list explicit origins in production and staging — an unset or "
            "wildcard value combined with allow_credentials=True is a "
            "cross-origin credential leak. Set it in Doppler, e.g. "
            "CORS_ORIGINS=https://app.example.com"
        )
    return origins


def resolve_cors_origins() -> list[str]:
    """Env-reading wrapper around :func:`check_cors_origins` used at app build.

    Under pytest the app is imported at collection time with no CORS config, so
    the environment is treated as non-production there; the guard's real
    behaviour is pinned by calling ``check_cors_origins`` directly in
    ``tests/backend/test_hardening_residuals.py``.
    """
    env = os.getenv("ENV", "production")
    if "pytest" in sys.modules:
        env = "test"
    return check_cors_origins(os.getenv("CORS_ORIGINS"), env)


def check_fake_runs(raw: str | None, env: str | None) -> bool:
    """Pure ``GTM_FAKE_RUNS`` boot guard. True when scripted runs are enabled.

    The flag swaps the Agent SDK for a scripted executor (backend/services/runs/fake.py) so a
    client developer can drive the whole run lifecycle with no model key. That is only ever
    legitimate on a local stack: anywhere else it would serve fabricated runs to real tenants
    while looking healthy. So a truthy flag outside ``ENV=development`` — including an unset
    ``ENV``, which this module reads as production — raises. Ignoring it instead would leave
    an operator believing a flag is on that is not; honouring it would be worse.

    Pure + fully parameterised, like :func:`check_cors_origins`.
    """
    enabled = flag_truthy(raw)
    if enabled and not is_development(env):
        raise RuntimeError(
            f"GTM_FAKE_RUNS is set but ENV={env!r} — scripted runs are local-development "
            "only. Unset GTM_FAKE_RUNS, or set ENV=development on a local stack."
        )
    return enabled


# deploy/.env.dev.example commits PUBLIC values for BACKEND_JWT_SECRET and BILLING_SYNC_SECRET;
# both start with this prefix (pinned against that file in test_hardening_residuals.py).
_DEV_SECRET_PREFIX = "local-dev-"  # nosec B105 — a marker prefix, not a credential

# VAULT_KEK cannot carry a text prefix like the other two: it must be valid 32-byte hex (get_kek()
# rejects anything else), and no ASCII marker survives that. So its dev placeholder
# (deploy/.env.dev.example) is guarded by exact match instead of a prefix — pinned against that
# file in test_hardening_residuals.py. Unlike a leaked JWT/billing secret, this one is
# non-revocable: past-encrypted `encrypted_credentials` rows stay decryptable by anyone holding
# this public repo value even after the real KEK is rotated in, since rotation re-encrypts only
# rows a rotation pass has touched.
_DEV_VAULT_KEK = (  # nosec B105 — a known-public placeholder, not a credential
    "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
)


def check_no_dev_secrets(
    env: str | None,
    jwt_secret: str | None,
    billing_sync_secret: str | None,
    vault_kek: str | None = None,
) -> None:
    """Pure boot guard: the committed local-development secrets never serve a real ENV.

    deploy/.env.dev.example is copy-and-go, so its secrets are public. Copied into a real
    config, anyone could forge a workspace token (``BACKEND_JWT_SECRET``), grant themselves an
    entitlement (``BILLING_SYNC_SECRET``), or decrypt every workspace's BYOK credentials
    (``VAULT_KEK`` — see the module-level comment on ``_DEV_VAULT_KEK`` for why that one is
    worse: non-revocable). So outside ``ENV=development`` — an unset ``ENV`` included — a
    prefixed JWT/billing value or an exact-match dev KEK raises. The message names the
    variable, never its value. An empty or unset secret is not this guard's concern.

    Pure + fully parameterised, like :func:`check_cors_origins`.
    """
    if is_development(env):
        return
    prefixed = (
        ("BACKEND_JWT_SECRET", jwt_secret),
        ("BILLING_SYNC_SECRET", billing_sync_secret),
    )
    public = [name for name, value in prefixed if value and value.startswith(_DEV_SECRET_PREFIX)]
    if vault_kek and vault_kek == _DEV_VAULT_KEK:
        public.append("VAULT_KEK")
    if public:
        raise RuntimeError(
            f"{' and '.join(public)}: public local-development value from "
            f"deploy/.env.dev.example, but ENV={env!r}. Set a real secret from Doppler, or "
            "ENV=development on a local stack."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ───────────────────────────────────────────────────────────────
    # First, before any DB work: scripted runs refuse to boot anywhere but ENV=development.
    if check_fake_runs(os.getenv("GTM_FAKE_RUNS"), os.getenv("ENV")):
        logging.getLogger(__name__).warning(
            "GTM_FAKE_RUNS is ON — every run is SCRIPTED: no model is called, no cost is "
            "recorded, nothing is published. Local development only."
        )
    # Also before any DB work: the public dev secrets never boot outside ENV=development.
    check_no_dev_secrets(
        os.getenv("ENV"),
        os.getenv("BACKEND_JWT_SECRET"),
        os.getenv("BILLING_SYNC_SECRET"),
        os.getenv("VAULT_KEK"),
    )

    # Two-role split (V009): the MIGRATION pool connects as the owner role
    # (POSTGRES_MIGRATION_URL, e.g. gtm_app) for DDL + role/grant management, then
    # closes — it never serves a request. The RUNTIME pool connects as the
    # non-owner gtm_api role (DATABASE_URL) so FORCE RLS actually applies.
    runtime_dsn = os.environ["DATABASE_URL"]
    migration_dsn = os.getenv("POSTGRES_MIGRATION_URL")
    # Hardened covers staging too: the two-role split silently collapsing to one DSN is
    # exactly as wrong on staging, which rehearses the artifact that ships.
    is_prod = is_hardened()

    # A3: parse + validate TRUSTED_ISSUERS eagerly — a bad issuer config (non-https
    # JWKS URL, HS*/none alg) must fail the boot loudly, never surface per-request.
    from .oidc import boot_validate

    boot_validate()

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
        # A5: N forked uvicorn workers each run this whole lifespan, so migrate() would
        # run N times and concurrent DDL can deadlock. The advisory lock makes exactly
        # one worker migrate; the losers wait, then find every file already in
        # schema_migrations and do nothing.
        async with advisory_singleton(migration_pool, _MIGRATE_LOCK_KEY):
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
                    pass  # nosec B110 — intentional best-effort swallow

    evict_task = asyncio.create_task(_evict())

    # A5 step 3: the cross-worker transport. Connect BEFORE the queue loops start, so a
    # run claimed in the first second already fans its events out to every worker. A
    # missing/unreachable broker degrades to in-process delivery at one worker and is
    # REFUSED at more than one — see broker.require_broker.
    from .services.runs.events import on_broker_message, start_relay, stop_relay

    broker = await broker_connect(on_broker_message)
    require_broker(worker_count(), broker)
    set_broker(broker)
    # One relay per worker: it assigns each event its durable run_events id and is what
    # makes ?since= replay possible. Started unconditionally — durability does not depend
    # on the broker, only cross-worker delivery does.
    start_relay(pool)

    # A5 gate reconciliation: resume runs a restart stranded mid-gate (and apply a
    # decision posted while this process was down). Best-effort — never blocks boot.
    # Under the advisory lock so N workers do not each re-dispatch the same run.
    from .routers.runs import reconcile_gates

    try:
        async with advisory_singleton(pool, _RECONCILE_LOCK_KEY):
            await reconcile_gates(pool, repo_root)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("gate reconciliation failed at startup")

    # A5 step 2: this worker's claim + heartbeat loops. Every run now reaches an executor
    # through here, so a run outlives the process that accepted its request.
    from .services.runs.queue import claim_loop, heartbeat_loop, stop_task

    queue_task = asyncio.create_task(claim_loop(pool, repo_root, app.state.sessions))
    heartbeat_task = asyncio.create_task(heartbeat_loop(pool))

    yield

    # ── shutdown ──────────────────────────────────────────────────────────────
    evict_task.cancel()
    # Stop claiming before draining, or the loop hands out work while we tear down.
    await stop_task(queue_task)
    await stop_task(heartbeat_task)
    # Drain tracked pipeline tasks before tearing down sessions + pool.
    from .routers.runs import drain_background_tasks

    await drain_background_tasks()
    # Relay last: the drain's terminal `done` events are still worth persisting.
    await stop_relay()
    if broker is not None:
        await broker.close()
        set_broker(None)
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

    # Rate limiting (H4): register the shared limiter. Per-route
    # limits live on the sensitive endpoints (auth, runs, …); disabled under pytest.
    from .ratelimit import limiter

    app.state.limiter = limiter

    # Unified error handling (PRD 2026-09-11): normalize Starlette/FastAPI HTTPExceptions,
    # RequestValidationError, and RateLimitExceeded into canonical {"error": {...}} envelope.
    from .errors import register_error_handlers

    register_error_handlers(app)

    # CORS: allow the mobile app origin(s) and the local dashboard.
    origins = resolve_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
        # A cross-origin response's headers are invisible to the page unless exposed; the
        # RFC 6750 challenge is how a browser client tells an expired token from a bad one,
        # and Retry-After lets a browser client back off correctly on a 429.
        expose_headers=["WWW-Authenticate", "Retry-After"],
    )

    # Mount all routers under /v1
    for rtr in (
        auth.router,
        workspaces.router,
        profiles.router,
        packs.router,
        agents.router,
        runs.router,
        ledger.router,
        push_tokens.router,
        api_keys.router,
        account.router,
        onboard.router,
        # Service-to-service: the billing service → entitlement sync (service-secret auth,
        # not a user JWT — see backend/routers/entitlement.py). Replaces the RC webhook.
        entitlement.router,
        # Service-to-service: per-workspace publish destination (A5). Same service-secret
        # auth — a user JWT could otherwise set its own egress target.
        publish_settings.router,
        integrations.router,
    ):
        app.include_router(rtr, prefix="/v1")

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    return app


app = create_app()


def main():
    import uvicorn

    # BACKEND_WORKERS defaults to 1: A5 makes >1 SAFE, it does not turn it on. Flipping
    # staging to 2 is a deliberate operator step after a soak. `reload` forks its own
    # process and is mutually exclusive with `workers`, so development ignores it.
    reload_enabled = is_development()
    workers = 1 if reload_enabled else worker_count()

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",  # nosec B104 — in-container bind; Cloudflare Tunnel is the only inbound path, no host port exposed
        port=int(os.getenv("BACKEND_PORT", "8000")),
        reload=reload_enabled,
        workers=workers if workers > 1 else None,
        log_level="info",
    )


if __name__ == "__main__":
    main()

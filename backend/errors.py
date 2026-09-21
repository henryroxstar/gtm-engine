"""Unified error envelopes and centralized exception handlers for the backend API.

Harmonizes all API endpoints to emit a predictable, typed error contract:
{
    "error": {
        "code": "...",
        "message": "...",
        "details": ...
    },
    "detail": ... # backward compatibility alias during client transition
}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from .schemas import ErrorResponse

# Backward-compatibility alias for internal references (PRD M-03)
UnifiedErrorResponse = ErrorResponse

log = logging.getLogger(__name__)


_STATUS_DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    403: "forbidden",
    410: "gone",
    422: "unprocessable_entity",
    429: "rate_limit_exceeded",
    503: "service_unavailable",
}

_NOT_FOUND_ENTITIES = (
    "run",
    "workspace",
    "user",
    "artifact",
    "agent",
    "profile",
    "draft",
    "pack",
    "token",
    "api_key",
    "subscription",
)


def _status_to_code(status_code: int, message: str) -> str:
    """Map HTTP status codes and messages to canonical snake_case error slugs."""
    msg_lower = message.lower()
    if status_code == 401:
        return (
            "invalid_credentials"
            if any(w in msg_lower for w in ("credential", "password"))
            else "unauthorized"
        )
    if status_code == 404:
        for entity in _NOT_FOUND_ENTITIES:
            if entity in msg_lower:
                return f"{entity}_not_found"
        return "not_found"
    if status_code == 409:
        return (
            "already_exists"
            if any(w in msg_lower for w in ("already registered", "already exists", "taken"))
            else "conflict"
        )
    if status_code in _STATUS_DEFAULT_CODES:
        return _STATUS_DEFAULT_CODES[status_code]
    return "internal_error" if status_code >= 500 else f"http_{status_code}"


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Transform starlette/fastapi HTTPExceptions into the unified envelope."""
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        code = str(exc.detail["code"])
        message = str(exc.detail.get("message") or code.replace("_", " ").capitalize())
        details = {k: v for k, v in exc.detail.items() if k not in ("code", "message")} or None
    else:
        message = str(exc.detail)
        code = _status_to_code(exc.status_code, message)
        details = None

    content = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
        "detail": exc.detail,
    }
    headers = getattr(exc, "headers", None)
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


def _strip_noisy_ctx(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop pydantic's `ctx.error` — the raised exception OBJECT a `value_error` (e.g. a
    `@model_validator` that raises `ValueError(...)`) carries in `ctx`. It isn't JSON
    serializable, so `jsonable_encoder` falls back to an empty `{}`: content-free noise
    that looks like it should carry detail but never does, and fully duplicates `msg`
    (which already renders the same exception's `str()`). Drop `ctx` entirely once
    `error` was its only key, rather than leave a dangling empty dict."""
    cleaned = []
    for e in errors:
        ctx = e.get("ctx")
        if isinstance(ctx, dict) and "error" in ctx:
            ctx = {k: v for k, v in ctx.items() if k != "error"}
            e = {**e, "ctx": ctx} if ctx else {k: v for k, v in e.items() if k != "ctx"}
        cleaned.append(e)
    return cleaned


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Transform request validation errors into the unified envelope."""
    # `input` echoes the rejected value (e.g. a candidate password) into client logs.
    errors = jsonable_encoder(
        _strip_noisy_ctx(
            [{k: v for k, v in e.items() if k not in ("input", "url")} for e in exc.errors()]
        )
    )
    content = {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
            "details": errors,
        },
        "detail": errors,
    }
    return JSONResponse(status_code=422, content=content)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Transform slowapi rate limit exceeded into the unified envelope."""
    message = str(exc.detail) if exc.detail else "Rate limit exceeded"
    content = {
        "error": {
            "code": "rate_limit_exceeded",
            "message": message,
            "details": None,
        },
        "detail": message,
    }
    response = JSONResponse(status_code=429, content=content)
    # RFC 7231 §7.1.3: a delay-seconds value is a valid Retry-After. The limit's own window
    # duration is a safe upper bound without touching the rate-limit storage backend the way
    # slowapi's own (unused here) header injection does — that can itself raise on a down
    # backend. Read defensively rather than assume exc.limit is a real slowapi Limit: a header
    # we can't compute must never turn a 429 into a 500.
    get_expiry = getattr(getattr(exc, "limit", None), "limit", None)
    get_expiry = getattr(get_expiry, "get_expiry", None)
    if callable(get_expiry):
        response.headers["Retry-After"] = str(get_expiry())
    return response


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Any exception no other handler claims: the 500 envelope, with the cause logged only."""
    log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    message = "Internal server error"
    content = {
        "error": {"code": "internal_error", "message": message, "details": None},
        "detail": message,
    }
    return JSONResponse(status_code=500, content=content)


class UnhandledExceptionMiddleware:
    """Catches an otherwise-unhandled exception before it reaches Starlette's own
    ``ServerErrorMiddleware``.

    Starlette special-cases a handler registered for the base ``Exception`` class (or
    status 500): ``Starlette.build_middleware_stack`` always routes it to
    ``ServerErrorMiddleware``, which is built OUTSIDE every ``app.add_middleware(...)``
    layer — including ``CORSMiddleware`` — and, after invoking the handler, always
    re-raises the exception ("this allows servers to log the error"). Two consequences:
    a 500 response built there never carries CORS headers (CORSMiddleware's `send`
    wrapper never sees it, since the response is sent directly by ServerErrorMiddleware
    outside CORS), so a browser client can't read the error body at all; and the
    exception gets logged twice — once by `unhandled_exception_handler` here, once more
    by uvicorn's own "Exception in ASGI application" line when the re-raise reaches it.

    Registering this middleware via `app.add_middleware` BEFORE `CORSMiddleware` (so it
    ends up wrapped BY it — see `register_error_handlers`) fixes both: it catches the
    exception here, inside CORS, so the JSONResponse it builds flows back out through
    CORS's header-adding `send` wrapper, and it does not re-raise — so
    ServerErrorMiddleware (and therefore uvicorn's own logging) never sees the exception
    at all. `add_exception_handler(Exception, ...)` stays registered too, as a
    last-resort fallback for whatever this middleware can't catch — e.g. a failure
    raised after a streamed response (the run SSE stream) has already started, which
    `response_started` below (the same guard Starlette's own middleware uses) detects
    and leaves alone rather than attempt a second, invalid response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def _send(message: Any) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            if response_started:
                raise
            response = await unhandled_exception_handler(Request(scope, receive=receive), exc)
            await response(scope, receive, send)


async def refusal_exception_handler(request: Request, exc: Any) -> JSONResponse:
    """Fleet Phase A (Task 3): a ``backend.callers.ports.Refusal`` raised anywhere in a
    route body — not just inside the ``require_principal`` dependency itself, which
    already converts its own refusals via ``refusal_to_http`` before they ever reach
    here.

    ``backend/callers/dependency.py``'s admission primitives (``bind_agent``,
    ``require_pack_mode``, ``require_human``, ``authorize_run_read``) and
    ``backend/callers/limits.py``'s ``enforce_agent_daily_cap`` all raise ``Refusal``
    rather than ``HTTPException`` — by the time one of these runs, the route's own
    ``Depends(require_principal)`` has already returned successfully, so there is no
    dependency-level boundary left to catch it. Registering ONE handler here — rather
    than a local ``try/except Refusal: raise refusal_to_http(exc) from exc`` around
    every call site — is the DRYer choice given how many sites in ``create_run`` alone
    can raise one; it reuses the exact same ``refusal_to_http`` adapter and the exact
    same envelope ``http_exception_handler`` already builds, so a ``Refusal`` and an
    ``HTTPException`` render byte-identically on the wire.
    """
    from backend.callers.dependency import refusal_to_http

    return await http_exception_handler(request, refusal_to_http(exc))


def register_error_handlers(app: Any) -> None:
    """Register the canonical exception handlers on a FastAPI application.

    Also installs `UnhandledExceptionMiddleware` — call this BEFORE
    `app.add_middleware(CORSMiddleware, ...)` so the new middleware ends up wrapped BY
    CORS (see its docstring for why the ordering matters).
    """
    from backend.callers.ports import Refusal

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(Refusal, refusal_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(UnhandledExceptionMiddleware)

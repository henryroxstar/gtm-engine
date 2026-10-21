"""Contract tests for the unified API error envelope.

Asserts that all error responses across 4xx and 5xx status codes conform to:
{
    "error": {
        "code": "<snake_case_slug>",
        "message": "<human_diagnostic_string>",
        "details": <structured_data_or_null>
    },
    "detail": <legacy_alias_for_backward_compat>
}
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded

from backend.errors import register_error_handlers
from backend.schemas import RegisterRequest, RunRequest


@pytest.fixture
def test_app():
    app = FastAPI()
    register_error_handlers(app)

    class SampleBody(BaseModel):
        name: str
        age: int = Field(gt=0)

    @app.get("/test/plain-404")
    def get_plain_404():
        raise HTTPException(status_code=404, detail="Run not found")

    @app.get("/test/plain-401")
    def get_plain_401():
        raise HTTPException(status_code=401, detail="Invalid credentials")

    @app.get("/test/plain-403")
    def get_plain_403():
        raise HTTPException(status_code=403, detail="Permission denied")

    @app.get("/test/plain-409")
    def get_plain_409():
        raise HTTPException(status_code=409, detail="Email already registered")

    @app.get("/test/structured-403")
    def get_structured_403():
        raise HTTPException(status_code=403, detail={"code": "pack_not_activated"})

    @app.get("/test/structured-422")
    def get_structured_422():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "pack_not_ready",
                "blocked": [{"kind": "setting", "name": "profile"}],
            },
        )

    @app.post("/test/validation")
    def post_validation(body: SampleBody):
        return {"ok": True}

    @app.post("/test/run-request")
    def post_run_request(body: RunRequest):
        return {"ok": True}

    @app.post("/test/register")
    def post_register(body: RegisterRequest):
        return {"ok": True}

    @app.get("/test/rate-limit")
    def get_rate_limit():
        import types

        lim = types.SimpleNamespace(error_message="10 per 1 minute", limit="10/minute")
        raise RateLimitExceeded(lim)

    @app.get("/test/rate-limit-real")
    def get_rate_limit_real():
        from limits import parse
        from slowapi.wrappers import Limit

        item = parse("5/minute")
        lim = Limit(
            item,
            key_func=lambda: "x",
            scope=None,
            per_method=False,
            methods=None,
            error_message=None,
            exempt_when=None,
            cost=1,
            override_defaults=False,
        )
        raise RateLimitExceeded(lim)

    @app.get("/test/unhandled")
    def get_unhandled():
        raise RuntimeError("internal-diagnostic-detail")

    return app


def test_plain_string_http_exception_envelope(test_app):
    client = TestClient(test_app)
    resp = client.get("/test/plain-404")
    assert resp.status_code == 404
    data = resp.json()

    # Canonical envelope
    assert "error" in data
    assert data["error"]["code"] == "run_not_found"
    assert data["error"]["message"] == "Run not found"
    assert data["error"]["details"] is None

    # Backward-compatible alias
    assert data["detail"] == "Run not found"


def test_plain_401_invalid_credentials(test_app):
    client = TestClient(test_app)
    resp = client.get("/test/plain-401")
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "invalid_credentials"
    assert data["error"]["message"] == "Invalid credentials"
    assert data["detail"] == "Invalid credentials"


def test_plain_409_already_exists(test_app):
    client = TestClient(test_app)
    resp = client.get("/test/plain-409")
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"]["code"] == "already_exists"
    assert data["detail"] == "Email already registered"


def test_structured_code_http_exception_envelope(test_app):
    client = TestClient(test_app)
    resp = client.get("/test/structured-403")
    assert resp.status_code == 403
    data = resp.json()

    assert "error" in data
    assert data["error"]["code"] == "pack_not_activated"
    assert data["error"]["message"] == ""
    assert data["error"]["details"] is None
    assert data["error"]["next_step"] is None

    # Legacy structured detail preserved
    assert data["detail"] == {"code": "pack_not_activated"}


def test_structured_code_with_details_envelope(test_app):
    client = TestClient(test_app)
    resp = client.get("/test/structured-422")
    assert resp.status_code == 422
    data = resp.json()

    assert data["error"]["code"] == "pack_not_ready"
    assert data["error"]["details"] == {"blocked": [{"kind": "setting", "name": "profile"}]}
    assert data["detail"] == {
        "code": "pack_not_ready",
        "blocked": [{"kind": "setting", "name": "profile"}],
    }


def test_request_validation_error_envelope(test_app):
    client = TestClient(test_app)
    resp = client.post("/test/validation", json={"name": "Alice", "age": -5})
    assert resp.status_code == 422
    data = resp.json()

    assert "error" in data
    assert data["error"]["code"] == "validation_error"
    assert data["error"]["message"] == "Request validation failed"
    assert isinstance(data["error"]["details"], list)
    assert len(data["error"]["details"]) > 0

    # Detail alias carries the FastAPI validation list
    assert data["detail"] == data["error"]["details"]


def test_model_validator_value_error_is_a_422_envelope(test_app):
    """ER-01: a model_validator ValueError puts the exception object in ctx; the
    handler must still serialize it rather than crash into a text/plain 500."""
    client = TestClient(test_app)
    resp = client.post(
        "/test/run-request", json={"pack": "marketing", "profile_name": "example-profile"}
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/json"
    data = resp.json()
    assert data["error"]["code"] == "validation_error"
    assert data["error"]["message"] == "Request validation failed"
    [entry] = data["error"]["details"]
    assert entry["type"] == "value_error"
    assert entry["loc"] == ["body"]
    assert entry["msg"] == "Value error, pack mode requires 'variant'"
    # ctx.error is pydantic's raised ValueError object; jsonable_encoder can't serialize
    # it (falls back to `{}`), so it's content-free noise fully duplicating `msg` above —
    # stripped entirely, not just left as an empty dict.
    assert "ctx" not in entry
    assert data["detail"] == data["error"]["details"]


def test_validation_error_does_not_echo_submitted_input(test_app):
    """ER-09: a rejected field value (here a too-short password) is never echoed back."""
    client = TestClient(test_app)
    resp = client.post("/test/register", json={"email": "user@example.com", "password": "pw-7chr"})
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == "validation_error"
    assert [e["loc"] for e in data["error"]["details"]] == [["body", "password"]]
    for entry in data["error"]["details"] + data["detail"]:
        assert set(entry) == {"type", "loc", "msg", "ctx"}
    assert data["error"]["details"][0]["ctx"] == {"min_length": 8}
    assert "pw-7chr" not in resp.text


def test_unhandled_exception_is_a_500_envelope(test_app, caplog):
    """ER-02: an unhandled exception is the internal_error envelope, never text/plain,
    and its message stays in the server log rather than the response body."""
    client = TestClient(test_app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="backend.errors"):
        resp = client.get("/test/unhandled")
    assert resp.status_code == 500
    assert resp.headers["content-type"] == "application/json"
    assert resp.json() == {
        "error": {"code": "internal_error", "message": "Internal server error", "details": None},
        "detail": "Internal server error",
    }
    assert "internal-diagnostic-detail" not in resp.text
    [record] = [r for r in caplog.records if r.name == "backend.errors"]
    assert str(record.exc_info[1]) == "internal-diagnostic-detail"


def test_unhandled_exception_carries_cors_headers_and_logs_once(caplog):
    """A generic `Exception` handler is special-cased by Starlette to run in
    `ServerErrorMiddleware`, which sits OUTSIDE every `app.add_middleware(...)` layer —
    including CORSMiddleware — and always re-raises after responding so the ASGI server
    can also log it. Unpatched, that means a browser client can't read a 500 body at all
    (no CORS header) and the exception is logged twice. `register_error_handlers` also
    installs `UnhandledExceptionMiddleware`, positioned inside CORSMiddleware, so neither
    happens: the response carries CORS headers, and there is exactly one log record.
    """
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://example-app.test"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/test/unhandled")
    def get_unhandled():
        raise RuntimeError("internal-diagnostic-detail")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="backend.errors"):
        resp = client.get("/test/unhandled", headers={"Origin": "https://example-app.test"})

    assert resp.status_code == 500
    assert resp.headers["access-control-allow-origin"] == "https://example-app.test"
    assert resp.json()["error"]["code"] == "internal_error"
    assert len([r for r in caplog.records if r.name == "backend.errors"]) == 1


def test_rate_limit_exceeded_envelope(test_app):
    client = TestClient(test_app)
    resp = client.get("/test/rate-limit")
    assert resp.status_code == 429
    data = resp.json()

    assert "error" in data
    assert data["error"]["code"] == "rate_limit_exceeded"
    assert "10 per 1 minute" in data["error"]["message"]
    assert "10 per 1 minute" in data["detail"]
    # The fixture's limit is a bare SimpleNamespace, not a real slowapi Limit — Retry-After
    # computation must degrade to "omit the header", never crash the request into a 500.
    assert "retry-after" not in resp.headers


def test_rate_limit_exceeded_sets_retry_after(test_app):
    """A real slowapi Limit carries a real RateLimitItem, so Retry-After is computable —
    a browser client needs it exposed via CORS to back off correctly on a 429."""
    client = TestClient(test_app)
    resp = client.get("/test/rate-limit-real")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "60"


def test_app_handler_registration():
    """Verify register_error_handlers wires handlers that catch standard Starlette 404s."""
    app = FastAPI()
    register_error_handlers(app)
    client = TestClient(app)
    resp = client.get("/non-existent-route")
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "not_found"
    assert data["error"]["message"] == "Not Found"
    assert data["detail"] == "Not Found"

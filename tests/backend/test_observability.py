"""Tests for backend.observability (Sentry) + backend.ratelimit storage selection.

Both features are off/in-process by default and opt-in via env. These pin the
secret-safety (Authorization is scrubbed) and the no-op-by-default contract, plus
the multi-worker storage-uri resolution precedence.
"""

from __future__ import annotations

from backend import observability, ratelimit


def test_init_sentry_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert observability.init_sentry() is False


def test_init_sentry_noop_when_sdk_absent(monkeypatch):
    # DSN set but sentry_sdk not importable → still a no-op, never raises.
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    import builtins

    real_import = builtins.__import__

    def _no_sentry(name, *args, **kwargs):
        if name == "sentry_sdk":
            raise ImportError("simulated: sentry-sdk not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_sentry)
    assert observability.init_sentry() is False


def test_scrub_removes_auth_and_cookies():
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer sk-secret-value",
                "Cookie": "session=abc",
                "X-GTM-Proxy-Secret": "edge-secret",
                "Content-Type": "application/json",
            }
        }
    }
    scrubbed = observability._scrub(event, {})
    headers = scrubbed["request"]["headers"]
    assert headers["Authorization"] == "[scrubbed]"
    assert headers["Cookie"] == "[scrubbed]"
    assert headers["X-GTM-Proxy-Secret"] == "[scrubbed]"
    # Non-sensitive headers are untouched.
    assert headers["Content-Type"] == "application/json"


def test_scrub_tolerates_missing_headers():
    # No request/headers → returned unchanged, never raises.
    assert observability._scrub({}, {}) == {}


def test_ratelimit_storage_defaults_to_memory(monkeypatch):
    monkeypatch.delenv("RATELIMIT_STORAGE_URI", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert ratelimit._resolve_storage_uri() == "memory://"


def test_ratelimit_storage_prefers_explicit_uri_over_redis_url(monkeypatch):
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://explicit:6379")
    monkeypatch.setenv("REDIS_URL", "redis://fallback:6379")
    assert ratelimit._resolve_storage_uri() == "redis://explicit:6379"


def test_ratelimit_storage_falls_back_to_redis_url(monkeypatch):
    monkeypatch.delenv("RATELIMIT_STORAGE_URI", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://fallback:6379")
    assert ratelimit._resolve_storage_uri() == "redis://fallback:6379"


def test_build_limiter_falls_back_when_storage_unavailable(monkeypatch):
    # A redis:// URI with no redis package/server must NOT crash the process —
    # _build_limiter degrades to memory:// (per-process) and warns.
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://nonexistent-host:6379")
    monkeypatch.delenv("REDIS_URL", raising=False)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lim = ratelimit._build_limiter()
    assert lim is not None  # constructed despite the unreachable/absent backend


# ── A5 step 3: the broker resolves through the SAME storage decision ──────────


def test_broker_url_follows_the_rate_limit_storage_precedence(monkeypatch):
    """Rule 11 — one storage decision, one precedence. A second env var for the SSE/gate
    fan-out is exactly how a deploy ends up with the limiter and the fan-out pointed at
    different Redis instances, each half-working."""
    from backend import broker

    monkeypatch.delenv("RATELIMIT_STORAGE_URI", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert broker.broker_url() is None, "memory:// means there is no shared broker"

    monkeypatch.setenv("REDIS_URL", "redis://fallback:6379")
    assert broker.broker_url() == "redis://fallback:6379"

    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://explicit:6379")
    assert broker.broker_url() == "redis://explicit:6379"


def test_multi_worker_with_no_broker_refuses_to_boot():
    """A multi-worker process that silently fell back to per-process rate limiters, a
    split SSE fan-out, and gates that only wake on the poll is the green-but-wrong
    failure this guard exists to prevent — it stays invisible until a customer loses a
    run. Fail closed, naming the reason."""
    import pytest

    from backend import broker

    with pytest.raises(RuntimeError) as exc:
        broker.require_broker(2, None)
    assert "BACKEND_WORKERS=2" in str(exc.value)


def test_single_worker_with_no_broker_still_boots():
    """The positive control for the guard above (the repo's fail-closed-probe rule: a
    denial test carries its allowed twin). At one worker the in-process transport is
    complete, so dev, the test suite, and today's deployed stack keep working with no
    Redis at all — the guard must not have made Redis mandatory everywhere."""
    from backend import broker

    broker.require_broker(1, None)  # must not raise


def test_worker_count_defaults_to_one_and_survives_a_bad_value(monkeypatch):
    """A5 makes >1 SAFE; it does not turn it on. A typo in the env must not silently
    scale the deployment either."""
    from backend import broker

    monkeypatch.delenv("BACKEND_WORKERS", raising=False)
    assert broker.worker_count() == 1
    monkeypatch.setenv("BACKEND_WORKERS", "not-a-number")
    assert broker.worker_count() == 1
    monkeypatch.setenv("BACKEND_WORKERS", "0")
    assert broker.worker_count() == 1
    monkeypatch.setenv("BACKEND_WORKERS", "4")
    assert broker.worker_count() == 4

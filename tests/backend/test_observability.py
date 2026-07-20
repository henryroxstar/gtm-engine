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

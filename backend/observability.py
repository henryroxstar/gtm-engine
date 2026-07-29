"""Error tracking (Sentry) for the backend — optional, secret-safe, no-op by default.

Wired into ``backend/main.py:create_app()``. Three deliberate properties:

  1. **Optional dependency.** ``sentry-sdk`` is a lint/prod extra, not a runtime
     dep, so the unit suites and a minimal install never need it. The import is
     guarded — if the package is absent, :func:`init_sentry` is a silent no-op.
  2. **Off unless configured.** Nothing is sent unless ``SENTRY_DSN`` is set
     (Doppler-injected in staging/prod). Local dev and CI stay silent.
  3. **Never leak secrets or tenant PII** (§ CLAUDE.md "never echo secrets").
     ``send_default_pii=False`` and a ``before_send`` scrubber strips the
     ``Authorization`` bearer, cookies, and the proxy-secret header before any
     event leaves the process. Request bodies are not attached by default.

Error-rate ALERTS are a Sentry-dashboard concern (Alerts → "number of errors > N
in M minutes" and the crash-free-session rule), configured by the operator once
the DSN is live — see docs/runbooks/observability.md.
"""

from __future__ import annotations

import os

# Headers that must never reach Sentry — auth material and the edge proxy secret.
_SCRUB_HEADERS = frozenset({"authorization", "cookie", "x-gtm-proxy-secret", "set-cookie"})


def _scrub(event: dict, _hint: dict) -> dict:
    """before_send hook: drop auth/cookie headers so no bearer token is ever sent."""
    try:
        headers = event.get("request", {}).get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in _SCRUB_HEADERS:
                    headers[key] = "[scrubbed]"
    except Exception:  # noqa: BLE001 — scrubbing must never break error reporting
        pass
    return event


def init_sentry() -> bool:
    """Initialise Sentry if configured. Returns True when active, else False (no-op).

    Safe to call unconditionally at app creation. Absent SDK or unset DSN → no-op.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("ENV", "production"),
        release=os.getenv("SENTRY_RELEASE") or None,
        # Error-only by default; opt into a small perf-trace sample via env.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        # Do NOT attach user IP / cookies / request bodies (tenant PII + secrets).
        send_default_pii=False,
        before_send=_scrub,
    )
    return True

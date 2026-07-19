"""P5 tests — fallback chains + circuit breaker (model routing PRD §7/§10).

The self-heal contract:
  - a retryable error (deprecation 404, 429, 5xx, timeout) falls through to the next
    spec in the chain; a non-retryable error (400/401) is re-raised, not masked
  - the circuit breaker opens after N consecutive failures and cools down after a
    fixed window (driven by an injected clock — no real sleeping)
  - while the breaker is open the primary is skipped and the chain starts at the
    fallback

SDK-INDEPENDENT: gtm_core.models is pure stdlib; the retryable classifier is
duck-typed so no httpx import is needed.
"""

from __future__ import annotations

import asyncio

import pytest

from gtm_core.models import (
    CircuitBreaker,
    ModelSpec,
    call_with_fallback,
    is_retryable_error,
)


def _spec(role: str, model: str, fallbacks: tuple = ()) -> ModelSpec:
    return ModelSpec(
        role=role,
        provider="test",
        model=model,
        base_url="https://x",
        api_key_env="X_API_KEY",
        fallbacks=fallbacks,
    )


class _Resp:
    def __init__(self, status: int):
        self.status_code = status


class _HTTPError(Exception):
    def __init__(self, status: int):
        self.response = _Resp(status)


# --- retryable classification ------------------------------------------------


@pytest.mark.parametrize("status", [404, 408, 429, 500, 502, 503, 504])
def test_retryable_statuses(status):
    assert is_retryable_error(_HTTPError(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_non_retryable_statuses(status):
    assert is_retryable_error(_HTTPError(status)) is False


def test_timeout_shaped_errors_are_retryable():
    class ConnectTimeout(Exception):
        pass

    class NetworkError(Exception):
        pass

    assert is_retryable_error(ConnectTimeout()) is True
    assert is_retryable_error(NetworkError()) is True
    assert is_retryable_error(ValueError("nope")) is False


# --- circuit breaker (injected clock) ----------------------------------------


def test_breaker_opens_after_threshold():
    clock = {"t": 0.0}
    bp = CircuitBreaker(threshold=5, cooldown_s=60.0, now=lambda: clock["t"])
    for _ in range(4):
        bp.record_failure()
    assert bp.is_open() is False  # 4 < 5
    bp.record_failure()  # 5th
    assert bp.is_open() is True


def test_breaker_cools_down_then_half_opens():
    clock = {"t": 0.0}
    bp = CircuitBreaker(threshold=3, cooldown_s=60.0, now=lambda: clock["t"])
    for _ in range(3):
        bp.record_failure()
    assert bp.is_open() is True
    clock["t"] = 59.9
    assert bp.is_open() is True  # still cooling
    clock["t"] = 60.0
    assert bp.is_open() is False  # cooldown elapsed → half-open, state reset
    # After half-open reset it takes another full threshold to re-open.
    for _ in range(2):
        bp.record_failure()
    assert bp.is_open() is False


def test_breaker_success_resets():
    bp = CircuitBreaker(threshold=2, now=lambda: 0.0)
    bp.record_failure()
    bp.record_success()
    bp.record_failure()
    assert bp.is_open() is False  # the success reset the count


# --- call_with_fallback ------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def test_primary_success_short_circuits():
    calls = []

    async def attempt(spec):
        calls.append(spec.model)
        return f"ok:{spec.model}"

    spec = _spec("brain_radar", "primary", fallbacks=(_spec("brain_cheap", "cheap"),))
    bp = CircuitBreaker(now=lambda: 0.0)
    assert _run(call_with_fallback(spec, attempt, breaker=bp)) == "ok:primary"
    assert calls == ["primary"]  # fallback never touched


def test_retryable_primary_falls_to_fallback():
    calls = []

    async def attempt(spec):
        calls.append(spec.model)
        if spec.model == "primary":
            raise _HTTPError(404)  # deprecated → retryable
        return f"ok:{spec.model}"

    spec = _spec("brain_radar", "primary", fallbacks=(_spec("brain_cheap", "cheap"),))
    bp = CircuitBreaker(now=lambda: 0.0)
    assert _run(call_with_fallback(spec, attempt, breaker=bp)) == "ok:cheap"
    assert calls == ["primary", "cheap"]


def test_non_retryable_is_reraised_without_fallback():
    calls = []

    async def attempt(spec):
        calls.append(spec.model)
        raise _HTTPError(400)  # bad request → deterministic, do NOT fall back

    spec = _spec("brain_radar", "primary", fallbacks=(_spec("brain_cheap", "cheap"),))
    with pytest.raises(_HTTPError):
        _run(call_with_fallback(spec, attempt))
    assert calls == ["primary"]


def test_exhausted_chain_raises_last_error():
    async def attempt(spec):
        raise _HTTPError(503)

    spec = _spec("brain_radar", "primary", fallbacks=(_spec("brain_cheap", "cheap"),))
    with pytest.raises(_HTTPError):
        _run(call_with_fallback(spec, attempt))


def test_open_breaker_skips_primary():
    calls = []

    async def attempt(spec):
        calls.append(spec.model)
        return f"ok:{spec.model}"

    spec = _spec("brain_radar", "primary", fallbacks=(_spec("brain_cheap", "cheap"),))
    bp = CircuitBreaker(threshold=1, now=lambda: 0.0)
    bp.record_failure()  # open it
    assert bp.is_open() is True
    assert _run(call_with_fallback(spec, attempt, breaker=bp)) == "ok:cheap"
    assert calls == ["cheap"]  # primary skipped while open

"""Does the RocketReach rate-limit guard actually fire, and only when it should?

The bug this covers: `gtm_core/circuit_breaker.py` was written for exactly this and
imported by nothing, so the worker had no 429 branch, no Retry-After handling, no
backoff and no breaker. A test that only asserted "no crash" would have passed against
that broken state too, so every case here pins an observable: how many HTTP requests
were issued, and how long we actually slept.

`asyncio.sleep` is patched out and RECORDED rather than stubbed to a no-op, so the
backoff durations themselves are assertions. Nothing here sleeps for real.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agent.mcp.rocketreach import resilience


@pytest.fixture(autouse=True)
def _reset_breaker():
    """The breaker is a process-shared singleton — isolate every test from the last."""
    resilience.reset()
    yield
    resilience.reset()


@pytest.fixture
def slept(monkeypatch):
    """Capture every sleep duration instead of serving it."""
    calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(resilience.asyncio, "sleep", _fake_sleep)
    return calls


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(coro):
    return asyncio.run(coro)


def _responder(statuses, headers=None, seen=None):
    """Serve `statuses` in order, recording each request."""
    seq = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        code = seq.pop(0) if seq else 200
        return httpx.Response(code, headers=(headers or {}), json={"id": 1, "status": "complete"})

    return handler


# --- 429 retry ---------------------------------------------------------------------


def test_429_is_retried_and_then_succeeds(slept):
    seen: list[str] = []

    async def go():
        async with _client(_responder([429, 200], seen=seen)) as c:
            return await resilience.request(c, "GET", "https://api.example/person/lookup")

    resp = _run(go())
    assert resp.status_code == 200
    assert len(seen) == 2, "a 429 must be retried, not surfaced immediately"
    assert slept == [5.0], f"first retry should use the 5s backoff base, got {slept}"


def test_retry_after_header_is_honoured_over_backoff(slept):
    async def go():
        async with _client(_responder([429, 200], headers={"Retry-After": "12"})) as c:
            return await resilience.request(c, "GET", "https://api.example/person/lookup")

    assert _run(go()).status_code == 200
    assert slept == [12.0], "the provider's own Retry-After must win over our backoff"


def test_hostile_retry_after_is_clamped(slept):
    """A huge Retry-After must not park a tool call for hours."""

    async def go():
        async with _client(_responder([429, 200], headers={"Retry-After": "99999"})) as c:
            return await resilience.request(c, "GET", "https://api.example/person/lookup")

    _run(go())
    assert slept == [30.0], f"expected clamp to _MAX_SLEEP_S, got {slept}"


def test_malformed_retry_after_falls_back_to_backoff(slept):
    """HTTP-date form (or junk) must degrade to exponential backoff, not crash."""

    async def go():
        async with _client(
            _responder([429, 200], headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        ) as c:
            return await resilience.request(c, "GET", "https://api.example/person/lookup")

    assert _run(go()).status_code == 200
    assert slept == [5.0]


def test_retry_budget_is_finite(slept):
    """Sustained 429 must raise, not loop forever. This is the exhaustion case."""
    seen: list[str] = []

    async def go():
        async with _client(_responder([429] * 10, seen=seen)) as c:
            return await resilience.request(c, "GET", "https://api.example/person/lookup")

    with pytest.raises(resilience.ProviderUnavailable):
        _run(go())
    assert len(seen) == resilience.MAX_429_RETRIES + 1 == 3, f"issued {len(seen)} requests"
    assert slept == [5.0, 10.0], f"backoff should double, got {slept}"


# --- circuit breaker ---------------------------------------------------------------


def test_breaker_opens_after_sustained_failure_then_refuses_without_calling(slept):
    """Once open, the guard must stop ISSUING REQUESTS — the whole point of the fix."""
    seen: list[str] = []

    async def hammer():
        async with _client(_responder([429] * 50, seen=seen)) as c:
            for _ in range(3):
                with pytest.raises(resilience.ProviderUnavailable):
                    await resilience.request(c, "GET", "https://api.example/person/lookup")

    _run(hammer())
    assert not resilience.available(), "breaker should be open after sustained 429s"

    before = len(seen)

    async def after_open():
        async with _client(_responder([200], seen=seen)) as c:
            with pytest.raises(resilience.ProviderUnavailable):
                await resilience.request(c, "GET", "https://api.example/person/lookup")

    _run(after_open())
    assert len(seen) == before, "an open breaker must issue NO further HTTP requests"
    assert "[rocketreach-unavailable]" in resilience.unavailable_message()


def test_isolated_429_that_then_succeeds_does_not_open_the_breaker(slept):
    """The negative control: transient blips must not trip a sustained-failure guard."""

    async def go():
        async with _client(_responder([429, 200])) as c:
            for _ in range(6):
                await resilience.request(c, "GET", "https://api.example/person/lookup")

    async def go_many():
        for _ in range(6):
            async with _client(_responder([429, 200])) as c:
                await resilience.request(c, "GET", "https://api.example/person/lookup")

    _run(go_many())
    assert resilience.available(), "a success must reset the consecutive-failure count"


def test_provider_unavailable_is_an_httpx_error():
    """server.py maps errors via `except httpx.HTTPError` — a refusal must land there,
    not escape as an unhandled exception and change existing behaviour."""
    assert issubclass(resilience.ProviderUnavailable, httpx.HTTPError)


# --- what must NOT change ----------------------------------------------------------


def test_5xx_is_not_retried_but_is_recorded(slept):
    """5xx keeps its existing single-shot behaviour; only 429 is retried."""
    seen: list[str] = []

    async def go():
        async with _client(_responder([503], seen=seen)) as c:
            return await resilience.request(c, "GET", "https://api.example/person/lookup")

    resp = _run(go())
    assert resp.status_code == 503, "5xx must be returned for the caller to raise on"
    assert len(seen) == 1, "5xx must not be retried"
    assert slept == []
    assert resilience.breaker().summary()["rocketreach"]["consecutive_failures"] == 1


def test_404_counts_as_provider_health_not_failure(slept):
    """A miss is an answer. Counting it as a failure would open the breaker on a list
    of people who simply are not in the database."""

    async def go():
        async with _client(_responder([404] * 8)) as c:
            for _ in range(8):
                await resilience.request(c, "GET", "https://api.example/person/lookup")

    _run(go())
    assert resilience.available(), "8 clean misses must not trip the breaker"


def test_network_error_records_failure_and_propagates_unchanged(slept):
    """server.py's `except httpx.HTTPError` mapping must keep working verbatim."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async def go():
        async with _client(boom) as c:
            return await resilience.request(c, "GET", "https://api.example/person/lookup")

    with pytest.raises(httpx.ConnectError):
        _run(go())
    assert resilience.breaker().summary()["rocketreach"]["consecutive_failures"] == 1


# --- pacing ------------------------------------------------------------------------


def test_pace_skips_the_first_item_and_spaces_the_rest(slept):
    async def go():
        for i in range(4):
            await resilience.pace(i)

    _run(go())
    assert slept == [resilience.PACE_S] * 3, f"expected 3 gaps across 4 items, got {slept}"


def test_pace_is_disabled_when_configured_to_zero(slept, monkeypatch):
    monkeypatch.setattr(resilience, "PACE_S", 0.0)
    _run(resilience.pace(5))
    assert slept == []


# --- bounded concurrency (bulk lookup) ----------------------------------------------


def test_gather_paced_keeps_input_order_when_items_finish_out_of_order(monkeypatch):
    """A bulk caller zips results back to the people it asked about, so order is identity."""
    monkeypatch.setattr(resilience, "PACE_S", 0.0)
    monkeypatch.setattr(resilience, "CONCURRENCY", 3)

    async def worker(delay: float) -> float:
        await asyncio.sleep(delay)
        return delay

    delays = [0.03, 0.0, 0.02, 0.01]
    assert _run(resilience.gather_paced(delays, worker)) == delays


@pytest.mark.parametrize("limit", [1, 3])
def test_gather_paced_never_exceeds_the_concurrency_limit(monkeypatch, limit):
    monkeypatch.setattr(resilience, "PACE_S", 0.0)
    monkeypatch.setattr(resilience, "CONCURRENCY", limit)
    in_flight = peak = 0

    async def worker(item: int) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.005)
        in_flight -= 1
        return item

    out = _run(resilience.gather_paced(list(range(10)), worker))
    assert out == list(range(10))
    assert peak == limit, f"expected {limit} in flight at peak, saw {peak}"


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 3), ("", 3), ("5", 5), ("1", 1), ("0", 1), ("-2", 1), ("x", 1)]
)
def test_concurrency_env_parses_safely(raw, expected):
    """A junk env value must degrade to sequential, never crash the worker at import."""
    assert resilience._concurrency(raw) == expected


def test_gather_paced_turns_a_raising_worker_into_an_error_row(monkeypatch):
    """Siblings may already have spent credit; the caller must still reach its metering."""
    monkeypatch.setattr(resilience, "PACE_S", 0.0)
    monkeypatch.setattr(resilience, "CONCURRENCY", 3)

    async def worker(item: int) -> dict:
        if item == 1:
            raise AttributeError("list has no .get")
        await asyncio.sleep(0)
        return {"ok": item}

    out = _run(resilience.gather_paced([0, 1, 2], worker))
    assert out == [{"ok": 0}, {"error": "lookup failed: AttributeError"}, {"ok": 2}]


def test_gather_paced_serialises_starts_even_with_parallel_slots(monkeypatch):
    """Parallel slots must not sleep their pace together and then fire as a burst.

    Real (tiny) sleeps and a monotonic clock: a recorded-sleep fake cannot tell three
    parallel 20ms sleeps from three serial ones, which is the whole bug.
    """
    import time

    monkeypatch.setattr(resilience, "PACE_S", 0.02)
    monkeypatch.setattr(resilience, "CONCURRENCY", 3)
    starts: list[float] = []

    async def worker(item: int) -> int:
        starts.append(time.monotonic())
        await asyncio.sleep(0.2)  # hold every slot, so only pacing separates the starts
        return item

    _run(resilience.gather_paced([0, 1, 2], worker))
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 0.015 for g in gaps), f"starts fired together: gaps {gaps}"

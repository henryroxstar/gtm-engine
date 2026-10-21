"""Rate-limit and outage resilience for the RocketReach worker.

WHY THIS EXISTS
---------------
``gtm_core/circuit_breaker.py`` was written to stop "timeouts, rate limits, 5xx
outages" from burning retries in the RocketReach -> Vibe -> Apollo -> Web waterfall.
**Nothing imported it.** On 2026-09-21 an enrichment run exhausted RocketReach's API
rate limit: the worker had no 429 branch, no ``Retry-After`` handling, no backoff and
no breaker, so it kept firing requests into a wall and the operator learned about it
from the provider, not from us. A declared contract nobody runs is not a contract.

Three properties of this worker make that limit easy to hit, and they compound:

* ``rocketreach_bulk_lookup`` is **not** a bulk API call. It is a server-side loop of
  the synchronous lookup — one request per person — because the native async bulk
  endpoint needs >=10 profiles plus a webhook receiver this deployment has no inbound
  path for. "Bulk" therefore saves agent round-trips, never provider quota. Anyone
  reasoning about rate limits from the tool *name* will get it wrong.
* Each unresolved lookup polls ``checkStatus`` up to :data:`MAX_POLLS` times, so a
  single 25-person batch can issue up to ``25 * (1 + 5) = 150`` requests.
* They went out back-to-back, with no pacing between people.

The monthly-allowance guard in ``server.py`` does not cover this: it meters the plan's
finite **lookup count**, which is a different limit from requests-per-minute, and it
fails open when ``GTM_PROFILE`` is unset.

WHAT THIS IS
------------
The one place that owns *how hard we may hit this provider*: the pacing constants, a
429-aware request wrapper, and the process-shared breaker. It is a deliberate split
rather than more lines in ``server.py`` — resilience policy is a self-contained unit,
which is the remedy §R10 prefers over a ceiling raise.

Stdlib + httpx only. It holds no tenant facts and never sees a credential: the caller
still builds its own headers and passes them through.

WHAT IT DOES NOT DO
-------------------
It does not retry 5xx or network errors. Those record a failure and propagate
unchanged, so ``server.py``'s existing ``except httpx.HTTPError`` mapping still turns
them into ``{"error": ...}`` exactly as before. Only 429 is retried, because only 429
carries a provider-supplied instruction about when to try again.
"""

from __future__ import annotations

import asyncio
import os

import httpx

from gtm_core.circuit_breaker import CircuitBreaker

SOURCE = "rocketreach"

# Poll budget while a record is still resolving. RocketReach returns status
# "searching"/"progress" with an id; we poll checkStatus until it completes or we give
# up (then the brain falls back). Kept small — the caller is human-paced. This is also
# the request amplifier named in the module docstring: budget 1 + MAX_POLLS per person.
MAX_POLLS = 5
POLL_INTERVAL_S = 2.0

# Bulk is a server-side loop of the synchronous lookup (see the docstring). Cap the
# loop so a bad call can't fan out — finalist sets are small by design.
BULK_MAX = 25

# Pause between people inside that loop. Small enough to be invisible on a 25-person
# batch (~6s), large enough that a batch is not a burst. 0 disables.
PACE_S = float(os.getenv("ROCKETREACH_PACE_S", "0.25"))


def _concurrency(raw: str | None) -> int:
    """Parse ``ROCKETREACH_CONCURRENCY``; junk or <1 falls back to 1 (sequential)."""
    try:
        return max(1, int(raw or 3))
    except ValueError:
        return 1


# How many lookups in a bulk call may be in flight at once. Each lookup spends most of its
# time waiting (a ``checkStatus`` poll every POLL_INTERVAL_S), so a strictly sequential loop
# leaves the provider's per-minute lookup allowance mostly unused. 3 stays far under the
# 10 requests/second global cap even with every slot polling; a 429 is still retried by
# ``request`` below. 1 restores the old one-at-a-time behaviour.
CONCURRENCY = _concurrency(os.getenv("ROCKETREACH_CONCURRENCY"))

# 429 is the only retried status: it is the one that tells us when to come back.
MAX_429_RETRIES = 2
_BACKOFF_BASE_S = 5.0
_MAX_SLEEP_S = 30.0  # never park a tool call longer than this on one retry

# Opens after this many CONSECUTIVE failures; a success resets the count, so isolated
# 429s that then succeed never trip it. Only a sustained wall does.
_FAILURE_THRESHOLD = 5
_RESET_TIMEOUT_S = 300.0

_BREAKER = CircuitBreaker(
    failure_threshold=_FAILURE_THRESHOLD,
    reset_timeout_s=_RESET_TIMEOUT_S,
)


class ProviderUnavailable(httpx.HTTPError):
    """The breaker is open, or 429s outlasted the retry budget.

    Subclasses ``httpx.HTTPError`` on purpose: every call site in ``server.py`` already
    catches that and maps it to an ``{"error": ...}`` dict, so introducing this type
    changes no existing behaviour — a refusal reads to the brain exactly like any other
    failed request, rather than escaping as an unhandled exception.
    """


def breaker() -> CircuitBreaker:
    """The process-shared breaker. Exposed for tests and for status reporting."""
    return _BREAKER


def reset() -> None:
    """Forget all recorded health. For tests only.

    The breaker is deliberately process-shared — that is what makes it work across tool
    calls in a long-lived worker. The same property leaks state between tests, where a
    5xx in one can open the circuit and fail an unrelated one later in the file, so any
    test touching this module resets it.
    """
    _BREAKER._sources.clear()


def available() -> bool:
    """False when the circuit is open — callers should refuse fast rather than loop."""
    return _BREAKER.is_available(SOURCE)


def unavailable_message() -> str:
    """Operator-readable reason, for a tool that is refusing up front."""
    st = _BREAKER.summary().get(SOURCE, {})
    return (
        f"[rocketreach-unavailable] circuit {st.get('state', 'open')} after "
        f"{st.get('consecutive_failures', '?')} consecutive failures (rate limit or outage). "
        f"It reopens for a probe after {int(_RESET_TIMEOUT_S)}s. "
        "Person/company SEARCH is unaffected; report the pause to the operator rather "
        "than retrying in a loop."
    )


async def pace(index: int = 1) -> None:
    """Sleep the inter-request pacing interval between items in a loop.

    ``index`` is the caller's position: 0 means "first item, nothing to space out from",
    so it does not wait. Taking the index here keeps the caller a single line, which
    matters because the loop it paces lives in a file pinned at its §R10 ceiling.
    No-op when ``PACE_S`` is 0.
    """
    if index and PACE_S > 0:
        await asyncio.sleep(PACE_S)


async def gather_paced(items: list, worker) -> list:
    """Run ``worker(item)`` for every item, at most :data:`CONCURRENCY` at once.

    Starts are serialised through :func:`pace` (a lock, so parallel slots cannot sleep
    together and then fire at once), so a batch never opens as a burst. Results come back
    in **input order** whatever order they finish in — a bulk caller zips them back to the
    people it asked about.

    A worker that raises becomes an ``{"error": ...}`` row instead of propagating. With
    requests in flight in sibling slots, an exception escaping here would skip the caller's
    metering while those siblings still spent provider credit (§R2).
    """
    gate = asyncio.Semaphore(CONCURRENCY)
    start = asyncio.Lock()

    async def one(index: int, item: object) -> object:
        async with gate:
            async with start:
                await pace(index)
            try:
                return await worker(item)
            except Exception as exc:  # noqa: BLE001 — see docstring: metering must still run
                return {"error": f"lookup failed: {type(exc).__name__}"}

    return list(await asyncio.gather(*(one(i, item) for i, item in enumerate(items))))


def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying a 429.

    Prefers the provider's own ``Retry-After`` (delta-seconds form) and falls back to
    exponential backoff. Always clamped to ``_MAX_SLEEP_S``: a hostile or malformed
    header must not be able to park a tool call indefinitely.
    """
    raw = (resp.headers.get("Retry-After") or "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), _MAX_SLEEP_S))
        except ValueError:
            pass  # HTTP-date form, or junk: fall through to backoff
    return min(_BACKOFF_BASE_S * (2**attempt), _MAX_SLEEP_S)


async def request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: object,
) -> httpx.Response:
    """Perform one request under the breaker, retrying only on 429.

    Returns the response **without** raising for status, so the caller keeps its own
    ``raise_for_status()`` and its own error mapping. Raises :class:`ProviderUnavailable`
    (an ``httpx.HTTPError``) when the circuit is open or the 429 budget is spent.
    Network errors propagate unchanged after being recorded.
    """
    if not available():
        raise ProviderUnavailable(unavailable_message())

    for attempt in range(MAX_429_RETRIES + 1):
        try:
            resp = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError:
            _BREAKER.record_failure(SOURCE)
            raise

        if resp.status_code == 429:
            _BREAKER.record_failure(SOURCE)
            if attempt >= MAX_429_RETRIES or not available():
                raise ProviderUnavailable(
                    f"[rocketreach-rate-limited] HTTP 429 after {attempt + 1} attempt(s). "
                    "Slow down or wait for the window to reset; do not retry in a loop."
                )
            await asyncio.sleep(_retry_after_seconds(resp, attempt))
            continue

        # 5xx means the provider is unhealthy; 4xx (e.g. a 404 miss) means it answered.
        if resp.status_code >= 500:
            _BREAKER.record_failure(SOURCE)
        else:
            _BREAKER.record_success(SOURCE)
        return resp

    raise ProviderUnavailable("[rocketreach-rate-limited] retry budget exhausted")

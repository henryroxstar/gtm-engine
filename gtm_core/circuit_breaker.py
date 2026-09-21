"""Circuit breaker pattern for external enrichment waterfall providers.

Prevents consecutive provider errors (timeouts, rate limits, 5xx outages)
from hanging the pipeline or burning retries in RocketReach -> Vibe -> Apollo -> Web flow.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CircuitBreakerState(StrEnum):
    """Lifecycle states of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _SourceHealth:
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_time: float | None = None
    tripped_at: float | None = None
    is_open: bool = False


class CircuitBreaker:
    """Manages availability states for external API tools."""

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout_s: float = 300.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._sources: dict[str, _SourceHealth] = {}

    def _get(self, source: str) -> _SourceHealth:
        if source not in self._sources:
            self._sources[source] = _SourceHealth()
        return self._sources[source]

    def state(self, source: str) -> CircuitBreakerState:
        """Return the current circuit state for a source."""
        health = self._get(source)
        if not health.is_open:
            return CircuitBreakerState.CLOSED

        now = time.time()
        if health.tripped_at is not None and (now - health.tripped_at) >= self.reset_timeout_s:
            return CircuitBreakerState.HALF_OPEN

        return CircuitBreakerState.OPEN

    def is_available(self, source: str) -> bool:
        """Check if calls to the source should be attempted."""
        current_state = self.state(source)
        return current_state in (CircuitBreakerState.CLOSED, CircuitBreakerState.HALF_OPEN)

    def record_success(self, source: str) -> None:
        """Record a successful call, closing the breaker if open/half-open."""
        health = self._get(source)
        health.total_successes += 1
        health.consecutive_failures = 0
        health.is_open = False
        health.tripped_at = None

    def record_failure(self, source: str) -> None:
        """Record a failed call, opening the breaker if threshold reached."""
        health = self._get(source)
        now = time.time()
        health.consecutive_failures += 1
        health.total_failures += 1
        health.last_failure_time = now

        current_state = self.state(source)
        if current_state == CircuitBreakerState.HALF_OPEN:
            # Probe failed in half-open -> immediately trip open again
            health.is_open = True
            health.tripped_at = now
        elif health.consecutive_failures >= self.failure_threshold:
            health.is_open = True
            health.tripped_at = now

    def summary(self) -> dict[str, dict[str, Any]]:
        """Return a structured summary of all tracked sources."""
        result = {}
        for src, health in self._sources.items():
            st = self.state(src)
            result[src] = {
                "state": st.value,
                "consecutive_failures": health.consecutive_failures,
                "total_failures": health.total_failures,
                "total_successes": health.total_successes,
                "tripped_at": health.tripped_at,
            }
        return result

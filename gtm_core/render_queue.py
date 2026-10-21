"""Render queue, status abstraction, and provider error normalization (10x Video PRD §6).

Provides:
- Provider-agnostic status normalization: queued, rendering, done, failed, timeout
- Normalized error taxonomy: insufficient_credits, rate_limited, content_policy, provider_error, timeout
- Debug logging of raw provider errors while shielding operator cockpit from vendor tracebacks
- Known flicker profile registry and shot duration advisories
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

NORMALIZED_STATUSES = ("queued", "rendering", "done", "failed", "timeout")
NORMALIZED_ERROR_CATEGORIES = (
    "insufficient_credits",
    "rate_limited",
    "content_policy",
    "provider_error",
    "timeout",
)

#: Known flicker threshold (seconds) by provider/model
KNOWN_FLICKER_REGISTRY: dict[str, float] = {
    "luma_dream_machine": 5.0,
    "pika_labs": 5.0,
}


def normalize_status(raw_status: str, provider: str = "") -> str:
    """Translate provider-specific statuses to the 5 normalized lifecycle states."""
    s = (raw_status or "").strip().lower()
    if s in ("queued", "waiting", "scheduled"):
        return "queued"
    if s in ("pending", "processing", "in_progress", "running", "rendering"):
        return "rendering"
    if s in ("done", "completed", "success", "succeeded"):
        return "done"
    if s in ("timeout", "timed_out", "deadline_exceeded"):
        return "timeout"
    if s in ("failed", "error", "rejected", "cancelled"):
        return "failed"
    return "rendering" if "process" in s else "queued"


def normalize_error(raw_error: str) -> str:
    """Map provider-specific error codes to the 5 normalized error categories."""
    raw = (raw_error or "").strip().lower()
    if any(k in raw for k in ("credit", "balance", "quota", "insufficient")):
        return "insufficient_credits"
    if any(k in raw for k in ("429", "rate", "throttle", "too many requests")):
        return "rate_limited"
    if any(k in raw for k in ("content", "flagged", "nsfw", "policy", "moderation", "safety")):
        return "content_policy"
    if any(k in raw for k in ("timeout", "timed out", "timed_out", "deadline")):
        return "timeout"
    return "provider_error"


def check_known_flicker(provider: str, duration_s: float) -> str | None:
    """Return an advisory string if duration_s exceeds provider's known flicker threshold."""
    key = provider.strip().lower()
    threshold = KNOWN_FLICKER_REGISTRY.get(key)
    if threshold is not None and duration_s > threshold:
        return (
            f"Provider {provider!r} is known to flicker on shots longer than "
            f"{threshold:.1f}s (shot duration: {duration_s:.1f}s); consider splitting."
        )
    return None


@dataclass
class QueueEntry:
    shot_id: str
    provider: str
    status: str = "queued"
    submitted_at: float = field(default_factory=time.time)
    credits_spent: int = 0
    raw_error: str | None = None
    normalized_error: str | None = None
    debug_log: list[str] = field(default_factory=list)

    @property
    def elapsed_s(self) -> float:
        return max(0.0, time.time() - self.submitted_at)

    def update_status(self, raw_status: str) -> str:
        self.status = normalize_status(raw_status, self.provider)
        return self.status

    def record_error(self, raw_error: str) -> str:
        self.raw_error = raw_error
        self.normalized_error = normalize_error(raw_error)
        self.status = "failed"
        log_line = f"[{self.provider}][shot:{self.shot_id}] raw_error={raw_error!r} -> normalized={self.normalized_error}"
        self.debug_log.append(log_line)
        logger.debug(log_line)
        return self.normalized_error

    def to_operator_summary(self) -> str:
        """Human-readable cockpit message shielding the operator from raw provider traces."""
        if self.status == "failed":
            return f"Shot {self.shot_id} failed: {self.normalized_error.replace('_', ' ').capitalize()}"
        return f"Shot {self.shot_id} — {self.status} ({self.elapsed_s:.1f}s)"


class RenderQueue:
    """Lightweight provider-agnostic render queue."""

    def __init__(self) -> None:
        self._entries: dict[str, QueueEntry] = {}

    def submit(self, shot_id: str, provider: str, credits: int = 0) -> QueueEntry:
        entry = QueueEntry(shot_id=shot_id, provider=provider, credits_spent=credits)
        self._entries[shot_id] = entry
        return entry

    def get(self, shot_id: str) -> QueueEntry | None:
        return self._entries.get(shot_id)

    def list_entries(self) -> list[QueueEntry]:
        return list(self._entries.values())

"""Headless signal queue service package."""

from __future__ import annotations

from .queue import (
    MAX_ATTEMPTS,
    claim_next_signal,
    complete_signal,
    enqueue_signal,
    fail_signal,
    get_signal,
)

__all__ = [
    "MAX_ATTEMPTS",
    "enqueue_signal",
    "claim_next_signal",
    "complete_signal",
    "fail_signal",
    "get_signal",
]

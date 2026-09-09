"""One word's measured start/end — the smallest shared value in this repo, deliberately alone.

It lives here rather than in :mod:`gtm_core.captions` (which is still its public home, and
re-exports it) for one reason: `captions` imports Pillow at module top, on purpose, and is
boundary-tested as one of three modules allowed to. :mod:`gtm_core.vo_timings` needs this
dataclass and nothing else from captions, and making a vendor-ingest CLI drag Pillow in for three
floats would be a cost with no argument behind it.

ONE definition, two names. Do not add a second dataclass with these fields somewhere convenient —
`captions.split_screens_from_word_timings` consumes this exact type, and a parallel definition
would typecheck, work, and then diverge.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WordTiming:
    """One word's real start/end, e.g. from Reap ``transcribe`` or HeyGen ``create_speech``.
    Supersedes the evenly-apportioned estimate :func:`gtm_core.captions.split_screens` produces
    from ``total_s`` alone."""

    word: str
    start_s: float
    end_s: float

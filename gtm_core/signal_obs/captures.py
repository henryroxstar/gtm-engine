"""Which captures of a source ``signal_obs`` will trust.

A capture dated more than a day ahead of today is ignored. The index is a file a colleague's
merge can write, so a forged or clock-skewed future date would otherwise pin "latest" to one
page forever and make a stale source look fresh.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from ..signal_sources import Capture, fetched_at_key, get_captures, read_tolerant, url_norm

#: A page bigger than this is not a member list; nothing is done with it (a hostile page is the
#: usual reason, and the cost of reading one grows with its size).
MAX_CAPTURE_CHARS = 2_000_000

#: Clock skew between two machines is hours, not days.
FUTURE_GRACE = datetime.timedelta(days=1)


def day_of(fetched_at: str) -> datetime.date | None:
    """The UTC calendar day a ``fetched_at`` stamp names, or ``None`` when it is not a date."""
    try:
        parsed = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC).date()


def usable(url: str, sources_dir: Path, today: datetime.date) -> list[Capture]:
    """Captures of ``url``, oldest first, without any dated after ``today`` plus the grace.

    When the newest row of the index has lost its page, the answer is "none", not the older
    capture: the list that page showed may differ, and an old one must not stand in for it.
    """
    limit = today + FUTURE_GRACE

    def fresh(stamp: str) -> bool:
        return (d := day_of(stamp)) is None or d <= limit

    found = [c for c in get_captures(url, sources_dir=sources_dir) if fresh(c.fetched_at)]
    rows = [
        r
        for r in read_tolerant(sources_dir)[0]
        if r.get("url_norm") == url_norm(url) and fresh(str(r.get("fetched_at", "")))
    ]
    if found and rows and max(rows, key=fetched_at_key).get("sha256") != found[-1].sha256:
        return []
    return found

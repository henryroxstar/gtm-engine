from __future__ import annotations

import datetime
import re

SIGNAL_MAX_AGE_DAYS = 210

_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_YEAR_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b")


def signal_age_limit(segment: str, kind: str) -> int:
    segment = (segment or "").strip().lower()
    if segment not in {"enterprise", "startup", "builder"}:
        segment = "unknown"

    if kind not in {"event", "funding", "structural"}:
        raise ValueError(f"unknown kind: {kind}")

    table = {
        "enterprise": {"event": 90, "funding": 90, "structural": 90},
        "startup": {"event": 210, "funding": 540, "structural": 210},
        "builder": {"event": 210, "funding": 540, "structural": 210},
        "unknown": {"event": 90, "funding": 90, "structural": 90},
    }
    return table[segment][kind]


def signal_latest_date(clause: str) -> datetime.date | None:
    found: list[datetime.date] = []
    for year, month, day in _ISO_DATE_RE.findall(clause or ""):
        try:
            found.append(datetime.date(int(year), int(month), int(day)))
        except ValueError:
            continue
    for name, year in _MONTH_YEAR_RE.findall(clause or ""):
        lowered = name.lower()
        month = _MONTH_NAMES.get(lowered[:4]) or _MONTH_NAMES.get(lowered[:3])
        if month:
            found.append(datetime.date(int(year), month, 1))
    return max(found) if found else None


def signal_is_fresh(
    clause: str,
    as_of: datetime.date | None = None,
    max_age_days: int = SIGNAL_MAX_AGE_DAYS,
) -> bool:
    latest = signal_latest_date(clause)
    if latest is None:
        return False
    today = as_of or datetime.date.today()
    age = (today - latest).days
    return 0 <= age <= max_age_days


def signal_recency_score(
    observed: datetime.date | datetime.datetime | str | None,
    as_of: datetime.date | None = None,
    why_now: str | None = None,
    max_age_days: int | None = None,
) -> float:
    """Compute signal recency decay score [0.0, 1.0] from signal_observed or why_now."""
    today = as_of or datetime.date.today()
    dt: datetime.date | None = None

    if isinstance(observed, datetime.datetime):
        dt = observed.date()
    elif isinstance(observed, datetime.date):
        dt = observed
    elif isinstance(observed, str) and observed.strip():
        cleaned = observed.strip().split("T")[0]
        try:
            dt = datetime.date.fromisoformat(cleaned)
        except ValueError:
            dt = None

    if dt is None and why_now:
        dt = signal_latest_date(why_now)

    if dt is None:
        return 0.0

    age = (today - dt).days
    if age < 0:
        return 0.0  # future dated signals fail-close
    if age <= 7:
        return 1.0  # Hot
    if age <= 30:
        return 0.8  # Warm
    if age <= 90:
        return 0.5  # Ambient
    if age <= 180:
        return 0.3  # Cool
    if max_age_days is not None and age <= max_age_days:
        return 0.1  # Cool / source-test window
    return 0.0  # Stale


def row_signal_freshness(
    row: dict,
    as_of: datetime.date | None = None,
    max_age_days: int | None = None,
) -> tuple[str, bool]:
    from .signal_clean import signal_clause

    research = str(row.get("why_now") or "")
    clause = signal_clause(research)
    observed = str(row.get("signal_observed") or "").strip()
    today = as_of or datetime.date.today()

    if max_age_days is None:
        if "segment" in row:
            raw_kind = str(row.get("signal_kind") or row.get("kind") or "").strip().lower()
            if raw_kind in ("funding", "event", "structural"):
                kind = raw_kind
            elif "raised" in research.lower() or "funding" in research.lower():
                kind = "funding"
            elif any(
                w in research.lower()
                for w in ("partner", "integration", "compliance", "standard", "stack", "regulator")
            ):
                kind = "structural"
            else:
                kind = "event"
            max_age_days = signal_age_limit(row.get("segment"), kind)
        else:
            max_age_days = SIGNAL_MAX_AGE_DAYS

    if observed:
        try:
            fresh = (today - datetime.date.fromisoformat(observed)).days <= max_age_days
        except ValueError:
            fresh = signal_is_fresh(research, as_of=as_of, max_age_days=max_age_days)
    else:
        fresh = signal_is_fresh(research, as_of=as_of, max_age_days=max_age_days)
    return clause, fresh

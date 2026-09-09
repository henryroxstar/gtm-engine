from __future__ import annotations

import datetime
import re

# --- signal freshness ----------------------------------------------------
#
# A SEPARATE concern from :func:`signal_clause`, which judges only whether a value is
# well-formed enough to render. A perfectly-formed clause can still be unsendable because
# it is OLD: touch 1 opens "Saw the news out of {{Company}}: {{Why Now}}." — a present-tense
# recency claim. Found 2026-07-29: of 22 clauses that passed the formatting gate, 9 were
# 8-18 months old and 5 asserted no date at all. "Saw the news out of Gears & Vectors:
# CoreWeave completed its $1.4B acquisition" about something 15 months past reads as
# automated, which is the exact opposite of what a signal is for.
#
# Kept out of ``signal_clause`` on purpose: that function is pure and deterministic, and
# freshness depends on when you ask. Callers pass ``as_of`` so tests pin it.

SIGNAL_MAX_AGE_DAYS = 210  # ~7 months; a funding round that old is history, not news

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


def signal_latest_date(clause: str) -> datetime.date | None:
    """Newest date the clause actually asserts, or ``None`` when it asserts none.

    Reads ISO (``2026-06-09``) and month-year (``May 2026``, ``Sept 2025``) forms, taking
    the NEWEST — a clause may cite both an event and a filing date, and recency is what
    the opener claims. A month-year resolves to the 1st, which is the conservative
    (oldest) reading of that month.

    ``None`` is not "fresh": an undated clause cannot support "saw the news" either, and
    :func:`signal_is_fresh` treats it as a failure.
    """
    found: list[datetime.date] = []
    for year, month, day in _ISO_DATE_RE.findall(clause or ""):
        try:
            found.append(datetime.date(int(year), int(month), int(day)))
        except ValueError:
            continue  # 2026-13-45 and friends: a real string, not a real date
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
    """Whether ``clause`` is recent enough to open on "saw the news".

    Fail-closed like the rest of this module: no date, an unparseable date, or a date
    older than ``max_age_days`` all return ``False``, which routes the row to the generic
    arc rather than making a recency claim the facts do not support. A future date also
    fails — that is bad research, not fresh news.
    """
    latest = signal_latest_date(clause)
    if latest is None:
        return False
    today = as_of or datetime.date.today()
    age = (today - latest).days
    return 0 <= age <= max_age_days


def row_signal_freshness(
    row: dict,
    as_of: datetime.date | None = None,
    max_age_days: int = SIGNAL_MAX_AGE_DAYS,
) -> tuple[str, bool]:
    """``(clause, fresh)`` for one list row — the ONE predicate the signal/generic split
    (``prospects_consolidate.split_by_signal``) and the lane router share, so they cannot
    disagree about which rows may open on "saw the news".

    Age is read from ``signal_observed`` first and only falls back to parsing a date out of
    the research text when the row has none. Both halves are load-bearing: the reduced
    clause has had its date stamp stripped (asking it answers "undated" for every
    well-dated row), and a ``why_now`` inherited from the account record is already a
    clean, date-free clause (asking the text demoted five verified boundary facts to
    generic on 2026-08-29). ``signal_observed`` is the ISO column created to make
    freshness checkable; read it first.
    """
    from .signal_clean import signal_clause

    research = str(row.get("why_now") or "")
    clause = signal_clause(research)
    observed = str(row.get("signal_observed") or "").strip()
    today = as_of or datetime.date.today()
    if observed:
        try:
            fresh = (today - datetime.date.fromisoformat(observed)).days <= max_age_days
        except ValueError:
            fresh = signal_is_fresh(research, as_of=as_of, max_age_days=max_age_days)
    else:
        fresh = signal_is_fresh(research, as_of=as_of, max_age_days=max_age_days)
    return clause, fresh

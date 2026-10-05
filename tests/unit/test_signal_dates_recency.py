import datetime

import pytest

from gtm_core.merge_hygiene.signal_dates import signal_recency_score


@pytest.mark.parametrize(
    "days_ago,expected_score",
    [
        (0, 1.0),
        (7, 1.0),
        (8, 0.8),
        (30, 0.8),
        (31, 0.5),
        (90, 0.5),
        (91, 0.3),
        (180, 0.3),
        (181, 0.0),
        (365, 0.0),
        (-1, 0.0),
    ],
)
def test_signal_recency_score_decay_boundaries(days_ago: int, expected_score: float) -> None:
    today = datetime.date(2026, 10, 2)
    observed = today - datetime.timedelta(days=days_ago)
    score = signal_recency_score(observed, as_of=today)
    assert score == expected_score


def test_signal_recency_score_datetime_normalization() -> None:
    today = datetime.date(2026, 10, 2)
    dt = datetime.datetime(2026, 10, 2, 14, 30, 0)
    assert signal_recency_score(dt, as_of=today) == 1.0


def test_signal_recency_score_iso_string() -> None:
    today = datetime.date(2026, 10, 2)
    assert signal_recency_score("2026-10-02T15:30:00Z", as_of=today) == 1.0
    assert signal_recency_score("2026-09-20", as_of=today) == 0.8


def test_signal_recency_score_fallback_to_why_now() -> None:
    today = datetime.date(2026, 10, 2)
    # Observed is empty or invalid, fallback extracts date from why_now
    why_now = "Announced new cross-org security model on 2026-09-15 in press release"
    # 2026-10-02 - 2026-09-15 = 17 days -> 0.8
    assert signal_recency_score("", as_of=today, why_now=why_now) == 0.8
    assert signal_recency_score(None, as_of=today, why_now=why_now) == 0.8
    assert signal_recency_score("invalid-date", as_of=today, why_now=why_now) == 0.8


def test_signal_recency_score_fail_closed() -> None:
    today = datetime.date(2026, 10, 2)
    assert signal_recency_score("", as_of=today) == 0.0
    assert signal_recency_score(None, as_of=today) == 0.0
    assert signal_recency_score("not-a-date", as_of=today) == 0.0
    assert signal_recency_score(None, as_of=today, why_now="no date in this text") == 0.0

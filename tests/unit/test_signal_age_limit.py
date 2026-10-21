import pytest

from gtm_core.merge_hygiene.signal_dates import signal_age_limit


def test_signal_age_limit():
    assert signal_age_limit("enterprise", "event") == 90
    assert signal_age_limit("enterprise", "funding") == 90
    assert signal_age_limit("enterprise", "structural") == 90

    assert signal_age_limit("startup", "event") == 210
    assert signal_age_limit("startup", "funding") == 540
    assert signal_age_limit("startup", "structural") == 210

    assert signal_age_limit("builder", "event") == 210
    assert signal_age_limit("builder", "funding") == 540
    assert signal_age_limit("builder", "structural") == 210

    # unknown / blank fallback to strictest (90)
    assert signal_age_limit("", "event") == 90
    assert signal_age_limit(None, "funding") == 90
    assert signal_age_limit("foo", "structural") == 90

    with pytest.raises(ValueError):
        signal_age_limit("startup", "magic")

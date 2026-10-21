"""The test-scoped `--signal-age-days` override: bounded, and never silent in the report."""

from __future__ import annotations

import pytest

from gtm_core.account_integrity import AccountAudit, main, render

BASE = ["--csv", "unused.csv", "--profile", "p", "--lane", "personalised"]


@pytest.mark.parametrize("days", ["0", "-1", "366", "99999", "ninety"])
def test_an_out_of_range_override_is_refused_before_anything_runs(days):
    with pytest.raises(SystemExit) as exc:
        main([*BASE, "--signal-age-days", days])
    assert exc.value.code == 2


def test_the_report_names_the_override_when_one_is_in_force():
    assert "365 days" in render(AccountAudit(rows=1, accounts=1, signal_age_days=365))
    assert "signal age limit overridden" not in render(AccountAudit(rows=1, accounts=1))

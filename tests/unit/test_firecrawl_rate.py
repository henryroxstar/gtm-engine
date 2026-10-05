"""R0.4: the Firecrawl rate lives in gtm_core/credit_rates.py, dated, and reproduces the measured row."""

from __future__ import annotations

from gtm_core import credit_rates


def test_rate_reproduces_the_one_measured_ledger_row():
    # A hand-logged row in a tenant ledger: 60 credits = $0.30.
    assert credit_rates.firecrawl_credits_to_usd(60) == 0.3


def test_rate_is_dated_and_marked_unconfirmed():
    assert credit_rates.FIRECRAWL_RATE_VERIFIED_ON
    assert credit_rates.FIRECRAWL_RATE_BASIS == "derived-from-ledger-rows"


def test_conversion_rounds_to_the_ledgers_six_places():
    assert credit_rates.firecrawl_credits_to_usd(1) == round(
        credit_rates.FIRECRAWL_USD_PER_CREDIT, 6
    )
    assert credit_rates.firecrawl_credits_to_usd(0) == 0.0


def test_a_search_is_estimated_per_block_of_ten_results_with_a_floor_of_one_block():
    from gtm_core import credit_rates

    per = credit_rates.FIRECRAWL_SEARCH_CREDITS_PER_TEN_RESULTS
    assert credit_rates.firecrawl_search_credits_estimate(0) == per
    assert credit_rates.firecrawl_search_credits_estimate(1) == per
    assert credit_rates.firecrawl_search_credits_estimate(10) == per
    assert credit_rates.firecrawl_search_credits_estimate(11) == 2 * per
    assert credit_rates.firecrawl_search_credits_estimate(25) == 3 * per

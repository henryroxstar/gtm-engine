"""Tests for gtm_core.commercial — quotes derived from a profile's pricing pack."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from gtm_core.commercial import (
    QuoteError,
    load_pricing,
    main,
    price_keys,
    pricing_path,
    quote,
)
from gtm_core.paths import resolve_profiles_root

PACK = """
[meta]
source = "fictional rate card"
as_of = "2026-01-01"
currency = "USD"
tax_note = "All fees exclude sales tax."

[tiers.starter]
price_month = 100
appliances = 1
appliances_max = 5
self_service = true

[tiers.scale]
price_month_from = 900
appliances = 5

[addons]
widget_appliance = 100

[limits.widget]
proxies_total = 5

[partner.revenue_share]
partner_sourced = 0.35
floor_month = 500

[open_questions.list_price]
question = "Is it 100 or 101?"
evidence = "two papers disagree"
status = "OPEN"
"""


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "profiles"
    path = pricing_path(root, "acme")
    path.parent.mkdir(parents=True)
    path.write_text(PACK)
    return load_pricing(path)


@pytest.fixture
def root(tmp_path, data):
    return tmp_path / "profiles"


def test_monthly_term_and_year1_totals(data):
    q = quote(data, "tiers.starter.price_month", units=3, term_months=24)
    assert q.monthly_total == Decimal(300)
    assert q.term_total == Decimal(7200)
    assert q.year1_total == Decimal(3600)  # year 1 caps at 12 months
    assert q.net_year1 == Decimal(3600)
    assert not q.overridden


def test_short_term_year1_equals_term_total(data):
    q = quote(data, "addons.widget_appliance", term_months=6)
    assert q.year1_total == q.term_total == Decimal(600)


def test_outbound_value_drives_net_position_negative(data):
    q = quote(
        data, "addons.widget_appliance", outbound=Decimal(5000), outbound_label="research grant"
    )
    assert q.year1_total == Decimal(1200)
    assert q.net_year1 == Decimal(-3800)


def test_outbound_without_label_is_refused(data):
    with pytest.raises(QuoteError, match="outbound-label"):
        quote(data, "addons.widget_appliance", outbound=Decimal(5000))


def test_negative_outbound_is_refused(data):
    with pytest.raises(QuoteError, match="negative"):
        quote(data, "addons.widget_appliance", outbound=Decimal(-1), outbound_label="x")


def test_override_needs_a_reason(data):
    with pytest.raises(QuoteError, match="price-reason"):
        quote(data, "tiers.starter.price_month", price=Decimal(101))


def test_override_with_reason_keeps_list_price_visible(data):
    q = quote(data, "tiers.starter.price_month", price=Decimal(101), price_reason="agreed verbally")
    assert q.overridden
    assert (q.list_price, q.unit_price) == (Decimal(100), Decimal(101))
    assert q.year1_total == Decimal(1212)


def test_override_equal_to_list_is_not_an_override(data):
    q = quote(data, "tiers.starter.price_month", price=Decimal(100))
    assert not q.overridden and q.price_reason is None


# The discrimination tests (§R18): numeric is not the same as priceable. A resource limit,
# an appliance count or a revenue-share ratio multiplied by months is a confident wrong number.
@pytest.mark.parametrize(
    "key",
    [
        "limits.widget.proxies_total",
        "tiers.starter.appliances",
        "tiers.starter.appliances_max",
        "partner.revenue_share.partner_sourced",
    ],
)
def test_numeric_non_price_keys_are_refused(data, key):
    with pytest.raises(QuoteError, match="not a monthly price"):
        quote(data, key)


def test_boolean_is_not_numeric(data):
    with pytest.raises(QuoteError, match="unknown price key"):
        quote(data, "tiers.starter.self_service")


def test_unknown_key_is_refused(data):
    with pytest.raises(QuoteError, match="unknown price key"):
        quote(data, "tiers.nope.price_month")


@pytest.mark.parametrize(("units", "term"), [(0, 12), (1, 0), (-2, 12)])
def test_non_positive_units_or_term_refused(data, units, term):
    with pytest.raises(QuoteError, match="at least 1"):
        quote(data, "addons.widget_appliance", units=units, term_months=term)


def test_from_price_is_flagged(data):
    assert quote(data, "tiers.scale.price_month_from").basis == "from"


def test_price_keys_lists_only_prices(data):
    assert set(price_keys(data)) == {
        "tiers.starter.price_month",
        "tiers.scale.price_month_from",
        "addons.widget_appliance",
        "partner.revenue_share.floor_month",
    }


def test_open_questions_travel_with_the_quote(data):
    assert quote(data, "addons.widget_appliance").open_questions == ["list_price"]


def test_profile_traversal_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsafe profile"):
        pricing_path(tmp_path, "../other")


def test_cli_quote_json(root, capsys):
    rc = main(
        [
            "quote",
            "--profile",
            "acme",
            "--profiles-root",
            str(root),
            "--price-key",
            "addons.widget_appliance",
            "--price",
            "101",
            "--price-reason",
            "rounded",
            "--outbound",
            "2000",
            "--outbound-label",
            "grant",
            "--json",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["year1_total"] == "1212"
    assert out["net_year1"] == "-788"
    assert out["overridden"] is True


def test_cli_error_exits_2(root, capsys):
    rc = main(
        [
            "quote",
            "--profile",
            "acme",
            "--profiles-root",
            str(root),
            "--price-key",
            "limits.widget.proxies_total",
        ]
    )
    assert rc == 2
    assert "not a monthly price" in capsys.readouterr().err


def test_cli_missing_pack_exits_2(tmp_path, capsys):
    assert main(["keys", "--profile", "ghost", "--profiles-root", str(tmp_path)]) == 2


# Real-data properties, over whichever tenants ship a pricing pack (none named here, so the
# test carries no tenant identity into the public carve and skips where there is no data).
def _real_packs():
    root = resolve_profiles_root()
    return sorted(root.glob("*/knowledge/commercial/pricing.toml")) if root.is_dir() else []


@pytest.mark.parametrize(
    "path", _real_packs() or [None], ids=lambda p: p.parts[-4] if p else "none"
)
def test_real_pricing_packs_are_quotable(path):
    if path is None:
        pytest.skip("no tenant pricing pack present")
    data = load_pricing(path)
    meta = data["meta"]
    date.fromisoformat(meta["as_of"])
    assert meta["currency"] and meta["source"]
    prices = price_keys(data)
    assert prices, "a pricing pack with no prices"
    for key, amount in prices.items():
        assert amount >= 0, key
        assert quote(data, key).year1_total == amount * 12
    for qid, q in data.get("open_questions", {}).items():
        assert q.get("question") and q.get("status"), f"open question {qid} lacks question/status"
    for tier, spec in data.get("tiers", {}).items():
        assert "price_month" in spec or "price_month_from" in spec, f"tier {tier} has no price"
    # revenue_share mixes rate fractions with metadata (source, floor_month, status, ...);
    # the "*_sourced" suffix is the convention for a fraction, whichever party earns it.
    share = data.get("partner", {}).get("revenue_share", {})
    for key, val in share.items():
        if key.endswith("_sourced"):
            assert 0 < val <= 1, key

"""What the live RevenueCat channel will and will not credit (MM15, MM16, MM17).

The webhook stays the payment channel until the external billing service's top-up channel ships, so its rules have to
hold on **both** routes that reach ``apply_wallet_top_up``: ``POST /v1/webhooks/revenuecat`` and
the generic ``POST /v1/webhooks/{provider}/{workspace_id}`` with ``provider == "revenuecat"``.
Every route test here is parametrised over the pair, so fixing one and forgetting the other is a
red test, not a review comment.

* MM15 -- a product that is not a named credit pack credits nothing. The amount comes from the
  product id alone; ``price_in_purchased_currency`` is in the buyer's currency, not dollars, so
  it is never read (a 1000-yen purchase used to credit $1000).
* MM17 -- only ``NON_RENEWING_PURCHASE`` funds the wallet. A subscription's allowance arrives
  through ``PUT /v1/entitlement``; crediting ``INITIAL_PURCHASE`` / ``RENEWAL`` as well funded a
  subscriber twice.
* MM16 -- ``apply_wallet_top_up`` answers ``new | duplicate | unknown``; an ``unknown``
  workspace is a typed 404 with a warning naming the event, never a 500 and never a credit. The
  real foreign-key behaviour is in ``test_wallet_top_up_live.py`` -- a mocked pool cannot see it.

``apply_wallet_top_up`` is patched here, so these tests prove the *rules* and the *mapping from
outcome to response*, not what Postgres does with the write.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.errors import register_error_handlers
from backend.routers import webhooks

_WS = "11111111-2222-4333-8444-555555555555"
_SECRET = "rc_test_secret_topup_rules"  # nosec B105 -- throwaway test value
_AUTH = {"Authorization": f"Bearer {_SECRET}"}

#: Both routes that can reach ``apply_wallet_top_up``.
ROUTES = {
    "revenuecat": "/v1/webhooks/revenuecat",
    "generic": f"/v1/webhooks/revenuecat/{_WS}",
}
BOTH_ROUTES = pytest.mark.parametrize("route", ROUTES.values(), ids=ROUTES.keys())


def _payload(ev_type: str, product_id: str | None, *, event_id: str = "rc_evt_1", **extra) -> dict:
    event = {"id": event_id, "type": ev_type, "app_user_id": _WS, **extra}
    if product_id is not None:
        event["product_id"] = product_id
    return {"api_version": "1.0", "event": event}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", _SECRET)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(webhooks.router, prefix="/v1")
    app.state.pool = MagicMock()
    return TestClient(app)


@pytest.fixture
def top_up():
    """The wallet write, patched. ``await_count == 0`` is "the wallet was not touched"."""
    with patch.object(webhooks, "apply_wallet_top_up", AsyncMock(return_value="new")) as fake:
        yield fake


@pytest.fixture
def inbound_run(monkeypatch):
    """The inbound-reply path, patched, so a RevenueCat event that wrongly reaches it is seen."""
    monkeypatch.setattr(webhooks, "_recent_events", {})  # a real enqueue records into this global
    enqueue = AsyncMock(return_value=("run-1", False))
    with (
        patch.object(webhooks, "_enqueue_inbound_run", enqueue),
        patch.object(webhooks, "_resolve_workspace_profile", AsyncMock(return_value="p")),
        patch.object(webhooks, "_check_dedup", AsyncMock(return_value=None)),
    ):
        yield enqueue


def _post(client, route, body):
    return client.post(route, headers=_AUTH, json=body)


# -- MM15: the amount comes from the product id and from nothing else ----------------------


@pytest.mark.parametrize(
    ("product_id", "usd"),
    [
        ("credits_10_usd", 10.0),
        ("credits_25_usd", 25.0),
        ("credits_50_usd", 50.0),
        ("  CREDITS_25_USD ", 25.0),
    ],
)
def test_a_named_credit_pack_maps_to_its_dollar_amount(product_id, usd):
    """The positive control for every refusal below: the mapper is not simply returning 0."""
    assert webhooks._map_revenuecat_product_credits(product_id) == usd


@BOTH_ROUTES
@pytest.mark.parametrize("product_id", ["gems_pack_large", "", None])
def test_an_unmapped_product_credits_nothing_whatever_it_cost(client, top_up, route, product_id):
    """A 1000-yen purchase of a product we do not sell as credits used to credit $1000."""
    body = _payload(
        "NON_RENEWING_PURCHASE",
        product_id,
        price_in_purchased_currency=1000,
        currency="JPY",
    )
    resp = _post(client, route, body)

    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert top_up.await_count == 0


@BOTH_ROUTES
def test_a_purchase_of_an_unmapped_product_is_loudly_unattributed_not_silently_ignored(
    client, top_up, route, caplog
):
    """A new credit-pack SKU that nobody mapped takes the customer's money and credits nothing; the
    only trace must be findable by the event id, as it is for an unknown workspace (MM16)."""
    body = _payload("NON_RENEWING_PURCHASE", "credits_new_pack", event_id="rc_evt_new_sku_9")
    with caplog.at_level(logging.WARNING, logger=webhooks.log.name):
        resp = _post(client, route, body)

    assert resp.json()["status"] == "ignored"
    assert top_up.await_count == 0
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("rc_evt_new_sku_9" in m and "credits_new_pack" in m for m in warnings), warnings


@BOTH_ROUTES
def test_a_renewal_of_an_unmapped_product_is_quietly_ignored_because_it_never_funds_a_wallet(
    client, top_up, route, caplog
):
    """The control for the warning above: only an event that WOULD have credited warns."""
    with caplog.at_level(logging.WARNING, logger=webhooks.log.name):
        _post(client, route, _payload("RENEWAL", "gems_pack_large"))
    assert not [r for r in caplog.records if "NOT credited" in r.getMessage()]


@BOTH_ROUTES
@pytest.mark.parametrize("product_id", ["promo_credits_500_usd", "credits_500_usd_trial"])
def test_a_product_id_that_only_contains_a_pack_name_is_unmapped(client, top_up, route, product_id):
    """A pack is the whole id. Matching ``credits_<n>_usd`` anywhere inside a longer id would
    credit $500 for a product nobody listed."""
    resp = _post(client, route, _payload("NON_RENEWING_PURCHASE", product_id))

    assert resp.json()["status"] == "ignored"
    assert top_up.await_count == 0


@BOTH_ROUTES
def test_a_subscription_product_never_credits_even_as_a_one_off(client, top_up, route):
    """``pro_monthly_<n>_usd`` names a subscription, whose allowance is the entitlement sync's.
    Reading a dollar figure out of its id is the double-funding MM17 closes from the other side."""
    resp = _post(client, route, _payload("NON_RENEWING_PURCHASE", "pro_monthly_50_usd"))

    assert resp.json()["status"] == "ignored"
    assert top_up.await_count == 0


@BOTH_ROUTES
def test_a_named_credit_pack_is_credited_in_credits_at_a_thousand_per_dollar(client, top_up, route):
    """The positive control for the MM15 and MM17 refusals: a real purchase still credits."""
    resp = _post(
        client, route, _payload("NON_RENEWING_PURCHASE", "credits_25_usd", event_id="rc_evt_25")
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    top_up.assert_awaited_once_with(
        pool=ANY,
        workspace_id=_WS,
        amount_usd=25.0,
        provider="revenuecat_stripe",
        external_transaction_id="rc_evt_25",
        credits=25000.0,
    )


# -- MM17: only a one-off purchase funds the wallet -----------------------------------------


@BOTH_ROUTES
@pytest.mark.parametrize(
    "ev_type",
    [
        "INITIAL_PURCHASE",
        "RENEWAL",
        "CANCELLATION",
        "EXPIRATION",
        "BILLING_ISSUE",
        "wharrgarbl",
        "",
    ],
)
def test_only_a_non_renewing_purchase_funds_the_wallet(client, top_up, route, ev_type):
    """A subscriber is already funded by ``PUT /v1/entitlement``; a second credit from the same
    purchase is a free top-up. The granting value is a closed list of one."""
    resp = _post(client, route, _payload(ev_type, "credits_25_usd"))

    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert top_up.await_count == 0


@pytest.mark.parametrize(
    "ev_type",
    ["INITIAL_PURCHASE", "RENEWAL", "CANCELLATION", "TEST", "NON_RENEWING_PURCHASE"],
)
def test_a_revenuecat_event_never_starts_an_inbound_run(client, top_up, inbound_run, ev_type):
    """A RevenueCat payload is never a reply. The generic route's reply check is a substring test
    over the stringified payload, so an event that merely *carries* the word reached the inbound
    path -- every event here does, and the last one is unmapped so it credits nothing either."""
    body = _payload(
        ev_type,
        "gems_pack_large" if ev_type == "NON_RENEWING_PURCHASE" else "credits_25_usd",
        subscriber_attributes={"note": {"value": "please reply to this"}},
    )
    resp = _post(client, ROUTES["generic"], body)

    assert resp.status_code == 200
    assert inbound_run.await_count == 0


def test_the_generic_route_still_enqueues_a_real_reply(client, top_up, inbound_run, monkeypatch):
    """The instrument for the test above: the patched inbound path *is* reachable from this
    route, so ``await_count == 0`` there is a result and not an artefact of the harness."""
    monkeypatch.setenv("SALESHANDY_WEBHOOK_SECRET", _SECRET)
    body = {"event": "reply", "event_id": "evt-1", "reply_category": "interested", "body": "hi"}
    resp = client.post(f"/v1/webhooks/saleshandy/{_WS}", headers=_AUTH, json=body)

    assert resp.status_code == 202
    assert inbound_run.await_count == 1
    assert top_up.await_count == 0


# -- MM16: the outcome of the write decides the response ------------------------------------


@BOTH_ROUTES
def test_a_new_top_up_is_a_200_ok(client, top_up, route):
    top_up.return_value = "new"
    resp = _post(client, route, _payload("NON_RENEWING_PURCHASE", "credits_10_usd"))

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@BOTH_ROUTES
def test_a_replayed_top_up_is_a_200_duplicate(client, top_up, route):
    top_up.return_value = "duplicate"
    resp = _post(client, route, _payload("NON_RENEWING_PURCHASE", "credits_10_usd"))

    assert resp.status_code == 200
    assert resp.json()["status"] == "duplicate"


@BOTH_ROUTES
def test_a_top_up_for_a_workspace_that_does_not_exist_is_a_404_with_a_warning(
    client, top_up, route, caplog
):
    """The payment cannot be attributed, so it must be *visibly* unattributed: a typed refusal the
    caller can retry, and a warning an operator can find by the event id. Not a 200 (which says
    "handled"), not a 500."""
    top_up.return_value = "unknown"
    body = _payload("NON_RENEWING_PURCHASE", "credits_10_usd", event_id="rc_evt_orphan_77")

    with caplog.at_level(logging.WARNING, logger=webhooks.log.name):
        resp = _post(client, route, body)

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "workspace_not_found"
    assert top_up.await_count == 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("rc_evt_orphan_77" in m and _WS in m for m in warnings), warnings


# -- the primitive refuses what is not a credit ---------------------------------------------


@pytest.mark.parametrize("amount", [0, 0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_a_top_up_of_no_money_or_a_non_number_is_a_caller_bug_not_a_duplicate(amount):
    """It used to answer ``False`` -- "duplicate" to every caller -- which told a payment
    processor the credit was already handled. Nothing is written."""
    with patch.object(webhooks, "workspace_scope") as scope:
        with pytest.raises(ValueError, match="amount_usd"):
            asyncio.run(webhooks.apply_wallet_top_up(MagicMock(), _WS, amount, "p", "tx-1"))

    scope.assert_not_called()


def test_a_top_up_with_no_pool_is_an_error_not_a_duplicate():
    with patch.object(webhooks, "workspace_scope") as scope:
        with pytest.raises(RuntimeError, match="pool"):
            asyncio.run(webhooks.apply_wallet_top_up(None, _WS, 10.0, "p", "tx-1"))

    scope.assert_not_called()

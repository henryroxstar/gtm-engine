"""MM7: exactly ONE way to credit a wallet at a time — the RevenueCat webhook, or the billing service's top-up route.

Two live channels crediting the same payment is a double credit; two shut ones is a payment that credits
nothing. ``BILLING_SERVICE_ENABLED`` (default off) picks the channel, and the rule that matters is the
one a mistyped value could break: anything that is not an unambiguous "on" leaves the webhook live and the
new route shut, so no value of the setting can ever open both.

Pinned here, each with its twin:

* for EVERY value of the setting (unset, empty, ``0``/``false``/``no``/``off``, ``1``/``true``/``yes``/``on``
  in any case and padding, garbage), a purchase credits through exactly one channel;
* the channels are ENUMERATED from the code, not hand-listed: every function that can call
  ``apply_wallet_top_up`` must consult the switch, so a third route added later fails here until it does;
* the top-up route is bounded (``0 < amount <= 500``, finite) in the HANDLER — a pydantic constraint would
  422 before any handler code and the refusal could not be audited — idempotent on ``order_id``, and
  authenticated exactly as ``PUT /v1/entitlement`` is: the shared service secret, never a user token.

(The live-database half — the wallet row, the ledger row, the audit file — is
``test_wallet_top_up_route_live.py``.)
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.errors import register_error_handlers
from backend.routers import wallet, webhooks

_WS = "11111111-2222-4333-8444-555555555555"
_RC_SECRET = "rc_test_secret_channel"  # nosec B105 -- throwaway test value
_SVC_SECRET = "svc_test_secret_channel"  # nosec B105 -- throwaway test value
_REPO = Path(__file__).resolve().parents[2]

SETTING = "BILLING_SERVICE_ENABLED"
ON = ["1", "true", "TRUE", " True ", "yes", "on", "ON"]
OFF = [
    None,
    "",
    " ",
    "0",
    "false",
    "False",
    "no",
    "off",
    "2",
    "enabled",
    "garbage",
    "tru",
    "1.0",
    "-1",
]


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", _RC_SECRET)
    monkeypatch.setenv("BILLING_SYNC_SECRET", _SVC_SECRET)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(webhooks.router, prefix="/v1")
    app.include_router(wallet.router, prefix="/v1")
    app.state.pool = MagicMock()
    return TestClient(app)


@pytest.fixture
def top_up():
    with patch.object(webhooks, "apply_wallet_top_up", AsyncMock(return_value="new")) as fake:
        yield fake


def _set(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(SETTING, raising=False)
    else:
        monkeypatch.setenv(SETTING, value)


def _purchase(event_id="rc_evt_1"):
    return {
        "api_version": "1.0",
        "event": {
            "id": event_id,
            "type": "NON_RENEWING_PURCHASE",
            "app_user_id": _WS,
            "product_id": "credits_25_usd",
        },
    }


def _channels(client) -> dict[str, int]:
    """Make the SAME purchase through every channel; return the HTTP status each answered."""
    rc = {"Authorization": f"Bearer {_RC_SECRET}"}
    return {
        "revenuecat": client.post(
            "/v1/webhooks/revenuecat", headers=rc, json=_purchase()
        ).status_code,
        "generic": client.post(
            f"/v1/webhooks/revenuecat/{_WS}", headers=rc, json=_purchase()
        ).status_code,
        "top_up": client.post(
            f"/v1/wallet/{_WS}/top-ups",
            headers={"Authorization": _SVC_SECRET},
            json={"amount_usd": 25.0, "order_id": "ord_1"},
        ).status_code,
    }


# ── the switch: every value, exactly one channel ──────────────────────────────


@pytest.mark.parametrize("value", OFF)
def test_any_value_that_is_not_an_unambiguous_on_leaves_the_webhook_live_and_the_route_shut(
    app_client, top_up, monkeypatch, value
):
    _set(monkeypatch, value)
    status = _channels(app_client)
    assert status["revenuecat"] == 200 and status["generic"] == 200
    assert status["top_up"] != 200
    providers = [c.kwargs.get("provider") or c.args[3] for c in top_up.await_args_list]
    assert providers and all(p.startswith("revenuecat") for p in providers)


@pytest.mark.parametrize("value", ON)
def test_an_unambiguous_on_shuts_the_webhook_and_opens_the_route(
    app_client, top_up, monkeypatch, value
):
    _set(monkeypatch, value)
    status = _channels(app_client)
    assert status["top_up"] == 200
    assert status["revenuecat"] != 200 and status["generic"] != 200
    providers = [c.kwargs.get("provider") or c.args[3] for c in top_up.await_args_list]
    assert providers == ["billing_service"]  # one credit, and from the one channel that is open


@pytest.mark.parametrize("value", [*OFF, *ON])
def test_no_value_ever_credits_through_both_channels(app_client, top_up, monkeypatch, value):
    _set(monkeypatch, value)
    _channels(app_client)
    kinds = {
        "webhook" if (c.kwargs.get("provider") or c.args[3]).startswith("revenuecat") else "route"
        for c in top_up.await_args_list
    }
    assert len(kinds) == 1, f"{value!r} credited through {kinds}"


def test_a_webhook_event_that_is_not_a_purchase_is_still_acknowledged_when_the_route_is_open(
    app_client, top_up, monkeypatch
):
    """RevenueCat sends TEST and subscription events too; shutting the credit path must not turn those
    into errors that RevenueCat retries and surfaces as a failing integration."""
    _set(monkeypatch, "true")
    rc = {"Authorization": f"Bearer {_RC_SECRET}"}
    body = {"api_version": "1.0", "event": {"id": "e", "type": "TEST", "app_user_id": _WS}}
    assert app_client.post("/v1/webhooks/revenuecat", headers=rc, json=body).status_code == 200
    assert top_up.await_count == 0


# ── enumeration: nothing can credit a wallet without consulting the switch ─────


def _functions_calling_apply() -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for path in (_REPO / "backend").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if (
                not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                or fn.name == "apply_wallet_top_up"
            ):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    callee = node.func
                    name = getattr(callee, "id", getattr(callee, "attr", ""))
                    if name == "apply_wallet_top_up":
                        found[f"{path.relative_to(_REPO)}::{fn.name}"] = fn
    return found


def test_every_function_that_can_credit_a_wallet_consults_the_payment_channel_switch():
    callers = _functions_calling_apply()
    assert callers, "no caller of apply_wallet_top_up found: the enumeration is broken"
    for where, fn in callers.items():
        names = {getattr(n, "id", getattr(n, "attr", "")) for n in ast.walk(fn)}
        assert "billing_service_active" in names, (
            f"{where} can credit a wallet but never reads the switch"
        )


def test_the_set_of_crediting_functions_is_exactly_the_two_known_channels():
    """A third function that credits a wallet is a decision, not an accident: it must be added here, with
    its own both-directions test above."""
    assert {w.split("::")[1] for w in _functions_calling_apply()} == {
        "_credit_from_webhook",
        "top_up",
    }


# ── the route: auth, bounds, idempotency ──────────────────────────────────────


def _on(monkeypatch):
    monkeypatch.setenv(SETTING, "true")


def _post(client, body, *, auth=_SVC_SECRET, ws=_WS):
    headers = {} if auth is None else {"Authorization": auth}
    return client.post(f"/v1/wallet/{ws}/top-ups", headers=headers, json=body)


def test_a_user_token_is_not_the_service_secret(app_client, top_up, monkeypatch):
    _on(monkeypatch)
    resp = _post(app_client, {"amount_usd": 5.0, "order_id": "o"}, auth="Bearer some.user.jwt")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "service_auth_invalid"
    assert top_up.await_count == 0
    assert _post(app_client, {"amount_usd": 5.0, "order_id": "o"}, auth=None).status_code == 401


def test_an_unset_service_secret_refuses_every_call(app_client, top_up, monkeypatch):
    _on(monkeypatch)
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)
    assert _post(app_client, {"amount_usd": 5.0, "order_id": "o"}).status_code == 500
    assert top_up.await_count == 0


@pytest.mark.parametrize(
    "amount", ["0", "0.0", "-1", "-0.01", "500.01", "1e9", "NaN", "Infinity", "-Infinity"]
)
def test_an_amount_outside_the_bound_is_refused_in_the_handler_and_credits_nothing(
    app_client, top_up, monkeypatch, amount
):
    """Sent as raw JSON text: Python's own encoder will not emit NaN/Infinity, but a caller can."""
    _on(monkeypatch)
    resp = app_client.post(
        f"/v1/wallet/{_WS}/top-ups",
        headers={"Authorization": _SVC_SECRET, "Content-Type": "application/json"},
        content=f'{{"amount_usd": {amount}, "order_id": "o"}}'.encode(),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "amount_out_of_bounds"
    assert top_up.await_count == 0


@pytest.mark.parametrize("amount", [0.01, 1, 25.0, 499.99, 500, 500.0])
def test_an_amount_inside_the_bound_credits_exactly_once(app_client, top_up, monkeypatch, amount):
    _on(monkeypatch)
    resp = _post(app_client, {"amount_usd": amount, "order_id": "o"})
    assert resp.status_code == 200 and resp.json() == {"applied": True, "outcome": "new"}
    assert top_up.await_count == 1
    kw = top_up.await_args.kwargs
    assert (kw["workspace_id"], kw["amount_usd"], kw["external_transaction_id"]) == (
        _WS,
        float(amount),
        "o",
    )


def test_the_route_answers_a_replayed_order_as_a_duplicate_and_an_unknown_workspace_as_not_applied(
    app_client, top_up, monkeypatch
):
    _on(monkeypatch)
    top_up.return_value = "duplicate"
    assert _post(app_client, {"amount_usd": 5.0, "order_id": "o"}).json() == {
        "applied": False,
        "outcome": "duplicate",
    }
    top_up.return_value = "unknown"
    assert _post(app_client, {"amount_usd": 5.0, "order_id": "o"}).json() == {
        "applied": False,
        "outcome": "unknown",
    }


def test_the_route_is_shut_when_the_channel_is_off_and_credits_nothing(
    app_client, top_up, monkeypatch
):
    monkeypatch.delenv(SETTING, raising=False)
    resp = _post(app_client, {"amount_usd": 5.0, "order_id": "o"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "payment_channel_inactive"
    assert top_up.await_count == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"amount_usd": 5.0},
        {"order_id": "o"},
        {"amount_usd": "x", "order_id": "o"},
        {"amount_usd": 5.0, "order_id": ""},
    ],
)
def test_a_malformed_body_is_a_422_before_any_credit(app_client, top_up, monkeypatch, body):
    _on(monkeypatch)
    assert _post(app_client, body).status_code == 422
    assert top_up.await_count == 0


def test_a_workspace_id_that_is_not_a_uuid_is_refused(app_client, top_up, monkeypatch):
    _on(monkeypatch)
    assert (
        _post(app_client, {"amount_usd": 5.0, "order_id": "o"}, ws="not-a-uuid").status_code == 422
    )
    assert top_up.await_count == 0


def test_every_refusal_leaves_a_structured_line_naming_the_workspace_and_the_order(
    app_client, top_up, monkeypatch, caplog
):
    _on(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=wallet.log.name):
        _post(app_client, {"amount_usd": 9999.0, "order_id": "ord_audit_7"})
        top_up.return_value = "duplicate"
        _post(app_client, {"amount_usd": 5.0, "order_id": "ord_audit_8"})
        top_up.return_value = "unknown"
        _post(app_client, {"amount_usd": 5.0, "order_id": "ord_audit_9"})
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    for order in ("ord_audit_7", "ord_audit_8", "ord_audit_9"):
        assert any(order in m and _WS in m for m in lines), (order, lines)


def test_a_payment_refused_because_the_channel_is_shut_leaves_a_line_too(
    app_client, top_up, monkeypatch, caplog
):
    """The silent one: with the setting off (or set to something that is not an exact yes) the billing
    service's payments are refused 409, and without a trace the operator could not tell why they stopped."""
    monkeypatch.delenv(SETTING, raising=False)
    with caplog.at_level(logging.WARNING, logger=wallet.log.name):
        resp = _post(app_client, {"amount_usd": 5.0, "order_id": "ord_shut_1"})
    assert resp.status_code == 409 and top_up.await_count == 0
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("payment_channel_inactive" in m and "ord_shut_1" in m and _WS in m for m in lines), (
        lines
    )


def test_a_workspace_id_that_is_not_a_uuid_leaves_a_line_with_the_order(
    app_client, top_up, monkeypatch, caplog
):
    _on(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=wallet.log.name):
        _post(app_client, {"amount_usd": 5.0, "order_id": "ord_bad_ws"}, ws="not-a-uuid")
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("ord_bad_ws" in m and "not-a-uuid" in m for m in lines), lines


# ── MM7 changes who may CREDIT a wallet; it must change nothing about who may APPROVE an action ──


def test_the_human_gate_still_admits_people_only_and_the_external_effect_set_is_still_the_closed_three():
    """The new channel is a service principal's route. A service principal must remain unable to clear any
    gate, and no fourth irreversible effect may have appeared beside the three that each have their own
    dispatcher and kill switch."""
    from backend.callers.dependency import GATE_ADMITS, require_human
    from backend.callers.ports import Refusal
    from backend.callers.principal import Entitlement, Principal
    from gtm_core.packs import loader

    assert loader._ALLOWED_EXTERNAL_EFFECTS == frozenset({"publish", "email_enroll", "dnc_add"})
    assert set(GATE_ADMITS) == {"user"}
    service = Principal(
        kind="service",
        subject="k",
        workspace_id=_WS,
        entitlement=Entitlement("pro"),
        agent_id="a",
        credential_id="k",
        verifier="api_key",
        evidence_digest="x",
    )
    with pytest.raises(Refusal) as err:
        require_human(service)
    assert err.value.code == "human_approval_required"


@pytest.mark.private_tree  # reads deploy/, which the public carve does not ship
def test_the_payment_channel_setting_is_passed_to_the_api_with_an_empty_default():
    """Settable without a code change, and unset means the webhook stays live and the route stays shut."""
    from pathlib import Path

    import yaml

    from backend.payment_channel import SETTING

    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml").read_text()
    )
    env = compose["services"]["api"]["environment"]
    assert env.get(SETTING) == f"${{{SETTING}:-true}}"

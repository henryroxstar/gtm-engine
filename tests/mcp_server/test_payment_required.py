"""MM13: an origin refusal for payment says what to do and where, using the same words as the edge.

The origin's refusals used to be "402 Payment Required: Insufficient MCP credits. Please top up your
balance." — true, and a dead end. With ``BILLING_URL`` / ``PRICING_URL`` set they now end with the link the
Worker would have returned; with the setting unset they say nothing about a link and log a warning, because
a link nobody configured is not a link to invent. The payload itself is pinned to the Worker's source in
``tests/contracts/test_payment_required_payload.py``.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("asyncpg")

from gtm_core import payment_required as pr  # noqa: E402
from gtm_core.metering import InsufficientCreditsError, reserve_credits  # noqa: E402
from mcp_server.meter import check_budget  # noqa: E402
from tests.mcp_server.test_mcp_wallets import _make_mock_pool  # noqa: E402

WS = "aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa"
BILLING = "https://billing.example.test/top-up"
PRICING = "https://billing.example.test/pricing"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _links(monkeypatch):
    monkeypatch.setenv("BILLING_URL", BILLING)
    monkeypatch.setenv("PRICING_URL", PRICING)


# ── the module ───────────────────────────────────────────────────────────────────────────────────


def test_the_credits_payload_carries_the_billing_link_and_the_subscription_one_the_pricing_link():
    assert pr.payload("credits") == {
        "error": "payment_required",
        "message": "Insufficient MCP credits. Please top up your balance.",
        "upgrade_url": BILLING,
    }
    assert pr.payload("subscription")["upgrade_url"] == PRICING


def test_the_text_is_the_message_then_the_link():
    text = pr.text("credits")
    assert text.startswith("402 Payment Required: Insufficient MCP credits.")
    assert text.endswith(BILLING)


def test_the_text_can_carry_a_detail_before_the_link():
    text = pr.text("credits", detail="Available: 0.0000, Required: 10.0000")
    assert "Available: 0.0000, Required: 10.0000" in text and text.endswith(BILLING)


def test_with_no_setting_there_is_no_link_and_a_warning(monkeypatch, caplog):
    monkeypatch.delenv("BILLING_URL")
    with caplog.at_level(logging.WARNING):
        text = pr.text("credits")
    assert "http" not in text and "upgrade_url" not in pr.payload("credits")
    assert any("BILLING_URL" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("raw", ["", "  ", "javascript:alert(1)", "ftp://x", "not a url"])
def test_a_setting_that_is_not_an_http_link_is_treated_as_absent(monkeypatch, raw):
    monkeypatch.setenv("BILLING_URL", raw)
    assert "upgrade_url" not in pr.payload("credits")
    assert "://" not in pr.text("credits")


# ── the origin refusals use it ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("wallet_row", [{"balance_usd": 0.0}, {"balance_usd": -1.5}, None])
def test_check_budget_refuses_with_the_billing_link(wallet_row):
    pool = _make_mock_pool(wallet_row=wallet_row, sub_row=None)
    with pytest.raises(
        InsufficientCreditsError, match=r"402 Payment Required: Insufficient MCP credits"
    ) as err:
        _run(check_budget(WS, pool))
    assert str(err.value).endswith(BILLING)


def test_check_budget_with_no_link_configured_keeps_the_plain_message(monkeypatch):
    monkeypatch.delenv("BILLING_URL")
    pool = _make_mock_pool(wallet_row={"balance_usd": 0.0}, sub_row=None)
    with pytest.raises(InsufficientCreditsError) as err:
        _run(check_budget(WS, pool))
    assert (
        str(err.value)
        == "402 Payment Required: Insufficient MCP credits. Please top up your balance."
    )


def test_reserve_credits_refuses_with_the_billing_link_and_the_figures():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"balance_credits": 100.0, "balance_usd": 0.1})
    conn.fetchval = AsyncMock(return_value=90.0)
    with pytest.raises(InsufficientCreditsError) as err:
        _run(reserve_credits(conn, WS, estimated_credits=50.0, raise_on_error=True))
    assert "Available: 10.0000, Required: 50.0000" in str(err.value)
    assert str(err.value).endswith(BILLING)


def test_the_default_message_of_the_error_itself_carries_the_link():
    assert str(InsufficientCreditsError()).endswith(BILLING)


def test_a_free_key_calling_a_pipeline_tool_is_sent_to_pricing_not_billing():
    from gtm_core.capabilities import Entitlement
    from mcp_server import server
    from mcp_server.auth import ApiKeyCtx

    async def _free(ctx):
        return ApiKeyCtx(workspace_id=WS, entitlement=Entitlement.FREE, key_id="k")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server, "_require_key", _free)
        with pytest.raises(ValueError, match="PIPELINE tools require a Pro subscription") as err:
            _run(server._require_pipeline(MagicMock()))
    assert str(err.value).endswith(PRICING)


# ── settable without a code change ───────────────────────────────────────────────────────────────


@pytest.mark.private_tree  # reads deploy/, which the public carve does not ship
@pytest.mark.parametrize("service", ["api", "worker", "mcp"])
@pytest.mark.parametrize("setting", ["BILLING_URL", "PRICING_URL"])
def test_the_link_settings_are_passed_to_every_service_that_can_refuse(service, setting):
    from pathlib import Path

    import yaml

    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml").read_text()
    )
    env = compose["services"][service]["environment"]
    env = env if isinstance(env, dict) else dict(item.split("=", 1) for item in env)
    assert env.get(setting) == f"${{{setting}:-}}"


@pytest.mark.parametrize("tool", ["draft_post", "draft_outreach"])
def test_the_draft_tools_say_where_to_top_up_when_the_reservation_comes_back_empty(
    tool, monkeypatch
):
    """The one 402-class refusal that used to be a bare string: a reservation that returns nothing."""
    from gtm_core.capabilities import Entitlement
    from mcp_server import server
    from mcp_server.auth import ApiKeyCtx

    key = ApiKeyCtx(workspace_id=WS, entitlement=Entitlement.PRO, key_id="k")
    monkeypatch.setattr(server, "_require_profile", AsyncMock(return_value=key))
    monkeypatch.setattr(server, "check_budget", AsyncMock(return_value=True))
    monkeypatch.setattr(server, "reserve_credits", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="402 Payment Required") as err:
        _run(getattr(server, tool)(brief="b", profile="acme", ctx=MagicMock()))
    assert str(err.value).endswith(BILLING)

"""Test for the /cost slash command in cockpit."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import FakeMsg, make_cfg, text_update  # noqa: E402

from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 505


def test_cmd_cost_in_registered_commands(tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    cmds = dict(cockpit.registered_commands())
    assert "cost" in cmds


def test_cmd_cost_requires_whitelist(tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    msg = FakeMsg(999, text="/cost")
    update, context = text_update(999, msg)
    asyncio.run(cockpit.cmd_cost(update, context))
    assert msg.replies == []


def test_cmd_cost_renders_budget_status(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "example").mkdir(parents=True)
    (tmp_path / "profiles" / "example" / "PROFILE.md").write_text("monthly_tool_budget_usd: 50.0\n")
    (tmp_path / "example").mkdir()
    (tmp_path / "example" / "costs.jsonl").write_text(
        '{"ts":"2026-09-03T10:00:00Z","cost_usd":12.5,"tool":"rocketreach"}\n'
    )

    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    msg = FakeMsg(CHAT_ID, text="/cost")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.cmd_cost(update, context))

    assert len(msg.replies) == 1
    assert "of your $50.00 for" in msg.replies[0]
    assert "$12.50" in msg.replies[0]

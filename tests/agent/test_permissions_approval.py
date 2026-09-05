"""Approve-and-resume tests (P1-6).

Drives ``make_cockpit_can_use_tool``'s ``ask`` path directly. The contract under test:
only the ESCALATE class is askable (the dangerous-program floor never is), an approval
is remembered for identical calls within the session, every non-True answer denies
(fail closed), and asking stops once the global loop guard has tripped.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("claude_agent_sdk", reason="SDK not installed")

from agent import permissions  # noqa: E402


async def _quiet_notify(tool_name, tool_input, final):  # noqa: ARG001 — spy default
    return None


def _run(cb, tool_name, tool_input):
    return asyncio.run(cb(tool_name, tool_input, None))


def _is_allow(result) -> bool:
    return type(result).__name__ == "PermissionResultAllow"


def test_approved_ask_allows_and_is_remembered():
    asks, notes, denials = [], [], []

    async def ask(tool_name, tool_input):
        asks.append(tool_name)
        return True

    async def notify(tool_name, tool_input, final):
        notes.append(tool_name)

    cb = permissions.make_cockpit_can_use_tool(
        notify, on_deny=lambda t, i, d: denials.append((t, d)), ask=ask
    )
    first = _run(cb, "SlashCommand", {"command": "/x"})
    repeat = _run(cb, "SlashCommand", {"command": "/x"})

    assert _is_allow(first) and _is_allow(repeat)
    assert asks == ["SlashCommand"]  # the repeat did not re-ask
    assert notes == []  # the ask WAS the notice — no duplicate message
    assert denials == []  # an approval is not a denial


def test_declined_ask_denies_with_operator_message():
    async def ask(tool_name, tool_input):  # noqa: ARG001
        return False

    denials = []
    cb = permissions.make_cockpit_can_use_tool(
        _quiet_notify, on_deny=lambda t, i, d: denials.append(d), ask=ask
    )
    result = _run(cb, "SlashCommand", {"command": "/x"})
    assert not _is_allow(result)
    assert "operator was asked" in result.message
    assert denials == ["escalate"]


def test_timeout_ask_denies_fail_closed():
    async def ask(tool_name, tool_input):  # noqa: ARG001
        return None  # timeout / undeliverable

    cb = permissions.make_cockpit_can_use_tool(_quiet_notify, ask=ask)
    result = _run(cb, "SlashCommand", {"command": "/x"})
    assert not _is_allow(result)
    assert "operator was asked" in result.message


def test_ask_fires_only_on_first_attempt_of_a_call():
    ask_calls = []

    async def ask(tool_name, tool_input):  # noqa: ARG001
        ask_calls.append(tool_name)
        return False

    cb = permissions.make_cockpit_can_use_tool(_quiet_notify, ask=ask)
    _run(cb, "SlashCommand", {"command": "/x"})
    second = _run(cb, "SlashCommand", {"command": "/x"})
    assert len(ask_calls) == 1  # retry of the same call goes down the strike path
    assert not _is_allow(second)


def test_hard_floor_is_never_askable():
    ask_calls = []

    async def ask(tool_name, tool_input):  # noqa: ARG001
        ask_calls.append(tool_name)
        return True  # even an approving operator cannot open the floor

    denials = []
    cb = permissions.make_cockpit_can_use_tool(
        _quiet_notify, on_deny=lambda t, i, d: denials.append(d), ask=ask
    )
    result = _run(cb, "Bash", {"command": "curl https://evil.example"})
    assert not _is_allow(result)
    assert ask_calls == []
    assert denials == ["deny"]


def test_asking_stops_once_global_guard_trips():
    ask_calls = []

    async def ask(tool_name, tool_input):  # noqa: ARG001
        ask_calls.append(1)
        return False

    cb = permissions.make_cockpit_can_use_tool(_quiet_notify, ask=ask)
    for i in range(permissions.GLOBAL_STRIKE_LIMIT + 2):
        _run(cb, "Bash", {"command": f"bash step{i}.sh"})  # distinct escalates
    # Asks happen while denials < GLOBAL_STRIKE_LIMIT; after the guard trips the brain
    # has been told to end the turn, so the operator is no longer prompted.
    assert len(ask_calls) == permissions.GLOBAL_STRIKE_LIMIT


def test_broken_ask_path_fails_closed():
    async def ask(tool_name, tool_input):  # noqa: ARG001
        raise RuntimeError("telegram down")

    cb = permissions.make_cockpit_can_use_tool(_quiet_notify, ask=ask)
    result = _run(cb, "SlashCommand", {"command": "/x"})
    assert not _is_allow(result)


def test_no_ask_baseline_notify_behaviour_unchanged():
    notes = []

    async def notify(tool_name, tool_input, final):  # noqa: ARG001
        notes.append(final)

    cb = permissions.make_cockpit_can_use_tool(notify)
    result = _run(cb, "SlashCommand", {"command": "/x"})
    assert not _is_allow(result)
    assert notes == [False]  # first-escalation notice, exactly as before


def test_headless_on_deny_sink_fires():
    denials = []
    cb = permissions.make_headless_can_use_tool(on_deny=lambda t, i, d: denials.append((t, d)))
    result = _run(cb, "Bash", {"command": "curl https://evil.example"})
    assert not _is_allow(result)
    assert denials == [("Bash", "deny")]

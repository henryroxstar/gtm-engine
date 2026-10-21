"""`AgentSession.interrupt` tells the truth about whether a turn is running (F3-3).

Found by the live test (tests/live_sdk/test_stop_live.py): a cold session is still connecting for a few
seconds after the operator's message, and `/stop` pressed then used to report "nothing is running" while the
turn went on to start — and an idle but connected session used to claim it had stopped something. The SDK
client is faked here (its real interrupt is covered live); the session's own bookkeeping is real.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

import agent.session as sm


class FakeClient:
    instances: list[FakeClient] = []

    def __init__(self, options=None):
        self.options = options
        self.queries: list[str] = []
        self.interrupts = 0
        self.connect_delay = 0.0
        self.turn_gate: asyncio.Event | None = None
        FakeClient.instances.append(self)

    async def connect(self):
        await asyncio.sleep(type(self).connect_delay)

    async def disconnect(self):
        return None

    async def query(self, prompt):
        self.queries.append(prompt)

    async def interrupt(self):
        self.interrupts += 1
        if self.turn_gate is not None:
            self.turn_gate.set()

    async def receive_response(self):
        if self.turn_gate is not None:
            await self.turn_gate.wait()
        return
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture
def session(monkeypatch):
    FakeClient.instances = []
    FakeClient.connect_delay = 0.0
    sdk = types.ModuleType("claude_agent_sdk")
    sdk.ClaudeSDKClient = FakeClient
    sdk.AssistantMessage = type("AssistantMessage", (), {})
    sdk.ResultMessage = type("ResultMessage", (), {})
    sdk.TextBlock = type("TextBlock", (), {})
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(sm, "build_agent_options", lambda *a, **k: object())
    return sm.AgentSession(object(), "alpha")


async def _drain(s, prompt="hi"):
    return [t async for t in s.run(prompt)]


def test_an_idle_connected_session_reports_nothing_to_stop(session):
    async def go():
        await _drain(session)
        return await session.interrupt()

    assert asyncio.run(go()) is False
    assert FakeClient.instances[0].interrupts == 0  # it did not pretend


def test_interrupt_during_a_cold_connect_cancels_the_turn_before_it_starts(session):
    FakeClient.connect_delay = 0.3

    async def go():
        task = asyncio.create_task(_drain(session))
        await asyncio.sleep(0.05)  # the CLI is still starting
        stopped = await session.interrupt()
        out = await task
        return stopped, out

    stopped, out = asyncio.run(go())
    assert stopped is True and out == []
    assert (
        FakeClient.instances[0].queries == []
    )  # the prompt was never sent: /stop really stopped it


def test_interrupt_mid_turn_reaches_the_client_and_the_turn_ends(session):
    async def go():
        FakeClient.connect_delay = 0.0
        gate = asyncio.Event()
        orig = FakeClient.__init__

        def init(self, options=None):
            orig(self, options)
            self.turn_gate = gate

        FakeClient.__init__ = init
        try:
            task = asyncio.create_task(_drain(session))
            await asyncio.sleep(0.1)  # connected, query sent, waiting for the model
            stopped = await session.interrupt()
            await asyncio.wait_for(task, timeout=2)
            return stopped
        finally:
            FakeClient.__init__ = orig

    assert asyncio.run(go()) is True
    assert FakeClient.instances[0].interrupts == 1


def test_a_stop_request_does_not_leak_into_the_next_turn(session):
    FakeClient.connect_delay = 0.2

    async def go():
        task = asyncio.create_task(_drain(session))
        await asyncio.sleep(0.05)
        await session.interrupt()
        await task
        FakeClient.connect_delay = 0.0
        await _drain(session, "second")
        return FakeClient.instances[0].queries

    assert asyncio.run(go()) == ["second"]  # the cancelled prompt never went; the next one does

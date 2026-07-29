"""Streaming contract for the backend agent session (Phase F mobile surface).

These prove that ``backend.session._BackendSession.run()`` streams assistant text
via the REAL SDK contract — ``client.query(prompt)`` then iterating
``client.receive_response()``, yielding only ``TextBlock`` text inside
``AssistantMessage``s — *without* importing the real ``claude_agent_sdk``, hitting
the network, or needing an API key.

Why this test exists: an earlier copy of ``_BackendSession.run()`` called a
non-existent ``client.run(prompt)`` and yielded ``msg.text`` on anything with a
``.text`` attribute. That bug never surfaced because the Phase F acceptance gate
(PENDING.md) was never run, but it would crash the moment the mobile backend
streamed a run. ``_BackendSession`` now delegates to the production
``agent.session.AgentSession`` (the Telegram-cockpit-tested implementation), so
this test drives that shared streaming path through the backend wrapper.

Style mirrors ``tests/agent/test_web_concurrency.py``: SDK-free, coroutines driven
with ``asyncio.run`` (the repo convention — no pytest-asyncio plugin).
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

# Repo root on sys.path so ``import backend.session`` / ``import agent.session``
# resolve to the in-tree packages when pytest runs from the repo root.
# tests/backend/test_session_streaming.py → parents[2] == repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _install_fake_sdk(monkeypatch):
    """Inject a minimal ``claude_agent_sdk`` so ``AgentSession.run()`` imports.

    ``AgentSession.run`` does ``from claude_agent_sdk import AssistantMessage,
    TextBlock`` lazily — we only need those two names (plus the message types the
    stream yields). No real SDK, so the test runs in a bare unit-test env.
    """
    mod = types.ModuleType("claude_agent_sdk")

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content, usage=None):
            self.content = content
            self.usage = usage

    mod.TextBlock = TextBlock
    mod.AssistantMessage = AssistantMessage
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return mod


class _ToolUseBlock:
    """A non-text content block — must be filtered out of the stream."""


class _ResultMessage:
    """A non-assistant stream message (system/result) — must be skipped."""


class _FakeClient:
    """Stub for ``ClaudeSDKClient``: records query(), replays receive_response()."""

    def __init__(self, messages):
        self._messages = messages
        self.queries: list[str] = []
        self.disconnected = False

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        for msg in self._messages:
            yield msg

    async def disconnect(self):
        self.disconnected = True


def _make_session(messages):
    """A connected ``_BackendSession`` whose AgentSession holds ``_FakeClient``.

    Bypasses ``connect()`` (which would build real options + a real SDK client)
    by injecting a pre-connected ``AgentSession`` — exactly the state ``connect()``
    leaves behind, minus the network.
    """
    from agent.session import AgentSession
    from backend.session import _BackendSession

    client = _FakeClient(messages)
    # cfg=None is safe: run() only touches cfg via _log_usage, which is gated on
    # ``msg.usage`` being truthy — our messages carry no usage.
    agent = AgentSession(cfg=None, profile="example")
    agent._client = client
    agent._connected = True

    sess = _BackendSession(REPO_ROOT, "example")
    sess._agent = agent
    sess.connected = True
    return sess, client


def test_run_streams_text_chunks(monkeypatch):
    """run() yields only TextBlock text from AssistantMessages, in order."""
    sdk = _install_fake_sdk(monkeypatch)

    messages = [
        sdk.AssistantMessage(
            content=[sdk.TextBlock("Hello "), _ToolUseBlock(), sdk.TextBlock("world")]
        ),
        _ResultMessage(),  # skipped — not an AssistantMessage
        sdk.AssistantMessage(content=[sdk.TextBlock("!")]),
    ]
    sess, client = _make_session(messages)

    async def drive():
        out = []
        async for chunk in sess.run("warmest account?"):
            out.append(chunk)
        return out

    out = asyncio.run(drive())

    assert out == ["Hello ", "world", "!"], f"unexpected stream: {out}"
    # The real SDK contract: query() must be called with the prompt, then the
    # response iterated — proving we are NOT on the bogus client.run(prompt) path.
    assert client.queries == ["warmest account?"]


def test_run_requires_connect(monkeypatch):
    """run() on an unconnected session raises rather than touching a None agent."""
    _install_fake_sdk(monkeypatch)
    from backend.session import _BackendSession

    sess = _BackendSession(REPO_ROOT, "example")  # never connected

    async def drive():
        async for _ in sess.run("hi"):
            pass

    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(drive())


def test_close_disconnects_and_resets(monkeypatch):
    """close() disconnects the underlying SDK client and clears connected state."""
    sdk = _install_fake_sdk(monkeypatch)
    sess, client = _make_session([sdk.AssistantMessage(content=[sdk.TextBlock("ok")])])

    asyncio.run(sess.close())

    assert client.disconnected is True
    assert sess.connected is False
    assert sess._agent is None

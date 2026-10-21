"""Regression: the headless one-shot path MUST drive a persistent ``ClaudeSDKClient``.

Root cause of the 2026-06-28 example pipeline failure: ``execute_stage`` (and the ``agent``
CLI one-shot + the dashboard brief/directive/run-now paths) used the module-level
``claude_agent_sdk.query()`` with a single-message input generator. That generator exhausts
immediately, which closes the SDK↔CLI control stream — so the CLI's permission round-trip for
every gated tool (``Write`` / ``Bash`` / any MCP tool) came back ``Tool permission request
failed: Error: Stream closed`` while read-only tools (no ``can_use_tool`` callback) kept working.
The fix routes all of those through :func:`agent.session.stream_brain_messages`, which holds a
``ClaudeSDKClient`` open for the whole turn (``client.query(prompt)`` then iterating
``client.receive_response()``) — the same pattern ``AgentSession`` already used.

This test pins that contract with a fake SDK: ``query()`` is called once with the prompt, and the
messages from ``receive_response()`` are yielded through. It guards against regressing back to the
one-shot ``query()`` that silently broke headless writes.
"""

from __future__ import annotations

import asyncio
import sys
import types


def _install_fake_sdk(monkeypatch):
    """Inject a minimal ``claude_agent_sdk`` exposing a recording ``ClaudeSDKClient``."""
    mod = types.ModuleType("claude_agent_sdk")

    captured: dict = {"options": None, "prompt": None, "entered": 0, "exited": 0}
    replay = ["m1", "m2", "m3"]

    class ClaudeSDKClient:
        def __init__(self, options=None):
            captured["options"] = options

        async def __aenter__(self):
            captured["entered"] += 1
            return self

        async def __aexit__(self, *exc):
            captured["exited"] += 1
            return False

        async def query(self, prompt):
            captured["prompt"] = prompt

        async def receive_response(self):
            for msg in replay:
                yield msg

    mod.ClaudeSDKClient = ClaudeSDKClient
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return captured, replay


def test_stream_brain_messages_drives_persistent_client(monkeypatch):
    captured, replay = _install_fake_sdk(monkeypatch)

    from agent.session import stream_brain_messages

    async def _collect():
        return [m async for m in stream_brain_messages({"opt": 1}, "do the thing")]

    got = asyncio.run(_collect())

    # Yields exactly what receive_response() replays, in order.
    assert got == replay
    # query() was called once with the prompt; options threaded into the client.
    assert captured["prompt"] == "do the thing"
    assert captured["options"] == {"opt": 1}
    # The client was opened and cleaned up as an async context manager (control
    # stream stays open for the whole turn — the actual fix).
    assert captured["entered"] == 1
    assert captured["exited"] == 1


def test_prompt_stream_helper_is_gone(monkeypatch):
    """The old single-yield ``_prompt_stream`` (which closed the control stream) is removed."""
    _install_fake_sdk(monkeypatch)
    import agent.session as session

    assert not hasattr(session, "_prompt_stream")


def test_executor_does_not_use_oneshot_query():
    """The pipeline executor routes through the shared client helper, not module-level query()."""
    import agent.pipeline_executor as pe

    assert hasattr(pe, "stream_brain_messages")
    # The standalone one-shot query() must not be imported at module scope here.
    assert not hasattr(pe, "query")

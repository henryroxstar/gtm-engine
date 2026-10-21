"""W4 measurement wired into the backend run path (PRD 2026-10-19a, test plan P4-6).

SDK-free: a fake ``claude_agent_sdk`` and fake client, driven with ``asyncio.run`` (repo convention).
Proves the *plumbing* — a record is emitted per backend run with the right fields, a failed or slow
measurement never breaks the run, and the warm-session hit/miss is counted. What it cannot prove
(real token values, the 10% agreement with the first-turn cache write) needs a live CLI; see the
step report.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import types
from types import SimpleNamespace

from backend import session as bsess

_REAL_BACKEND_SESSION = bsess._BackendSession  # the store test swaps the module attribute

_CTX = {
    "totalTokens": 61_000,
    "memoryFiles": [{"path": "/app/CLAUDE.md", "tokens": 10_000}],
    "mcpTools": [{"name": "t", "serverName": "worker", "tokens": 700, "isLoaded": True}],
    "skills": {"totalSkills": 3, "includedSkills": 2, "skillFrontmatter": [{"tokens": 150}]},
}


def _install_fake_sdk(monkeypatch):
    mod = types.ModuleType("claude_agent_sdk")

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content, usage=None, parent_tool_use_id=None):
            self.content = content
            self.usage = usage
            self.parent_tool_use_id = parent_tool_use_id

    class ResultMessage:
        session_id = None

    mod.TextBlock, mod.AssistantMessage, mod.ResultMessage = (
        TextBlock,
        AssistantMessage,
        ResultMessage,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return mod


class _Client:
    def __init__(self, sdk, *, context=_CTX, context_error=None, context_delay=0.0):
        self._sdk = sdk
        self._context = context
        self._error = context_error
        self._delay = context_delay
        self.context_calls = 0

    async def query(self, prompt):
        pass

    async def receive_response(self):
        A, T = self._sdk.AssistantMessage, self._sdk.TextBlock
        yield A(
            [T("a")],
            {"input_tokens": 10, "cache_creation_input_tokens": 50_000, "output_tokens": 1},
        )
        yield A(
            [T("sub")], {"input_tokens": 9_999, "cache_creation_input_tokens": 9_999}, "toolu_sub"
        )
        yield A(
            [T("b")],
            {
                "input_tokens": 20,
                "cache_creation_input_tokens": 300,
                "cache_read_input_tokens": 50_000,
            },
        )

    async def get_context_usage(self):
        self.context_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return self._context

    async def disconnect(self):
        pass


def _session(client, **kw):
    """A connected _BackendSession whose inner AgentSession drives ``client``."""
    from agent.session import AgentSession

    inner = AgentSession(
        SimpleNamespace(content_root="."),
        "fixtureco",
        usage_sink=lambda rec: None,
        measure_context=True,
        **kw,
    )
    inner._client, inner._connected = client, True
    s = _REAL_BACKEND_SESSION(".", "fixtureco", workspace_id="ws_test")
    s._agent, s.connected = inner, True
    return s


def _drain(session, run_id="run_test"):
    async def go():
        return [c async for c in session.run("hi", run_id, agent_id="agent_test")]

    return asyncio.run(go())


def _records(caplog):
    return [
        json.loads(r.getMessage().split("run_context_metrics ", 1)[1])
        for r in caplog.records
        if r.name == "gtm.context_metrics" and "run_context_metrics " in r.getMessage()
    ]


def test_a_run_emits_one_record_with_the_measured_fields(monkeypatch, caplog):
    sdk = _install_fake_sdk(monkeypatch)
    caplog.set_level(logging.INFO, logger="gtm.context_metrics")
    assert _drain(_session(_Client(sdk))) == ["a", "sub", "b"]
    (rec,) = _records(caplog)
    assert (
        rec["run_id"] == "run_test"
        and rec["workspace_id"] == "ws_test"
        and rec["agent_id"] == "agent_test"
    )
    assert rec["turns"] == 2  # the subagent's turn is not a main-thread turn
    assert rec["first_turn_cache_creation_input_tokens"] == 50_000
    assert rec["last_turn_input_tokens"] == 20 + 300 + 50_000
    assert rec["skills_tokens"] == 150 and rec["memory_files_tokens"] == 10_000
    assert rec["mcp_tokens_by_server"] == {"worker": 700}
    assert rec["warm_hit"] is False


def test_the_context_snapshot_is_taken_once_per_session_not_per_run(monkeypatch, caplog):
    sdk = _install_fake_sdk(monkeypatch)
    caplog.set_level(logging.INFO, logger="gtm.context_metrics")
    client = _Client(sdk)
    s = _session(client)
    _drain(s, "r1")
    _drain(s, "r2")
    assert client.context_calls == 1
    first, second = _records(caplog)
    assert (
        second["skills_tokens"] == first["skills_tokens"] == 150
    )  # the second run reuses the snapshot


def test_a_failing_snapshot_never_breaks_the_run_and_is_marked_unavailable(monkeypatch, caplog):
    sdk = _install_fake_sdk(monkeypatch)
    caplog.set_level(logging.INFO, logger="gtm.context_metrics")
    client = _Client(sdk, context_error=RuntimeError("control request unsupported"))
    s = _session(client)
    assert _drain(s, "r1") == ["a", "sub", "b"]
    _drain(s, "r2")
    assert client.context_calls == 1  # a failure is not retried on every run
    rec = _records(caplog)[0]
    assert rec["context_available"] is False
    assert rec["skills_tokens"] is None and rec["mcp_tokens_total"] is None  # unmeasured, not zero
    assert (
        rec["last_turn_input_tokens"] == 50_320
    )  # the per-turn figures do not depend on the snapshot


def test_a_slow_snapshot_is_cut_off(monkeypatch, caplog):
    sdk = _install_fake_sdk(monkeypatch)
    monkeypatch.setattr("agent.session._CONTEXT_SNAPSHOT_TIMEOUT_S", 0.05)
    caplog.set_level(logging.INFO, logger="gtm.context_metrics")
    assert _drain(_session(_Client(sdk, context_delay=5))) == ["a", "sub", "b"]
    assert _records(caplog)[0]["context_available"] is False


def test_a_session_not_asked_to_measure_makes_no_extra_call(monkeypatch):
    from agent.session import AgentSession

    sdk = _install_fake_sdk(monkeypatch)
    client = _Client(sdk)
    inner = AgentSession(
        SimpleNamespace(content_root="."), "fixtureco", usage_sink=lambda rec: None
    )
    inner._client, inner._connected = client, True

    async def go():
        return [c async for c in inner.run("hi")]

    asyncio.run(go())
    assert client.context_calls == 0  # the cockpit path is unchanged


def test_store_counts_warm_hits_and_cold_starts(monkeypatch, caplog):
    sdk = _install_fake_sdk(monkeypatch)
    caplog.set_level(logging.INFO, logger="gtm.context_metrics")

    def new_session(*a, **k):
        s = _session(_Client(sdk))

        async def connect():  # the store connects a fresh session before running it
            s.connected = True

        s.connect = connect
        return s

    monkeypatch.setattr(bsess, "_BackendSession", new_session)
    store = bsess.BackendSessionStore(max_warm_sessions=4)

    async def go():
        for rid in ("r1", "r2", "r3"):
            async for _ in store.run(None, "ws_test", "fixtureco", "hi", rid):
                pass

    asyncio.run(go())
    assert store.warm_stats == {"warm_hits": 2, "cold_starts": 1}
    assert [r["warm_hit"] for r in _records(caplog)] == [False, True, True]

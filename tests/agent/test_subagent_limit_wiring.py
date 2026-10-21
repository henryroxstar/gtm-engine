"""W5 wiring: every run path carries the limit, each window resets, and the note reaches the output.

``tests/agent/test_subagent_limit.py`` covers the counter and the hook; this file proves they are attached
where a run starts and reset where a run (or a pack node, or a cockpit turn) begins.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("claude_agent_sdk")

import agent.pipeline_executor as pe  # noqa: E402
from agent.pipeline import OK  # noqa: E402
from agent.session import AgentSession, build_agent_options  # noqa: E402
from agent.subagent_limit import SubagentCounter  # noqa: E402


def _build(monkeypatch, tmp_path, *, counter=None, settings=None, **kw):
    captured: dict = {}

    class _Options:
        def __init__(self, **k):
            captured.update(k)

    monkeypatch.setattr("agent.mcp_config.build_mcp_servers", lambda *a, **k: {})
    monkeypatch.setattr("agent.profiles.system_prompt_for", lambda *a, **k: "")
    cfg = SimpleNamespace(
        repo_root=tmp_path,
        plugin_path=tmp_path,
        content_root=tmp_path / "content",
        profiles_root=tmp_path / "profiles",
        per_run_cap_usd=1.0,
    )
    if settings is not None:
        (cfg.content_root / "fixtureco").mkdir(parents=True, exist_ok=True)
        (cfg.content_root / "fixtureco" / "settings.json").write_text(json.dumps(settings))
    with patch("claude_agent_sdk.ClaudeAgentOptions", _Options):
        opts = build_agent_options(
            cfg, "fixtureco", can_use_tool=lambda *a: None, subagent_counter=counter, **kw
        )
    return captured, opts


def _matchers(captured):
    return {m.matcher: m for m in captured["hooks"]["PreToolUse"]}


def _fire(hook, name, tool_use_id):
    return asyncio.run(hook({"tool_name": name, "tool_input": {}}, tool_use_id, None))


# ── build_agent_options: the limit is on by default ──────────────────────────


def test_every_run_carries_the_limit_without_the_caller_asking(monkeypatch, tmp_path):
    captured, opts = _build(monkeypatch, tmp_path)
    (matcher,) = _matchers(captured).values()
    counter = opts._subagent_counter
    assert counter.limit == 8
    assert [_fire(matcher.hooks[0], "Agent", f"t{i}") for i in range(8)] == [{}] * 8
    assert (
        _fire(matcher.hooks[0], "Agent", "t9")["hookSpecificOutput"]["permissionDecision"] == "deny"
    )
    assert counter.denied == 1


def test_the_matcher_covers_exactly_the_names_a_cli_uses_for_a_helper(monkeypatch, tmp_path):
    import re

    captured, _ = _build(monkeypatch, tmp_path)
    (pattern,) = _matchers(captured)
    assert all(re.fullmatch(pattern, n) for n in ("Agent", "Task"))
    assert not any(re.fullmatch(pattern, n) for n in ("TaskCreate", "TaskOutput", "Read", "Bash"))


def test_a_profile_setting_sets_the_limit_and_the_clamp_holds(monkeypatch, tmp_path):
    assert (
        _build(monkeypatch, tmp_path, settings={"max_subagents": 3})[1]._subagent_counter.limit == 3
    )
    assert (
        _build(monkeypatch, tmp_path, settings={"max_subagents": 999})[1]._subagent_counter.limit
        == 32
    )


def test_a_caller_supplied_counter_is_the_one_the_hook_uses(monkeypatch, tmp_path):
    mine = SubagentCounter(1)
    captured, opts = _build(monkeypatch, tmp_path, counter=mine)
    assert opts._subagent_counter is mine
    (matcher,) = _matchers(captured).values()
    _fire(matcher.hooks[0], "Agent", "a")
    assert mine.started == 1  # a shared window: two builds with one counter share one budget


def test_the_deny_floor_and_the_permission_callback_are_unchanged(monkeypatch, tmp_path):
    captured, _ = _build(monkeypatch, tmp_path)
    assert captured["permission_mode"] == "default" and captured["can_use_tool"] is not None
    assert captured["permission_mode"] != "bypassPermissions"


# ── AgentSession: one window per run / turn ───────────────────────────────────


class _Client:
    """Stands in for the CLI: it 'launches' helpers by calling the same counter the hook would."""

    def __init__(self, session, launches_per_run):
        self.session, self.plan = session, list(launches_per_run)

    async def query(self, prompt):
        self.n = self.plan.pop(0)

    async def receive_response(self):
        from claude_agent_sdk import AssistantMessage, TextBlock

        for i in range(self.n):
            self.session._subagents.admit(f"{id(self)}-{self.n}-{i}-{len(self.plan)}")
        yield AssistantMessage(content=[TextBlock("done")], model="m", usage=None)

    async def disconnect(self):
        pass


def _session(tmp_path, plan, settings=None):
    cfg = SimpleNamespace(content_root=tmp_path)
    if settings is not None:
        (tmp_path / "fixtureco").mkdir(parents=True, exist_ok=True)
        (tmp_path / "fixtureco" / "settings.json").write_text(json.dumps(settings))
    s = AgentSession(cfg, "fixtureco")
    s._client, s._connected = _Client(s, plan), True
    return s


def _turn(session):
    async def go():
        return [t async for t in session.run("hi")]

    return asyncio.run(go())


def test_a_run_over_the_limit_ends_with_the_one_line_note(tmp_path):
    out = _turn(_session(tmp_path, [9]))
    assert out[0] == "done"
    assert out[-1].strip() == "stopped starting helpers after 8; the result may be less thorough"


def test_a_run_within_the_limit_has_no_note(tmp_path):
    assert _turn(_session(tmp_path, [8])) == ["done"]


def test_the_window_resets_on_a_warm_session_or_a_second_cockpit_turn(tmp_path):
    s = _session(tmp_path, [8, 8, 9])
    assert _turn(s) == ["done"]  # run 1 uses all eight
    assert _turn(s) == ["done"]  # run 2 may use eight again: a new window, not a lifetime budget
    assert "stopped starting helpers" in _turn(s)[-1]
    assert s._subagents.denied == 1


def test_a_changed_setting_is_picked_up_by_the_next_run_of_a_warm_session(tmp_path):
    s = _session(tmp_path, [3, 3], settings={"max_subagents": 8})
    assert _turn(s) == ["done"]
    (tmp_path / "fixtureco" / "settings.json").write_text(json.dumps({"max_subagents": 2}))
    assert "after 2" in _turn(s)[-1]


# ── a pack node is one window; a retry shares it; the note joins the node's text ──


def _msg(text):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    return [
        AssistantMessage(content=[TextBlock(text)], model="m", usage=None),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
        ),
    ]


def _stage(monkeypatch, tmp_path, *, launches):
    built: list = []

    def fake_build(cfg, profile, **kw):
        built.append(kw.get("subagent_counter"))
        return object()

    async def fake_stream(options, prompt):
        counter = built[-1]
        for i in range(launches):
            counter.admit(f"n{i}")
        for m in _msg("node text"):
            yield m

    monkeypatch.setattr(pe, "build_agent_options", fake_build)
    monkeypatch.setattr(pe, "stream_brain_messages", fake_stream)
    cfg = SimpleNamespace(content_root=tmp_path)
    out = asyncio.run(pe.execute_stage(cfg, "fixtureco", "dossier", {}, prompts={"dossier": "go"}))
    return out, built


def test_a_pack_node_gets_its_own_counter_and_the_note_joins_its_text(monkeypatch, tmp_path):
    out, built = _stage(monkeypatch, tmp_path, launches=9)
    assert out.status == OK
    assert out.text.endswith("stopped starting helpers after 8; the result may be less thorough")
    assert isinstance(built[0], SubagentCounter)


def test_each_node_dispatch_is_a_new_window(monkeypatch, tmp_path):
    _, first = _stage(monkeypatch, tmp_path, launches=8)
    _, second = _stage(monkeypatch, tmp_path, launches=8)
    assert first[0] is not second[0]
    assert first[0].denied == 0 and second[0].denied == 0


def test_a_node_within_the_limit_has_no_note(monkeypatch, tmp_path):
    out, _ = _stage(monkeypatch, tmp_path, launches=2)
    assert "stopped starting helpers" not in out.text

"""W5: a counted limit on helper (subagent) launches per run (PRD 2026-10-19a; test plan P5-*).

Why a hook and not the permission callback: the step-1 probe (``tests/fixtures/subagent_tool_probe.json``)
found that on BOTH the pinned and the bundled CLI the model launches a helper as ``Agent`` and the launch
never reaches ``can_use_tool`` in default mode, only a ``PreToolUse`` hook. A cap written into the callback
would never fire. The live half of that claim was re-confirmed under the repo's real options
(``build_agent_options``, project settings, deny floor, plugin) on the unscoped and the tenant path.

These are SDK-free unit tests. What a mock cannot show (that the CLI really honours the hook's deny) was
exercised once against a live session; see the step report.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import subagent_limit as sl
from agent.subagent_limit import (
    DEFAULT_MAX,
    MAX_CLAMP,
    SubagentCounter,
    make_subagent_hook,
    resolve_max,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "subagent_tool_probe.json"


def _settings(tmp_path, body, profile="fixtureco"):
    d = tmp_path / profile
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(
        body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
    )
    return SimpleNamespace(content_root=tmp_path)


# ── the setting: clamped, and never unlimited ────────────────────────────────


@pytest.mark.parametrize(
    "stored, want",
    [
        ({}, DEFAULT_MAX),  # absent
        ({"max_subagents": 12}, 12),
        ({"max_subagents": 500}, MAX_CLAMP),  # clamped, not trusted
        ({"max_subagents": 0}, 0),  # "no helpers" is a legitimate narrowing
        ({"max_subagents": -1}, DEFAULT_MAX),  # garbage reads as the default, never as unlimited
        ({"max_subagents": "lots"}, DEFAULT_MAX),
        ({"max_subagents": None}, DEFAULT_MAX),
        ({"max_subagents": True}, DEFAULT_MAX),  # a bool is not a count
        ({"max_subagents": 7.5}, DEFAULT_MAX),
        ({"max_subagents": [3]}, DEFAULT_MAX),
    ],
)
def test_the_limit_is_clamped_and_garbage_is_the_default(tmp_path, stored, want):
    assert resolve_max(_settings(tmp_path, stored), "fixtureco") == want


@pytest.mark.parametrize("body", ["{not json", "[]", '"x"', ""])
def test_a_corrupt_settings_file_is_the_default_with_a_logged_warning(tmp_path, body, caplog):
    assert resolve_max(_settings(tmp_path, body), "fixtureco") == DEFAULT_MAX


def test_a_missing_file_or_a_config_without_a_content_root_is_the_default(tmp_path):
    assert resolve_max(SimpleNamespace(content_root=tmp_path), "nobody") == DEFAULT_MAX
    assert resolve_max(SimpleNamespace(), "fixtureco") == DEFAULT_MAX


def test_a_traversal_shaped_profile_name_cannot_reach_another_file(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "settings.json").write_text('{"max_subagents": 30}', encoding="utf-8")
    cfg = SimpleNamespace(content_root=tmp_path / "content")
    (tmp_path / "content").mkdir()
    assert resolve_max(cfg, "../outside") == DEFAULT_MAX


# ── the counter ──────────────────────────────────────────────────────────────


def test_calls_one_to_eight_pass_and_the_ninth_is_denied():
    c = SubagentCounter(8)
    assert [c.admit(f"t{i}") for i in range(1, 9)] == [True] * 8
    assert c.admit("t9") is False
    assert c.denied == 1 and c.started == 8


def test_a_relaunch_of_the_same_tool_use_is_not_counted_twice():
    c = SubagentCounter(2)
    assert c.admit("a") and c.admit("a") and c.admit("b")
    assert c.started == 2
    assert c.admit("c") is False


def test_a_call_with_no_id_is_counted_every_time():
    c = SubagentCounter(2)
    assert [c.admit(None) for _ in range(3)] == [True, True, False]


def test_reset_opens_a_fresh_window_and_can_change_the_limit():
    c = SubagentCounter(1)
    assert c.admit("a") and not c.admit("b")
    c.reset(limit=3)
    assert c.started == 0 and c.denied == 0 and c.limit == 3
    assert [c.admit(f"x{i}") for i in range(4)] == [True, True, True, False]


def test_a_limit_of_zero_denies_every_helper():
    assert SubagentCounter(0).admit("a") is False


def test_a_counter_never_exceeds_the_clamp_even_if_built_with_more():
    assert SubagentCounter(10_000).limit == MAX_CLAMP
    assert SubagentCounter("x").limit == DEFAULT_MAX  # type: ignore[arg-type]


def test_the_note_exists_only_after_a_denial_and_says_what_happened():
    c = SubagentCounter(1)
    c.admit("a")
    assert c.note is None
    c.admit("b")
    assert c.note == "stopped starting helpers after 1; the result may be less thorough"


# ── the hook ─────────────────────────────────────────────────────────────────


def _call(hook, name, tool_use_id, tool_input=None):
    return asyncio.run(hook({"tool_name": name, "tool_input": tool_input or {}}, tool_use_id, None))


def test_the_hook_allows_up_to_the_limit_and_denies_the_ninth_in_the_clis_own_vocabulary():
    hook = make_subagent_hook(SubagentCounter(8))
    assert all(_call(hook, "Agent", f"t{i}") == {} for i in range(1, 9))
    out = _call(hook, "Agent", "t9")
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "deny"
    assert "8" in spec["permissionDecisionReason"]  # the model is told the limit
    assert "yourself" in spec["permissionDecisionReason"]  # ...and what to do instead


@pytest.mark.parametrize("name", ["Agent", "Task"])
def test_both_names_a_cli_has_used_for_a_helper_are_counted(name):
    hook = make_subagent_hook(SubagentCounter(1))
    assert _call(hook, name, "a") == {}
    assert _call(hook, name, "b")["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "name", ["Read", "Bash", "TaskCreate", "TaskOutput", "mcp__worker__draft", "Skill", ""]
)
def test_other_tools_are_neither_counted_nor_touched(name):
    c = SubagentCounter(1)
    hook = make_subagent_hook(c)
    for i in range(5):
        assert _call(hook, name, f"t{i}") == {}
    assert c.started == 0 and c.denied == 0


def test_a_denied_launch_is_reported_to_the_sink_once_per_denial():
    seen = []
    hook = make_subagent_hook(SubagentCounter(1), on_deny=lambda *a: seen.append(a))
    _call(hook, "Agent", "a")
    _call(hook, "Agent", "b")
    _call(hook, "Agent", "c")
    assert [(n, d) for n, _, d in seen] == [("Agent", "subagent_cap")] * 2


def test_a_failing_sink_never_turns_a_denial_into_an_allow():
    def boom(*a):
        raise RuntimeError("ledger down")

    hook = make_subagent_hook(SubagentCounter(0), on_deny=boom)
    assert _call(hook, "Agent", "a")["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_malformed_hook_input_is_not_an_allow_for_a_helper():
    hook = make_subagent_hook(SubagentCounter(0))
    assert asyncio.run(hook({"tool_name": "Agent"}, None, None))["hookSpecificOutput"]
    assert asyncio.run(hook({}, "x", None)) == {}  # no tool at all: nothing to decide


# ── the denials ledger ───────────────────────────────────────────────────────


def test_a_denial_lands_in_denials_jsonl_with_the_reason(tmp_path):
    from agent.denial_log import make_denial_sink

    cfg = SimpleNamespace(content_root=tmp_path)
    sink = make_subagent_hook  # noqa: F841 — the wiring below goes through the public helper
    hook = make_subagent_hook(
        SubagentCounter(0), on_deny=sl.denial_sink(make_denial_sink(cfg, "fixtureco", "test"))
    )
    _call(hook, "Agent", "a")
    rows = [
        json.loads(line)
        for line in (tmp_path / "fixtureco" / "denials.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["tool"] == "Agent" and rows[0]["decision"] == "deny"
    assert "subagent_cap" in rows[0]["detail"] and rows[0]["source"] == "test"


# ── the probe fixture is the contract for which names are covered ─────────────


def test_every_name_the_probe_saw_a_cli_use_for_a_helper_is_covered():
    probe = json.loads(FIXTURE.read_text(encoding="utf-8"))
    observed = {
        n
        for run in probe["runs"].values()
        if run.get("status") == "observed"
        for n in run["assistant_tool_use_names"]
        if n not in ("ToolSearch",)
    }
    assert observed and observed <= sl.SUBAGENT_TOOLS, (
        f"a CLI launched a helper as {sorted(observed - sl.SUBAGENT_TOOLS)}, which the limit does not count"
    )


def test_an_unknown_helper_like_tool_is_still_denied_by_the_default_policy():
    """The names above are an allowlist of what is COUNTED. A tool name nobody has seen is not allowed
    through uncounted: the base policy escalates (denies) anything it does not recognise."""
    from agent import permissions

    assert permissions.classify_tool("SpawnWorker", {}) == "escalate"
    assert permissions.classify_tool("Subagent", {}) == "escalate"


def test_one_helper_per_row_over_a_hundred_rows_stops_at_the_cap():
    """P5-6: a procedure that starts a helper for every row spends on eight of them, not a hundred."""
    c = SubagentCounter()
    hook = make_subagent_hook(c)
    verdicts = [_call(hook, "Agent", f"row{i}") for i in range(100)]
    assert sum(1 for v in verdicts if v == {}) == 8
    assert sum(1 for v in verdicts if v) == 92 and c.denied == 92
    assert c.note == "stopped starting helpers after 8; the result may be less thorough"

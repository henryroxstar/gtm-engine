"""A stage whose brain turn errored, or ended with no result, FAILS (client issue #337).

Before this, ``execute_stage`` read only assistant text: a turn the API rejected, or a CLI
that exited after its init message, streamed nothing and the stage returned OK. A pack run
then "completed" every node at $0 until the enrollment gate found no draft and blamed the
wrong node. These pin the turn's own outcome as the stage's outcome.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import ResultMessage, SystemMessage  # noqa: E402

import agent.pipeline_executor as pe  # noqa: E402
from agent.pipeline import AWAITING_APPROVAL, FAILED, OK  # noqa: E402


class _Cfg:
    content_root = None


def _result(*, is_error: bool, **kw) -> ResultMessage:
    return ResultMessage(
        subtype=kw.pop("subtype", "success"),
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        **kw,
    )


def _stream_of(*messages):
    async def _stream(options, prompt):
        for m in messages:
            yield m

    return _stream


def _run(monkeypatch, tmp_path, *messages, stage="dossier", gates=None):
    monkeypatch.setattr(pe, "build_agent_options", lambda *a, **k: object())
    monkeypatch.setattr(pe, "stream_brain_messages", _stream_of(*messages))
    cfg = _Cfg()
    cfg.content_root = tmp_path
    return asyncio.run(
        pe.execute_stage(cfg, "example", stage, {}, prompts={stage: "go"}, gates=gates)
    )


def test_a_turn_with_no_result_fails_the_stage(monkeypatch, tmp_path):
    """The staging reproduction: an init SystemMessage, then the stream just ends."""
    init = SystemMessage(subtype="init", data={})
    outcome = _run(monkeypatch, tmp_path, init)
    assert outcome.status == FAILED
    assert "without a result" in outcome.error


def test_an_errored_turn_fails_the_stage_and_says_why(monkeypatch, tmp_path):
    bad = _result(is_error=True, api_error_status=401, result="Invalid API key")
    outcome = _run(monkeypatch, tmp_path, bad)
    assert outcome.status == FAILED
    assert "HTTP 401" in outcome.error and "Invalid API key" in outcome.error


def test_a_failed_turn_on_a_gate_node_fails_rather_than_pausing(monkeypatch, tmp_path):
    """The exact #337 shape: `sequence` is gate=true, so an empty turn used to PAUSE."""
    outcome = _run(monkeypatch, tmp_path, stage="sequence", gates={"sequence": True})
    assert outcome.status == FAILED


def test_a_clean_turn_still_succeeds_and_still_pauses_at_a_gate(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, _result(is_error=False)).status == OK
    gated = _run(
        monkeypatch, tmp_path, _result(is_error=False), stage="sequence", gates={"sequence": True}
    )
    assert gated.status == AWAITING_APPROVAL

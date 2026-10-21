"""A stage that ends waiting for an answer FAILS as ``needs_input`` (client issue #337 rerun).

A pack run has nobody to ask. The ``prospect`` step stopped to ask whether it could run on free
web search, ended its turn cleanly, and was recorded ``completed`` — so ``dossier``, ``outreach``
and ``quality`` ran on an empty list and spent $7.91 before the enrollment gate found no draft.
The skill's own unattended default is to stop and report ``web-floor-offered``; the engine must
treat that, and a refused ``AskUserQuestion``, as a failed step with the reason on it.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

import agent.pipeline_executor as pe  # noqa: E402
from agent.pipeline import FAILED, OK  # noqa: E402


class _Cfg:
    content_root = None


def _ok_result() -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
    )


def _assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="m")


def _run(monkeypatch, tmp_path, *messages):
    async def _stream(options, prompt):
        for m in messages:
            yield m

    monkeypatch.setattr(pe, "build_agent_options", lambda *a, **k: object())
    monkeypatch.setattr(pe, "stream_brain_messages", _stream)
    cfg = _Cfg()
    cfg.content_root = tmp_path
    return asyncio.run(pe.execute_stage(cfg, "example", "prospect", {}, prompts={"prospect": "go"}))


def test_an_ask_user_question_call_fails_the_stage_and_quotes_the_question(monkeypatch, tmp_path):
    ask = ToolUseBlock(
        id="t1",
        name="AskUserQuestion",
        input={"questions": [{"question": "Run it on free web search?"}]},
    )
    outcome = _run(monkeypatch, tmp_path, _assistant(ask), _ok_result())
    assert outcome.status == FAILED
    assert outcome.needs_input is True
    assert "Run it on free web search?" in outcome.error


def test_the_skills_unattended_stop_marker_fails_the_stage(monkeypatch, tmp_path):
    said = _assistant(TextBlock(text="Stopping: web-floor-offered. Connect a contacts source."))
    outcome = _run(monkeypatch, tmp_path, said, _ok_result())
    assert outcome.status == FAILED
    assert outcome.needs_input is True
    assert "web-floor-offered" in outcome.error


def test_a_normal_stage_is_untouched(monkeypatch, tmp_path):
    said = _assistant(TextBlock(text="Wrote 12 prospects."))
    outcome = _run(monkeypatch, tmp_path, said, _ok_result())
    assert outcome.status == OK
    assert outcome.needs_input is False

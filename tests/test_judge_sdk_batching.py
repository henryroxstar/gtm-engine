"""The SDK judge transport must survive more than one batch.

All fixtures invented (docs/RULES.md R9) — no real recipient appears here.

Regression: `del op` was the last statement INSIDE `_score_sdk`'s batch loop, so the name
was unbound after the first batch and the second raised ``UnboundLocalError``. Every list
longer than ``SDK_BATCH_ROWS`` (5) failed, and because the transport had only ever been
exercised on 1- and 2-row batches, nothing caught it. Found 2026-08-23 by running the judge
across nine drafted cells: the 5-row cells scored, the 6-row cell raised.
"""

from __future__ import annotations

import sys
import types

import pytest

from agent.mcp.judge.scoring import SDK_BATCH_ROWS


def _row(i: int) -> dict:
    return {
        "first": f"Robin{i}",
        "last": f"Ashford{i}",
        "email": f"robin{i}@brightpath{i}.example",
        "title": "CISO",
        "company": f"Brightpath{i}",
        "segment": "enterprise",
        "signal_clause": f"Brightpath{i} joined a supplier-agent working group",
        "why_now": f"Brightpath{i} joined a supplier-agent working group",
    }


class _Touch:
    subject = "supplier agents"
    body = "Hi {{First Name}},\n\n{{Why Now}}.\n\nHenry"


@pytest.fixture
def stub_sdk(monkeypatch):
    """Stand in for the Agent SDK so the test never spawns a subprocess or touches auth."""
    mod = types.ModuleType("claude_agent_sdk")

    class AssistantMessage:  # noqa: D401 - shape only
        content: list = []

    class TextBlock:
        text = ""

    mod.AssistantMessage, mod.TextBlock = AssistantMessage, TextBlock
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)

    import agent.session as session

    async def _stream(options, prompt):  # noqa: ARG001
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(session, "stream_brain_messages", _stream, raising=False)
    monkeypatch.setattr(session, "build_agent_options", lambda *a, **k: object(), raising=False)


@pytest.mark.parametrize("n_rows", [SDK_BATCH_ROWS, SDK_BATCH_ROWS + 1, SDK_BATCH_ROWS * 3])
def test_score_sdk_spans_multiple_batches(stub_sdk, monkeypatch, n_rows):
    """One record per row, across however many batches that takes.

    The single-batch case is the control: it passed before the fix too, so a test that only
    covered it would have reported a healthy transport.
    """
    import agent.mcp.judge.server as server

    monkeypatch.setattr(server, "budget_ok", lambda profile: True)
    monkeypatch.setattr(
        server,
        "_rendered",
        lambda row, touch, extra=None: ("supplier agents", "body", dict(row), 1),
    )

    import asyncio

    records, stopped = asyncio.run(
        server._score_sdk(
            [_row(i) for i in range(n_rows)],
            _Touch(),
            profile="",
            reverse=False,
            spec_path="spec.md",
            csv_path="rows.csv",
            repair_attempt=0,
            repaired=False,
            op="score_emails",
        )
    )
    assert len(records) == n_rows
    assert stopped == ""
    # Unreadable verdicts are recorded as unscored, never dropped — a silently short list is
    # the failure mode `check-complete` exists to catch.
    assert all(r.unscored and r.verdict == "" for r in records)


# --------------------------------------------------------- context upgrade + batch override


def test_rendered_context_carries_clause_evidence_capability_groups_and_reads_touch_number():
    import agent.mcp.judge.server as server

    touch = types.SimpleNamespace(number=3, day=5, subject="s", body="b {{First Name}}")
    row = {
        "title": "CISO",
        "company": "Acme",
        "segment": "enterprise",
        "signal_clause": "opened a program",
        "signal_evidence": "e" * 400,
        "first": "Ada",
    }
    subject, body, context, touch_n = server._rendered(
        row,
        touch,
        {"capability": "security-policy", "capability_groups": ["identity", "security-policy"]},
    )
    assert touch_n == 3, "touch.number is the field; the old getattr read a name that never existed"
    assert context["signal_clause"] == "opened a program" and len(context["signal_evidence"]) == 300
    assert context["capability"] == "security-policy" and context["capability_groups"] == [
        "identity",
        "security-policy",
    ]
    assert context["title"] == "CISO"
    legacy = types.SimpleNamespace(n=2, subject="", body="")
    assert server._rendered(row, legacy)[3] == 2


def test_judge_context_is_empty_without_a_capability_vocab(tmp_path):
    import agent.mcp.judge.server as server

    spec = tmp_path / "spec.md"
    spec.write_text("```\ncapability: security-policy\n```\n", encoding="utf-8")
    ctx = server.judge_context(str(spec), "")
    assert ctx == {} or ctx.get("capability_groups") == []


def test_env_batch_rows_is_clamped_and_invalid_values_refuse_loudly(monkeypatch):
    from agent.mcp.judge import scoring

    monkeypatch.delenv("JUDGE_SDK_BATCH_ROWS", raising=False)
    assert scoring._sdk_batch_rows() == 5
    monkeypatch.setenv("JUDGE_SDK_BATCH_ROWS", "1")
    assert scoring._sdk_batch_rows() == 1
    for bad in ("0", "50", "abc"):
        monkeypatch.setenv("JUDGE_SDK_BATCH_ROWS", bad)
        with pytest.raises(ValueError):
            scoring._sdk_batch_rows()

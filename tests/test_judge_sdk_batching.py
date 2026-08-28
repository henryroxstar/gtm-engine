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
        server, "_rendered", lambda row, touch: ("supplier agents", "body", dict(row), 1)
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

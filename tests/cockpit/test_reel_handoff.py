"""Contract tests for the reel hand-off trigger.

Gate 1's Approve button streams an augmented directive when the pending plan draft contains
a `format: "reel"` item, routing it into the creator pack's short-form-video render chain.
When no reel is present, the original directive is streamed byte-identically so existing
callback dispatch tests keep their contract.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    callback_update,
    make_cfg,
    recording_stream,
)

from cockpit import bot as botmod  # noqa: E402
from cockpit.gates import (  # noqa: E402
    _APPROVE_DIRECTIVE,
    _APPROVE_DIRECTIVE_RENDER,
)

CHAT_ID = 555
PROFILE = "example"


def _make_cockpit(tmp_path):
    return botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))


def _write_pending_draft(tmp_path, items):
    draft_dir = tmp_path / PROFILE / "plans" / ".pending"
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / "2026-W33.draft.json"
    draft_path.write_text(json.dumps(items), encoding="utf-8")
    return draft_path


def test_plan_approve_with_reel_streams_render_directive(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    _write_pending_draft(
        tmp_path,
        [
            {"id": "ci-202633-01", "format": "carousel", "platform": "linkedin"},
            {"id": "ci-202633-02", "format": "reel", "platform": "instagram"},
        ],
    )
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Plan promoted."))

    update, context, query = callback_update(CHAT_ID, "gate:plan:approve")
    asyncio.run(cockpit.on_callback(update, context))

    assert query.answered
    assert len(calls) == 1
    assert calls[0][1] == _APPROVE_DIRECTIVE_RENDER


def test_plan_approve_without_reel_streams_original_directive(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    _write_pending_draft(
        tmp_path,
        [
            {"id": "ci-202633-01", "format": "carousel", "platform": "linkedin"},
            {"id": "ci-202633-02", "format": "text", "platform": "linkedin"},
        ],
    )
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Plan promoted."))

    update, context, query = callback_update(CHAT_ID, "gate:plan:approve")
    asyncio.run(cockpit.on_callback(update, context))

    assert query.answered
    assert len(calls) == 1
    assert calls[0][1] == _APPROVE_DIRECTIVE


def test_plan_approve_with_missing_draft_streams_original_directive(monkeypatch, tmp_path):
    """No draft on disk → fail closed: do not authorize a render hand-off."""
    cockpit = _make_cockpit(tmp_path)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Plan promoted."))

    update, context, query = callback_update(CHAT_ID, "gate:plan:approve")
    asyncio.run(cockpit.on_callback(update, context))

    assert len(calls) == 1
    assert calls[0][1] == _APPROVE_DIRECTIVE


def test_plan_approve_with_malformed_draft_streams_original_directive(monkeypatch, tmp_path):
    """Malformed draft → fail closed: no silent render authorization."""
    cockpit = _make_cockpit(tmp_path)
    draft_dir = tmp_path / PROFILE / "plans" / ".pending"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "2026-W33.draft.json").write_text("not-json", encoding="utf-8")

    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Plan promoted."))

    update, context, query = callback_update(CHAT_ID, "gate:plan:approve")
    asyncio.run(cockpit.on_callback(update, context))

    assert len(calls) == 1
    assert calls[0][1] == _APPROVE_DIRECTIVE


def test_render_directive_is_original_plus_augmented_clause():
    """The augmented directive starts with the original and appends the render hand-off."""
    assert _APPROVE_DIRECTIVE_RENDER.startswith(_APPROVE_DIRECTIVE)
    assert "video-script" in _APPROVE_DIRECTIVE_RENDER
    assert "short-form-video" in _APPROVE_DIRECTIVE_RENDER
    assert _APPROVE_DIRECTIVE_RENDER != _APPROVE_DIRECTIVE

"""Tests for cockpit.hooks — operator hook-library UX."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakeMsg,
    callback_update,
    make_cfg,
    text_update,
)

from cockpit import bot as botmod  # noqa: E402
from gtm_core import hooks as hk  # noqa: E402

CHAT_ID = 555


def _make_cockpit(tmp_path, profiles_root, content_root):
    cfg = make_cfg(
        tmp_path, chat_ids={CHAT_ID}, profiles_root=profiles_root, content_root=content_root
    )
    return botmod.Cockpit(cfg)


def _setup_profile(profiles_root: Path, content_root: Path, profile: str) -> None:
    (profiles_root / profile / "knowledge").mkdir(parents=True)
    (content_root / profile).mkdir(parents=True)
    bank = hk.HookBank(
        hooks=[
            hk.Hook(id="proven-hook", angle="a", payoff_promise="b", status="proven"),
            hk.Hook(id="test-hook", angle="c", payoff_promise="d", status="test"),
            hk.Hook(id="candidate-hook", angle="e", payoff_promise="f", status="candidate"),
            hk.Hook(
                id="tired-hook",
                angle="g",
                payoff_promise="h",
                status="proven",
                max_impressions=10,
                fatigue_window_days=30,
            ),
        ]
    )
    hk.save_hooks(profiles_root, profile, bank)


def test_hooks_command_shows_counts_and_fatigue(monkeypatch, tmp_path):
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    _setup_profile(profiles_root, content_root, "example")

    cockpit = _make_cockpit(tmp_path, profiles_root, content_root)
    monkeypatch.setattr("cockpit.hooks.resolve_profiles_root", lambda: profiles_root)
    monkeypatch.setattr("cockpit.hooks.resolve_content_root", lambda: content_root)

    # Push tired-hook over its fatigue limit.
    from gtm_core import outcomes as oc

    oc.append_outcome(
        content_root,
        "example",
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 15,
            "tags": ["hook:tired-hook"],
        },
    )

    msg = FakeMsg(CHAT_ID)
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.cmd_hooks(update, context))

    text = msg.replies[0]
    assert "proven:" in text
    assert "<b>2</b>" in text
    assert "test:" in text
    assert "<b>1</b>" in text
    assert "candidate:" in text
    assert "tired-hook" in text
    assert msg.reply_kwargs[0].get("reply_markup") is not None


def test_hooks_revive_callback_resets_fatigue(monkeypatch, tmp_path):
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    _setup_profile(profiles_root, content_root, "example")

    cockpit = _make_cockpit(tmp_path, profiles_root, content_root)
    monkeypatch.setattr("cockpit.hooks.resolve_profiles_root", lambda: profiles_root)
    monkeypatch.setattr("cockpit.hooks.resolve_content_root", lambda: content_root)

    from gtm_core import outcomes as oc

    oc.append_outcome(
        content_root,
        "example",
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 15,
            "tags": ["hook:tired-hook"],
        },
    )

    update, context, query = callback_update(CHAT_ID, "hooks:revive:tired-hook")
    asyncio.run(cockpit.on_callback(update, context))

    assert query.answered
    assert any("revived" in t for t in query.text_edits)
    bank = hk.load_hooks(profiles_root, "example", content_root=content_root)
    assert bank.by_id("tired-hook").history[-1].event == "revived"


def test_hooks_promote_callback(monkeypatch, tmp_path):
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    _setup_profile(profiles_root, content_root, "example")

    # Seed a distiller promote candidate file.
    models_dir = content_root / "example" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "promote_candidates.json").write_text(
        json.dumps([{"hook_id": "test-hook", "engagement_rate": 0.05, "baseline_rate": 0.03}]),
        encoding="utf-8",
    )

    cockpit = _make_cockpit(tmp_path, profiles_root, content_root)
    monkeypatch.setattr("cockpit.hooks.resolve_profiles_root", lambda: profiles_root)
    monkeypatch.setattr("cockpit.hooks.resolve_content_root", lambda: content_root)

    update, context, query = callback_update(CHAT_ID, "hooks:promote:test-hook")
    asyncio.run(cockpit.on_callback(update, context))

    assert query.answered
    bank = hk.load_hooks(profiles_root, "example", content_root=content_root)
    assert bank.by_id("test-hook").status == "proven"


def test_hooks_demote_callback(monkeypatch, tmp_path):
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    _setup_profile(profiles_root, content_root, "example")

    models_dir = content_root / "example" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "demote_candidates.json").write_text(
        json.dumps([{"hook_id": "proven-hook", "engagement_rate": 0.01, "baseline_rate": 0.03}]),
        encoding="utf-8",
    )

    cockpit = _make_cockpit(tmp_path, profiles_root, content_root)
    monkeypatch.setattr("cockpit.hooks.resolve_profiles_root", lambda: profiles_root)
    monkeypatch.setattr("cockpit.hooks.resolve_content_root", lambda: content_root)

    update, context, query = callback_update(CHAT_ID, "hooks:demote:proven-hook")
    asyncio.run(cockpit.on_callback(update, context))

    assert query.answered
    bank = hk.load_hooks(profiles_root, "example", content_root=content_root)
    assert bank.by_id("proven-hook").status == "test"

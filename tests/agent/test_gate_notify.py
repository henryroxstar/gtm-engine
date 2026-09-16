"""Unit tests for ``agent.gate_notify.push_gate1`` — the Gate 1 Telegram push.

The message is sent with ``parse_mode=HTML``, so the two data-controlled fields
(``profile``, ``run_id``) must be escaped. Unescaped, a ``<`` or ``&`` makes
Telegram reject the whole message — and the failure is swallowed by design, so
the operator would simply never learn the run had paused at Gate 1.

Network-free: httpx is monkeypatched, so no real Bot API call is made.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import types

import pytest

pytest.importorskip("httpx", reason="httpx not installed")

from agent import gate_notify  # noqa: E402


@dataclasses.dataclass
class _Cfg:
    telegram_bot_token: str = "test-token"


class _Resp:
    status_code = 200


def _patch_httpx(monkeypatch, captured: dict):
    """Replace httpx.AsyncClient so push_gate1's lazy import gets our fake."""

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            captured["url"] = url
            captured["data"] = data or {}
            return _Resp()

    fake = types.ModuleType("httpx")
    fake.AsyncClient = _Client
    monkeypatch.setitem(sys.modules, "httpx", fake)


def _run(monkeypatch, profile: str, run_id: str) -> dict:
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    monkeypatch.setattr(gate_notify, "load_gate1_chat_id", lambda *a: 12345, raising=False)
    monkeypatch.setattr("agent.profiles.load_gate1_chat_id", lambda *a, **kw: 12345, raising=False)
    from pathlib import Path

    asyncio.run(gate_notify.push_gate1(_Cfg(), Path("/nonexistent"), profile, run_id))
    return captured


def test_profile_and_run_id_are_html_escaped(monkeypatch):
    captured = _run(monkeypatch, profile="<b>evil</b>&co", run_id="run<script>")
    text = captured["data"]["text"]

    assert captured["data"]["parse_mode"] == "HTML"
    # The injected markup is neutralised…
    assert "<b>evil</b>&co" not in text
    assert "<script>" not in text
    # …and present in escaped form.
    assert "&lt;b&gt;evil&lt;/b&gt;&amp;co" in text
    assert "run&lt;script&gt;" in text
    # The message's own structural tags survive.
    assert text.startswith("<b>[")
    assert "<code>" in text


def test_ordinary_values_are_unchanged(monkeypatch):
    """Escaping must be invisible for every real profile name and run id."""
    captured = _run(monkeypatch, profile="acme", run_id="2026-08-13-radar-01")
    text = captured["data"]["text"]
    assert "<b>[acme] Gate 1 — plan ready for review</b>" in text
    assert "<code>2026-08-13-radar-01</code>" in text


def test_no_send_without_a_token(monkeypatch):
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    asyncio.run(gate_notify.push_gate1(_Cfg(telegram_bot_token=""), None, "p", "r"))
    assert captured == {}


# --------------------------------------------------------------------------- push_optout_alert


def _optout_match(**overrides):
    from gtm_core.optout_watch import OptOutMatch

    defaults = {
        "thread_id": "t1",
        "email": "jordan@brackenhealth.example",
        "subject": "re: agents on regulated data",
        "message_ts": "2026-08-11T14:00:00Z",
        "snippet": "Unsubscribe",
        "direction_known": True,
    }
    defaults.update(overrides)
    return OptOutMatch(**defaults)


def _run_optout(monkeypatch, profile: str, match) -> dict:
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    monkeypatch.setattr("agent.profiles.load_gate1_chat_id", lambda *a, **kw: 12345, raising=False)
    from pathlib import Path

    asyncio.run(gate_notify.push_optout_alert(_Cfg(), Path("/nonexistent"), profile, match))
    return captured


def test_optout_alert_quotes_the_reply_and_never_claims_suppression(monkeypatch):
    captured = _run_optout(monkeypatch, "example", _optout_match())
    text = captured["data"]["text"]
    assert captured["data"]["parse_mode"] == "HTML"
    assert "jordan@brackenhealth.example" in text
    assert "Unsubscribe" in text
    # Must never claim the person IS suppressed — DNC has no removal API, so a false
    # positive here would be reported as done when it is not.
    assert "not yet suppressed" in text.lower()
    assert "Add" in text and "DNC" in text


def test_optout_alert_reply_snippet_is_html_escaped(monkeypatch):
    """The reply body is untrusted content — a crafted reply must not break the
    Telegram message or inject markup."""
    match = _optout_match(snippet="<script>unsubscribe</script>&stop")
    captured = _run_optout(monkeypatch, "example", match)
    text = captured["data"]["text"]
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_optout_alert_no_send_without_a_token(monkeypatch):
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    from pathlib import Path

    asyncio.run(
        gate_notify.push_optout_alert(
            _Cfg(telegram_bot_token=""), Path("/nonexistent"), "example", _optout_match()
        )
    )
    assert captured == {}


# --------------------------------------------------------------------------- push_pack_gate


def _run_pack_gate(monkeypatch, profile: str, run_id: str, node_ids: list[str]) -> dict:
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    monkeypatch.setattr("agent.profiles.load_gate1_chat_id", lambda *a, **kw: 12345, raising=False)
    from pathlib import Path

    asyncio.run(
        gate_notify.push_pack_gate(
            _Cfg(),
            Path("/nonexistent"),
            profile,
            run_id,
            node_ids,
            pack="prospecting",
            variant="prospect-outreach",
        )
    )
    return captured


def test_pack_gate_names_the_node_and_the_resolve_command(monkeypatch):
    captured = _run_pack_gate(monkeypatch, "acme", "r-1", ["sequence"])
    text = captured["data"]["text"]
    assert captured["data"]["parse_mode"] == "HTML"
    assert "sequence" in text
    assert "--gate-decision approve" in text
    assert "--pack prospecting" in text
    assert "--variant prospect-outreach" in text
    assert "--run-id r-1" in text


def test_pack_gate_lists_multiple_nodes(monkeypatch):
    captured = _run_pack_gate(monkeypatch, "acme", "r-1", ["outreach", "sequence"])
    text = captured["data"]["text"]
    assert "outreach, sequence" in text


def test_pack_gate_fields_are_html_escaped(monkeypatch):
    captured = _run_pack_gate(monkeypatch, "<b>evil</b>", "r<script>", ["node<script>"])
    text = captured["data"]["text"]
    assert "<script>" not in text.replace("<code>", "").replace("</code>", "").replace(
        "<b>", ""
    ).replace("</b>", "")
    assert "&lt;script&gt;" in text


def test_pack_gate_no_send_without_a_token(monkeypatch):
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    from pathlib import Path

    asyncio.run(
        gate_notify.push_pack_gate(
            _Cfg(telegram_bot_token=""),
            Path("/nonexistent"),
            "p",
            "r",
            ["n"],
            pack="prospecting",
            variant="prospect-outreach",
        )
    )
    assert captured == {}


def test_pack_gate_no_send_without_a_configured_chat_id(monkeypatch):
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    monkeypatch.setattr("agent.profiles.load_gate1_chat_id", lambda *a, **kw: None, raising=False)
    from pathlib import Path

    asyncio.run(
        gate_notify.push_pack_gate(
            _Cfg(),
            Path("/nonexistent"),
            "p",
            "r",
            ["n"],
            pack="prospecting",
            variant="prospect-outreach",
        )
    )
    assert captured == {}


def test_optout_alert_no_send_without_a_configured_chat_id(monkeypatch):
    captured: dict = {}
    _patch_httpx(monkeypatch, captured)
    monkeypatch.setattr("agent.profiles.load_gate1_chat_id", lambda *a, **kw: None, raising=False)
    from pathlib import Path

    asyncio.run(
        gate_notify.push_optout_alert(_Cfg(), Path("/nonexistent"), "example", _optout_match())
    )
    assert captured == {}

"""A4 — the repo's own Saleshandy server refuses to enrol, whatever agent calls it.

Until now the two enrolment wrappers on `agent/mcp/saleshandy/server.py` worked for any
caller and were "denied" only by `agent/permissions.py`, which is a Claude Agent SDK callback.
Antigravity, Cursor and Codex do not run it, so a session there that loaded this server could
enrol people with no gate at all. The wrappers now refuse unconditionally; the only path that
enrols is `agent/email_dispatch.py`, which calls the underlying request functions directly.

Fixtures are fictional (§R9): `example.test` addresses, made-up ids.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import email_dispatch
from agent.mcp.saleshandy import server
from agent.permissions import _ENROLL_PREFIXES, _SEND_STATE_PREFIXES

ENROL_WRAPPERS = ("add_leads_to_sequence", "import_prospects_to_sequence")

#: Benign arguments for each wrapper — what a confused or hostile agent might send.
_WRAPPER_ARGS = {
    "add_leads_to_sequence": {"lead_ids": [1, 2], "sequence_id": "seq-a", "step_id": "step-a"},
    "import_prospects_to_sequence": {
        "prospect_list": [{"fields": [{"id": "f1", "value": "pat@example.test"}]}],
        "step_id": "step-a",
    },
}


def _forbid_network(monkeypatch):
    """Any HTTP-shaped call from the server module fails the test loudly."""

    async def _boom(*_a, **_k):  # pragma: no cover - runs only if the property breaks
        raise AssertionError("a refused enrolment reached the Saleshandy HTTP layer")

    monkeypatch.setattr(server, "_call", _boom)
    for name in ("_add_leads_to_sequence_request", "_import_prospects_to_sequence_request"):
        monkeypatch.setattr(server, name, _boom)


@pytest.mark.parametrize("name", ENROL_WRAPPERS)
def test_enrol_wrapper_refuses_in_plain_words_with_a_key_configured(name, monkeypatch):
    monkeypatch.setenv("SALESHANDY_API_KEY", "key-for-test-only")
    _forbid_network(monkeypatch)
    out = asyncio.run(getattr(server, name)(**_WRAPPER_ARGS[name]))
    assert out.startswith("[saleshandy-error] I haven't loaded anyone."), out
    assert "That's because" in out and "You can" in out
    assert "Nothing" in out  # the cost sentence: nothing loaded, nothing spent


@pytest.mark.parametrize("name", ENROL_WRAPPERS)
def test_enrol_wrapper_refuses_even_when_no_key_is_configured(name, monkeypatch):
    """No key must not read as 'not configured, try elsewhere' — it is the same refusal."""
    monkeypatch.delenv("SALESHANDY_API_KEY", raising=False)
    _forbid_network(monkeypatch)
    out = asyncio.run(getattr(server, name)(**_WRAPPER_ARGS[name]))
    assert out != server.NOT_CONFIGURED
    assert out.startswith("[saleshandy-error] I haven't loaded anyone."), out


@pytest.mark.parametrize("name", ENROL_WRAPPERS)
def test_refusal_does_not_echo_the_key_or_the_people(name, monkeypatch):
    monkeypatch.setenv("SALESHANDY_API_KEY", "key-for-test-only")
    _forbid_network(monkeypatch)
    out = asyncio.run(getattr(server, name)(**_WRAPPER_ARGS[name]))
    assert "key-for-test-only" not in out
    assert "pat@example.test" not in out


def _tool_functions():
    """Every `@mcp.tool()` function on the module, by name (the surface the agent sees)."""
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for dec in node.decorator_list:
                func = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(func, "attr", None) == "tool":
                    names.append(node.name)
    return {n: getattr(server, n) for n in names}


def _dummy_kwargs(fn):
    kwargs = {}
    for pname, param in inspect.signature(fn).parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        ann = str(param.annotation)
        kwargs[pname] = [] if "list" in ann else ("x" if "str" in ann else 1)
    return kwargs


def test_every_registered_enrol_or_status_tool_refuses(monkeypatch):
    """Derived from `agent.permissions` — the one home of 'what changes who is loaded or
    whether a sequence sends' — so a NEW wrapper named in those families cannot be added
    here without refusing, and the test needs no edit when the lists grow."""
    monkeypatch.setenv("SALESHANDY_API_KEY", "key-for-test-only")
    _forbid_network(monkeypatch)
    families = tuple(_ENROLL_PREFIXES) + tuple(_SEND_STATE_PREFIXES)
    guarded = {n: fn for n, fn in _tool_functions().items() if n.startswith(families)}
    assert set(guarded) >= set(ENROL_WRAPPERS), "the premise: both enrol wrappers are registered"
    for name, fn in guarded.items():
        out = asyncio.run(fn(**_dummy_kwargs(fn)))
        assert out.startswith("[saleshandy-error] I haven't loaded anyone."), (name, out)


def test_the_read_and_staging_tools_are_not_collateral_damage(monkeypatch):
    """The refusal is about loading people, not about the server: reads still work."""
    monkeypatch.setenv("SALESHANDY_API_KEY", "key-for-test-only")
    seen = []

    async def _fake_call(method, path, **kwargs):
        seen.append((method, path))
        return "[]"

    monkeypatch.setattr(server, "_call", _fake_call)
    asyncio.run(server.list_sequences())
    assert seen and seen[0][0] == "GET"


# ── the dispatcher is unaffected: it calls the request functions, never the wrappers ──


def test_the_dispatcher_imports_the_request_functions_not_the_tool_wrappers():
    src = Path(email_dispatch.__file__).read_text(encoding="utf-8")
    imported = {
        alias.name
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.ImportFrom) and node.module == "agent.mcp.saleshandy.server"
        for alias in node.names
    }
    assert {"_add_leads_to_sequence_request", "_import_prospects_to_sequence_request"} <= imported
    assert not imported & set(ENROL_WRAPPERS), "the dispatcher must never route through a wrapper"


_COPY = [{"step_id": "step-1", "variants": [{"subject": "s", "content": "<p>c</p>"}]}]
_IMPORT_DRAFT = {
    "tool": "import_prospects_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": _COPY,
    "prospect_list": [{"Email": "pat@example.test", "First Name": "Pat"}],
}
_ADD_DRAFT = {
    "tool": "add_leads_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": _COPY,
    "lead_ids": [11, 22],
}


def _approve_everything_else(monkeypatch):
    """Stub only the checks that read other systems; the HTTP layer below is the subject."""

    async def _none(*_a, **_k):
        return None

    async def _rows(_key, prospects):
        return [{"fields": [{"id": "f-email", "value": p["Email"]}]} for p in prospects], None

    monkeypatch.setattr(email_dispatch, "_lane_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(email_dispatch, "_live_copy_refusal", _none)
    monkeypatch.setattr(email_dispatch, "_import_rows", _rows)
    monkeypatch.setattr(email_dispatch, "_load_refusal", lambda *_a, **_k: None, raising=False)


@pytest.mark.parametrize(
    ("draft", "method", "path"),
    [
        (_IMPORT_DRAFT, "POST", "/prospects/import"),
        (_ADD_DRAFT, "POST", "/leads/bulk-actions/add-to-sequence"),
    ],
)
def test_the_approved_dispatch_still_reaches_the_endpoint_the_wrapper_refuses(
    draft, method, path, monkeypatch, tmp_path
):
    _approve_everything_else(monkeypatch)
    calls = []

    async def _fake_call(m, p, *, json_body=None, api_key=None, **_k):
        calls.append((m, p, api_key))
        return '{"ok": true}'

    monkeypatch.setattr(server, "_call", _fake_call)
    cfg = SimpleNamespace(saleshandy_api_key="dispatch-key", content_root=tmp_path)
    ledgers = SimpleNamespace(profile="example", history=[])
    ledgers.append_history = ledgers.history.append  # type: ignore[attr-defined]
    outcome = asyncio.run(email_dispatch.dispatch_approved_enrollment(cfg, ledgers, draft=draft))
    assert outcome.ok and outcome.status == "enrolled", outcome
    assert calls == [(method, path, "dispatch-key")]

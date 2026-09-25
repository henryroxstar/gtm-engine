"""Tests for the SC9 DNC-add dispatcher (agent.dnc_dispatch).

This is a function whose whole job is to say no, so most of these are refusals:

  - the kill switch is closed by default, and an unrecognised value leaves it closed;
  - the brain can NARROW an approved list against the ledger, never widen it;
  - the DNC list id is resolved here and is never taken from the draft;
  - a read-back that does not show the address records NOTHING;
  - a dry run is structurally unable to reach the provider;
  - there is no removal path, asserted structurally as well as behaviourally.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from agent import dnc_dispatch


@dataclasses.dataclass
class _Cfg:
    content_root: Path
    saleshandy_api_key: str | None = "test-key"


class _Ledgers:
    """The two surfaces the dispatcher uses: `iter_history` and `append_history`."""

    def __init__(self, rows=(), profile="example"):
        self._rows = list(rows)
        self.written: list[dict] = []
        self.profile = profile

    def iter_history(self):
        return iter(self._rows)

    def append_history(self, record):
        self.written.append(record)
        self._rows.append(record)


def _optout(email, event="optout_detected"):
    return {"event": event, "email": email}


#: The default single confirmed-in-one-page read-back: {currentPage: totalPages}, per
#: the VERIFIED get_dnc_items envelope this repo's own server.py records — the paging
#: fields are load-bearing, not decoration: `_read_back` refuses on their absence.
_ONE_PAGE_META = {"totalItems": 1, "currentPage": 1, "itemsPerPage": 100, "totalPages": 1}


def _stub_calls(monkeypatch, *, lists=None, add=None, items=None, seen=None):
    """Replace the connector's `_call` with a scripted router.

    Routed on the VERIFIED paths (`GET /dnc`, `GET /dnc/{id}`,
    `agent/mcp/saleshandy/server.py`'s own live-tested comments) — this stub used to route
    on `/dnc-lists`/`.../items`, the wrong paths `dnc_dispatch.py` itself called before the
    2026-09-24 SC9b fix, so it was passing against its own bug rather than the API.
    """
    seen = seen if seen is not None else []
    # Unless `items` scripts the read, the list behaves like the real one: empty until a
    # successful POST adds to it. (The dispatcher pre-reads, so a list that already held
    # the address would — correctly — skip the POST these tests are about.)
    held: list[str] = []

    async def fake_call(method, path, *, params=None, json_body=None, api_key=None):
        seen.append((method, path, json_body))
        if method == "GET" and path == "/dnc":
            return lists if lists is not None else json.dumps({"payload": [{"id": "L1"}]})
        if method == "POST" and path == "/dnc":  # _add_items — same path, POST
            result = add if add is not None else json.dumps({"message": "ok"})
            if not result.startswith("[saleshandy-error]"):
                held.extend(json_body["items"])
            return result
        if path.startswith("/dnc/"):
            if items is not None:
                return items
            details = [{"value": v, "type": "email"} for v in held]
            return json.dumps({"payload": {"dncListDetails": details, "meta": _ONE_PAGE_META}})
        return "{}"

    monkeypatch.setattr("agent.mcp.saleshandy.server._call", fake_call)
    return seen


def _run(cfg, ledgers, draft, **kw):
    return asyncio.run(dnc_dispatch.dispatch_approved_dnc_add(cfg, ledgers, draft=draft, **kw))


# --- the kill switch ------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "off", "TRUEISH", "  "])
def test_the_switch_is_closed_unless_it_says_a_recognised_true(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("GTM_DNC_ADD_ENABLED", raising=False)
    else:
        monkeypatch.setenv("GTM_DNC_ADD_ENABLED", value)
    assert dnc_dispatch.enabled() is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", " On "])
def test_the_switch_recognises_its_closed_list(monkeypatch, value):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", value)
    assert dnc_dispatch.enabled() is True


def test_with_the_switch_off_nothing_is_attempted(tmp_path, monkeypatch):
    """Test plan §3.C: an approved gate with the switch unset writes nothing to the
    provider AND nothing to suppression.csv, and says so."""
    monkeypatch.delenv("GTM_DNC_ADD_ENABLED", raising=False)
    seen = _stub_calls(monkeypatch)
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is False
    assert out.status == "disabled"
    assert not seen, "the switch is off and the dispatcher still called the provider"
    assert not ledgers.written
    assert not (tmp_path / "example" / "prospects" / ".pool" / "suppression.csv").exists()
    assert "switched off" in out.operator_line()


def test_the_add_matches_the_vendor_spec(tmp_path, monkeypatch):
    """`DncController_addItemsToDncList` (open-api.saleshandy.com/api-doc-json, read
    2026-09-24): `POST /v1/dnc`, body `{"items": [string], "dncListId": string}`. The first
    live add sent `POST /dnc-lists/items` with object items and got a 404."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    seen = _stub_calls(monkeypatch)
    out = _run(
        _Cfg(tmp_path),
        _Ledgers([_optout("dana@acme.example")]),
        {"addresses": ["dana@acme.example"]},
    )
    assert out.ok is True
    posts = [(p, b) for m, p, b in seen if m == "POST"]
    assert posts == [("/dnc", {"dncListId": "L1", "items": ["dana@acme.example"]})]


# --- the key --------------------------------------------------------------------------


def test_a_missing_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    seen = _stub_calls(monkeypatch)
    out = _run(
        _Cfg(tmp_path, saleshandy_api_key=None),
        _Ledgers([_optout("dana@acme.example")]),
        {"addresses": ["dana@acme.example"]},
    )
    assert out.status == "not_configured"
    assert not seen


# --- narrowing only ---------------------------------------------------------------------


def test_an_address_with_no_ledger_row_is_refused(tmp_path, monkeypatch):
    """The brain can narrow, never widen. A crafted reply that talks the model into naming
    a competitor cannot suppress them, because the evidence comes from the ledger."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    seen = _stub_calls(monkeypatch)
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(
        _Cfg(tmp_path),
        ledgers,
        {"addresses": ["dana@acme.example", "rival@competitor.example"]},
    )
    assert out.ok is True
    assert out.added == ("dana@acme.example",)
    assert out.refused == ("rival@competitor.example",)
    sent = [body for _m, path, body in seen if (_m, path) == ("POST", "/dnc")]
    assert sent, "nothing was sent at all"
    values = sent[0]["items"]
    assert values == ["dana@acme.example"]
    assert "rival@competitor.example" not in json.dumps(sent)


def test_a_draft_naming_only_unevidenced_addresses_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    seen = _stub_calls(monkeypatch)
    out = _run(_Cfg(tmp_path), _Ledgers([]), {"addresses": ["rival@competitor.example"]})
    assert out.status == "nothing_to_do"
    assert not [p for _m, p, _b in seen if (_m, p) == ("POST", "/dnc")]


def test_an_already_mirrored_address_is_not_re_added(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    _stub_calls(monkeypatch)
    ledgers = _Ledgers(
        [_optout("dana@acme.example"), {"event": "dnc_added", "email": "dana@acme.example"}]
    )
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.status == "nothing_to_do"


def test_an_unreadable_optout_is_also_a_candidate(tmp_path, monkeypatch):
    """SC6 rows count: a reply this system could not read is treated as an ambiguous
    opt-out, and mirroring it is the conservative direction."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    _stub_calls(
        monkeypatch,
        items=json.dumps(
            {
                "payload": {
                    "dncListDetails": [{"value": "quinn@brightpath.example", "type": "email"}],
                    "meta": _ONE_PAGE_META,
                }
            }
        ),
    )
    ledgers = _Ledgers([_optout("quinn@brightpath.example", event="optout_unreadable")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["quinn@brightpath.example"]})
    assert out.ok is True
    assert out.added == ("quinn@brightpath.example",)


def test_open_candidates_is_derived_from_the_ledger_not_the_draft():
    ledgers = _Ledgers(
        [
            _optout("a@acme.example"),
            _optout("b@acme.example", event="optout_unreadable"),
            {"event": "dnc_added", "email": "a@acme.example"},
            {"event": "signal", "email": "c@acme.example"},
        ]
    )
    assert dnc_dispatch.open_candidates(ledgers) == {"b@acme.example"}


# --- the list id is never the draft's to choose -------------------------------------------


def test_the_list_id_is_never_taken_from_the_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    monkeypatch.delenv("SALESHANDY_DNC_LIST_ID", raising=False)
    seen = _stub_calls(monkeypatch)
    out = _run(
        _Cfg(tmp_path),
        _Ledgers([_optout("dana@acme.example")]),
        {"addresses": ["dana@acme.example"], "dnc_list_id": "ATTACKER-LIST"},
    )
    assert out.ok is True
    sent = [b for _m, p, b in seen if (_m, p) == ("POST", "/dnc")]
    assert sent[0]["dncListId"] == "L1"
    assert "ATTACKER-LIST" not in json.dumps(sent)


def test_several_lists_without_the_env_refuses_rather_than_guessing(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    monkeypatch.delenv("SALESHANDY_DNC_LIST_ID", raising=False)
    seen = _stub_calls(monkeypatch, lists=json.dumps({"payload": [{"id": "L1"}, {"id": "L2"}]}))
    out = _run(
        _Cfg(tmp_path),
        _Ledgers([_optout("dana@acme.example")]),
        {"addresses": ["dana@acme.example"]},
    )
    assert out.ok is False
    assert "refusing to guess" in out.detail
    assert not [p for _m, p, _b in seen if (_m, p) == ("POST", "/dnc")]


def test_a_configured_list_id_that_does_not_exist_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    monkeypatch.setenv("SALESHANDY_DNC_LIST_ID", "L9")
    seen = _stub_calls(monkeypatch)
    out = _run(
        _Cfg(tmp_path),
        _Ledgers([_optout("dana@acme.example")]),
        {"addresses": ["dana@acme.example"]},
    )
    assert out.ok is False
    assert not [p for _m, p, _b in seen if (_m, p) == ("POST", "/dnc")]


# --- the read-back ------------------------------------------------------------------------


def test_an_add_the_read_back_does_not_confirm_records_nothing(tmp_path, monkeypatch):
    """Test plan §4.7: 'add reported OK but absent on re-read' must record no `dnc_added`
    row and no ledger entry, so the next run retries rather than believing a phantom."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    _stub_calls(
        monkeypatch,
        add=json.dumps({"message": "ok"}),
        # the re-read shows nothing, WITH meta -- a genuinely-empty confirmed page, not
        # an unreadable one, so this exercises the "read worked, address absent" path
        # rather than the "no meta, refuse" path covered separately below.
        items=json.dumps({"payload": {"dncListDetails": [], "meta": _ONE_PAGE_META}}),
    )
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is False
    assert out.status == "dnc_add_failed"
    assert not [r for r in ledgers.written if r.get("event") == "dnc_added"]
    assert not (tmp_path / "example" / "prospects" / ".pool" / "suppression.csv").exists()


def test_an_unreadable_read_back_also_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    _stub_calls(monkeypatch, items="[saleshandy-error] HTTP 500")
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is False
    assert not [r for r in ledgers.written if r.get("event") == "dnc_added"]


def test_an_address_already_on_the_list_is_not_resent_and_is_recorded(tmp_path, monkeypatch):
    """The provider 400s a request naming an existing entry ("Inserted email or domain
    already exist in this list", live 2026-09-24), so an opt-out someone already added by
    hand must not be POSTed again — it is confirmed by the read and recorded."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    seen = _stub_calls(
        monkeypatch,
        add="[saleshandy-error] HTTP 400",
        items=json.dumps(
            {
                "payload": {
                    "dncListDetails": [{"value": "dana@acme.example", "type": "email"}],
                    "meta": _ONE_PAGE_META,
                }
            }
        ),
    )
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is True
    assert not [p for m, p, _b in seen if m == "POST"]
    assert [r["email"] for r in ledgers.written if r.get("event") == "dnc_added"] == [
        "dana@acme.example"
    ]


def test_only_the_addresses_not_yet_held_are_posted(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    seen = []
    held = ["dana@acme.example"]

    async def fake_call(method, path, *, params=None, json_body=None, api_key=None):
        seen.append((method, path, json_body))
        if method == "GET" and path == "/dnc":
            return json.dumps({"payload": [{"id": "L1"}]})
        if method == "POST" and path == "/dnc":
            if set(json_body["items"]) & set(held):
                return "[saleshandy-error] HTTP 400"
            held.extend(json_body["items"])
            return json.dumps({"message": "ok"})
        details = [{"value": v, "type": "email"} for v in held]
        return json.dumps({"payload": {"dncListDetails": details, "meta": _ONE_PAGE_META}})

    monkeypatch.setattr("agent.mcp.saleshandy.server._call", fake_call)
    ledgers = _Ledgers([_optout("dana@acme.example"), _optout("eli@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example", "eli@acme.example"]})
    assert out.ok is True
    assert [b["items"] for m, _p, b in seen if m == "POST"] == [["eli@acme.example"]]
    assert out.added == ("dana@acme.example", "eli@acme.example")


def test_a_failed_add_call_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    _stub_calls(monkeypatch, add="[saleshandy-error] HTTP 422")
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is False
    assert not ledgers.written


# --- SC9b: read-back paging on the VERIFIED endpoint ---------------------------------------
#
# The endpoint fix (`/dnc-lists/{id}/items` -> `/dnc/{id}`) is exercised implicitly by
# every test above, since the stub now only answers on the correct path -- a regression
# back to the old path would make every one of them fail with the stub's `{}` fallback.
# These four are the paging behaviour specifically: the thing SC9b actually adds.


def test_the_read_back_pages_to_find_an_address_past_page_one(tmp_path, monkeypatch):
    """The live list already holds 57 items; a newly added address is not guaranteed to
    land on page 1. This is the defect SC9b exists to fix."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    seen = []
    posted = []

    async def fake_call(method, path, *, params=None, json_body=None, api_key=None):
        seen.append((method, path, params))
        if method == "GET" and path == "/dnc":
            return json.dumps({"payload": [{"id": "L1"}]})
        if method == "POST" and path == "/dnc":
            posted.append(json_body)
            return json.dumps({"message": "ok"})
        if path == "/dnc/L1":
            page = (params or {}).get("page", 1)
            if page == 1:
                # A full page of OTHER addresses -- not the one just added.
                details = [{"value": f"other{i}@acme.example", "type": "email"} for i in range(100)]
                meta = {"currentPage": 1, "totalPages": 2}
            else:
                details = [{"value": "dana@acme.example", "type": "email"}] if posted else []
                meta = {"currentPage": 2, "totalPages": 2}
            return json.dumps({"payload": {"dncListDetails": details, "meta": meta}})
        return "{}"

    monkeypatch.setattr("agent.mcp.saleshandy.server._call", fake_call)
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is True
    assert out.added == ("dana@acme.example",)
    read_pages = [p.get("page") for _m, path, p in seen if path == "/dnc/L1"]
    # Two full passes: the pre-read (what is already held) and the confirming read-back.
    assert read_pages == [1, 2, 1, 2], "did not page past the first page"


def test_a_read_back_page_with_no_paging_signal_refuses_rather_than_confirms(tmp_path, monkeypatch):
    """A page missing meta.currentPage/totalPages cannot be proven complete. Confirming
    off it anyway is exactly the 'short read looks like a smaller answer' trap this fix
    closes -- so it must refuse, even though the address IS present on the one page read."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    _stub_calls(
        monkeypatch,
        items=json.dumps(
            {"payload": {"dncListDetails": [{"value": "dana@acme.example", "type": "email"}]}}
        ),
    )
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is False
    assert not [r for r in ledgers.written if r.get("event") == "dnc_added"]


def test_a_read_back_that_never_reaches_the_last_page_refuses(tmp_path, monkeypatch):
    """Every page reports MORE pages remain, forever -- coverage cannot be established,
    so the whole read-back refuses rather than banking whatever was seen so far."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")

    async def fake_call(method, path, *, params=None, json_body=None, api_key=None):
        if method == "GET" and path == "/dnc":
            return json.dumps({"payload": [{"id": "L1"}]})
        if method == "POST" and path == "/dnc":
            return json.dumps({"message": "ok"})
        if path == "/dnc/L1":
            details = [{"value": "dana@acme.example", "type": "email"}]
            meta = {"currentPage": (params or {}).get("page", 1), "totalPages": 999}
            return json.dumps({"payload": {"dncListDetails": details, "meta": meta}})
        return "{}"

    monkeypatch.setattr("agent.mcp.saleshandy.server._call", fake_call)
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is False
    assert not [r for r in ledgers.written if r.get("event") == "dnc_added"]


def test_the_read_back_sends_pagesize_not_limit_and_filters_type_email(tmp_path, monkeypatch):
    """The two vendor-documented parameter names. `limit` is a parameter the API does not
    read; `pageSize` is. `type=email` matches what `_add_items` ever writes."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    read_params = []

    async def fake_call(method, path, *, params=None, json_body=None, api_key=None):
        if method == "GET" and path == "/dnc":
            return json.dumps({"payload": [{"id": "L1"}]})
        if method == "POST" and path == "/dnc":
            return json.dumps({"message": "ok"})
        if path == "/dnc/L1":
            read_params.append(params or {})
            details = [{"value": "dana@acme.example", "type": "email"}]
            meta = {"currentPage": 1, "totalPages": 1}
            return json.dumps({"payload": {"dncListDetails": details, "meta": meta}})
        return "{}"

    monkeypatch.setattr("agent.mcp.saleshandy.server._call", fake_call)
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is True
    assert read_params, "the read-back never called the verified endpoint"
    assert read_params[0].get("pageSize") == 100
    assert "limit" not in read_params[0]
    assert read_params[0].get("type") == "email"


def test_a_confirmed_add_writes_both_records(tmp_path, monkeypatch):
    """The positive control: a refusal-heavy function that refused everything would pass
    every test above."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    _stub_calls(monkeypatch)
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]})
    assert out.ok is True
    rows = [r for r in ledgers.written if r.get("event") == "dnc_added"]
    assert len(rows) == 1
    assert rows[0]["email"] == "dana@acme.example"
    assert rows[0]["confirmed_by_read_back"] is True
    csv_path = tmp_path / "example" / "prospects" / ".pool" / "suppression.csv"
    assert csv_path.is_file()
    assert "dana@acme.example" in csv_path.read_text()
    assert "dnc-optout" in csv_path.read_text()


# --- dry run ------------------------------------------------------------------------------


def test_a_dry_run_cannot_reach_the_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    seen = _stub_calls(monkeypatch)
    ledgers = _Ledgers([_optout("dana@acme.example")])
    out = _run(_Cfg(tmp_path), ledgers, {"addresses": ["dana@acme.example"]}, dry_run=True)
    assert out.status == "dry_run"
    assert not seen, "a dry run reached the provider"
    assert not ledgers.written


# --- add only, structurally ----------------------------------------------------------------


def test_the_module_has_no_removal_path():
    """Not a policy note: there is no function, no verb and no endpoint here that could
    remove an entry, so an automated un-suppression is not expressible."""
    source = Path(dnc_dispatch.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for forbidden in ("remove", "delete", "unsuppress", "clear"):
        assert not [n for n in names if forbidden in n.lower()], f"a {forbidden} path exists"
    # ...and no HTTP verb that would delete one.
    methods = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "_call"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert methods <= {"GET", "POST"}, f"a destructive HTTP verb is reachable: {methods}"


def test_the_write_happens_inside_the_dnc_context():
    """The window is what makes the connector-level denial meaningful — outside it, the
    same call is denied to everyone including this module's own code path."""
    source = Path(dnc_dispatch.__file__).read_text(encoding="utf-8")
    assert "with dnc_context():" in source
    tree = ast.parse(source)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "dispatch_approved_dnc_add"
    )
    with_blocks = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
    assert with_blocks, "the dispatch does not open a dnc_context window"
    inside = {
        getattr(c.func, "id", "")
        for w in with_blocks
        for c in ast.walk(w)
        if isinstance(c, ast.Call)
    }
    assert "_add_items" in inside, "the provider write is outside the dnc_context window"


# --- gaps found by the mutation sweep (2026-09-21) ---------------------------------------


def test_the_outcome_ok_flag_matches_the_status(tmp_path, monkeypatch):
    """`ok` is what `_dispatch_gate2` branches on to decide whether to fail the run, so it
    is not decoration. The sweep could flip three of these with every test still green,
    because the tests asserted `status` and never `ok`."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    _stub_calls(monkeypatch)

    # a draft naming nobody at all
    empty = _run(_Cfg(tmp_path), _Ledgers([]), {"addresses": []})
    assert (empty.status, empty.ok) == ("nothing_to_do", False)

    # a draft naming only addresses with no ledger row
    unevidenced = _run(_Cfg(tmp_path), _Ledgers([]), {"addresses": ["rival@competitor.example"]})
    assert (unevidenced.status, unevidenced.ok) == ("nothing_to_do", False)

    # a dry run reports ok=True: nothing failed, nothing was sent
    dry = _run(
        _Cfg(tmp_path),
        _Ledgers([_optout("dana@acme.example")]),
        {"addresses": ["dana@acme.example"]},
        dry_run=True,
    )
    assert (dry.status, dry.ok) == ("dry_run", True)


def test_a_malformed_dnc_lists_payload_refuses(tmp_path, monkeypatch):
    """§R5: the list response is provider output and its shape is not to be trusted. The
    sweep found the item filter could be loosened with nothing noticing."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    monkeypatch.delenv("SALESHANDY_DNC_LIST_ID", raising=False)
    seen = _stub_calls(
        monkeypatch,
        lists=json.dumps({"payload": {"items": ["not-a-dict", {"no": "id"}, 7]}}),
    )
    out = _run(
        _Cfg(tmp_path),
        _Ledgers([_optout("dana@acme.example")]),
        {"addresses": ["dana@acme.example"]},
    )
    assert out.ok is False
    assert "no DNC list" in out.detail
    assert not [p for _m, p, _b in seen if (_m, p) == ("POST", "/dnc")]


def test_an_unconfigured_key_during_list_resolution_says_so(tmp_path, monkeypatch):
    """`not_configured` and `dnc_add_failed` route differently in the backend
    (`dnc_refusal` fails the run on one and not the other), so the distinction is
    load-bearing — the sweep found it untested."""
    monkeypatch.setenv("GTM_DNC_ADD_ENABLED", "true")
    from agent.mcp.saleshandy import server

    _stub_calls(monkeypatch, lists=server.NOT_CONFIGURED)
    out = _run(
        _Cfg(tmp_path),
        _Ledgers([_optout("dana@acme.example")]),
        {"addresses": ["dana@acme.example"]},
    )
    assert out.status == "not_configured"
    assert out.status != "dnc_add_failed"


def test_every_outcome_status_has_its_own_operator_line(tmp_path):
    """The operator reads this line at the gate; it is the whole UI of a refusal. One
    string per status, all distinct — the sweep found two branches nothing asserted."""
    from agent.dnc_dispatch import DncDispatchOutcome

    lines = {}
    for status, ok in (
        ("disabled", False),
        ("not_configured", False),
        ("dry_run", True),
        ("nothing_to_do", False),
        ("added", True),
        ("dnc_add_failed", False),
    ):
        lines[status] = DncDispatchOutcome(ok=ok, status=status, detail="why").operator_line()

    assert len(set(lines.values())) == len(lines), f"two statuses read the same: {lines}"
    assert "switched off" in lines["disabled"]
    assert "API key" in lines["not_configured"]
    assert "Dry run" in lines["dry_run"]
    assert "open opt-out row" in lines["nothing_to_do"]
    assert "Added" in lines["added"]
    assert "failed" in lines["dnc_add_failed"]

"""SC9b (4): an opt-out from a different alias must not leave the ENROLLED address reachable.

Before this, the only address the sweep ever recorded, alerted on or added was the reply's own
sender. A prospect enrolled as ``dana@acme.example`` who replies "Stop" from
``d.example@acme.example`` had the alias suppressed and the enrolled address — the one every
remaining sequence step is addressed to — left off the list.

The enrolled address comes from the thread detail's own join key, in the LIVE shape pinned by
``tests/agent/test_saleshandy_live_shapes.py``: the prospect's reply carries ``fromProspectId``,
and the message we sent carries ``toProspectId`` plus ``to``.

Boundary under test (CLAUDE.md, "One deliberate gateless path"): the automatic add stays "for the
reply's own sender only". The enrolled address is recorded as an open opt-out row and routed to
the ``optout-suppress`` gate — never added automatically.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from pathlib import Path

from agent import dnc_dispatch, optout_sweep
from agent.optout_auto_add import enrolled_address
from gtm_core.optout_watch import OptOutMatch

ENROLLED = "dana@acme.example"
ALIAS = "d.example@acme.example"
OURS = "sender@ourcompany.example"
PID = 209175356


@dataclasses.dataclass
class _Cfg:
    content_root: Path
    profiles_root: Path = Path("/nonexistent")
    telegram_bot_token: str = "test-token"


def _ours(to=(ENROLLED,), to_pid=PID):
    return {
        "fromEmail": OURS,
        "fromProspectId": None,
        "content": "<p>original outreach</p>",
        "sentAt": "2026-09-20T09:00:00.000Z",
        "to": list(to) if not isinstance(to, str) else to,
        "toProspectId": to_pid,
    }


def _theirs(sender, body="Stop", pid=PID):
    return {
        "fromEmail": sender,
        "fromProspectId": pid,
        "content": body,
        "sentAt": "2026-09-24T14:00:00.000Z",
        "to": [OURS],
        "toProspectId": None,
    }


def _thread(*messages):
    return {"threadId": "t1", "subject": "Re: your note", "messages": list(messages)}


def _match(sender):
    return OptOutMatch(
        thread_id="t1",
        email=sender,
        subject="Re: your note",
        message_ts="2026-09-24T14:00:00.000Z",
        snippet="Stop",
        direction_known=True,
        clear=True,
    )


# --- the resolver ------------------------------------------------------------------------


def test_the_enrolled_address_is_joined_on_the_prospect_id():
    assert enrolled_address(_thread(_ours(), _theirs(ALIAS)), _match(ALIAS)) == ENROLLED


def test_the_join_is_case_insensitive():
    thread = _thread(_ours(to=("Dana@Acme.Example",)), _theirs(ALIAS))
    assert enrolled_address(thread, _match(ALIAS)) == ENROLLED


def test_a_bare_string_to_field_is_accepted():
    assert enrolled_address(_thread(_ours(to=ENROLLED), _theirs(ALIAS)), _match(ALIAS)) == ENROLLED


def test_no_outbound_message_for_the_prospect_is_unresolvable():
    assert enrolled_address(_thread(_theirs(ALIAS)), _match(ALIAS)) is None


def test_an_outbound_message_to_a_different_prospect_is_not_a_join():
    thread = _thread(_ours(to_pid=111), _theirs(ALIAS))
    assert enrolled_address(thread, _match(ALIAS)) is None


def test_two_enrolled_addresses_for_one_prospect_is_ambiguous_not_a_guess():
    thread = _thread(_ours(), _ours(to=("other@acme.example",)), _theirs(ALIAS))
    assert enrolled_address(thread, _match(ALIAS)) is None


def test_the_legacy_shape_without_prospect_ids_is_unresolvable():
    thread = _thread(
        {"from": OURS, "direction": "outbound", "body": "hi", "to": [ENROLLED]},
        {"from": ALIAS, "direction": "inbound", "body": "Stop"},
    )
    assert enrolled_address(thread, _match(ALIAS)) is None


# --- through the sweep -------------------------------------------------------------------


def _run(tmp_path, monkeypatch, messages, *, switch_on, outcome_status="added"):
    records: list = []

    class _FakeLedgers:
        profile = "example"

        def __init__(self, cfg, profile):
            pass

        def append_history(self, record):
            records.append(record)

        def iter_history(self):
            return iter(records)

    monkeypatch.setattr(optout_sweep, "Ledgers", _FakeLedgers)

    async def fake_get_inbox_threads(**kw):
        return json.dumps(
            {
                "message": "ok",
                "payload": {"items": [{"emailThreadId": "t1", "sentAt": "2026-09-24T14:00:00Z"}]},
            }
        )

    async def fake_get_thread(thread_id):
        return json.dumps({"message": "ok", "payload": list(messages)})

    added: list = []

    async def fake_dispatch(cfg, ledgers, *, draft, dry_run=False, approved_by="operator"):
        added.append((list(draft["addresses"]), approved_by))
        ok = outcome_status == "added"
        return dnc_dispatch.DncDispatchOutcome(ok=ok, status=outcome_status, detail="x")

    alerts: list = []

    async def fake_push(cfg, profiles_root, profile, match, **kw):
        alerts.append((match.email, kw.get("enrolled", "<not passed>")))

    async def no_dispatch(cfg, profile):
        return None

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)
    monkeypatch.setattr("agent.mcp.saleshandy.server.get_thread", fake_get_thread)
    monkeypatch.setattr("agent.gate_notify.push_optout_alert", fake_push)
    monkeypatch.setattr(dnc_dispatch, "enabled", lambda: switch_on)
    monkeypatch.setattr(dnc_dispatch, "dispatch_approved_dnc_add", fake_dispatch)
    monkeypatch.setattr(optout_sweep, "_dispatch_signals", no_dispatch)
    assert asyncio.run(optout_sweep.run("example", cfg=_Cfg(content_root=tmp_path))) == 0
    return records, added, alerts, _FakeLedgers(None, "example")


def _detected(records):
    return [r for r in records if r.get("event") == "optout_detected"]


def _gate_signals(records):
    return sorted(r["who"] for r in records if r.get("signal_type") == "optout_detected")


def test_a_same_address_reply_is_unchanged(tmp_path, monkeypatch):
    records, added, alerts, _ = _run(
        tmp_path, monkeypatch, [_ours(), _theirs(ENROLLED)], switch_on=True
    )
    assert added == [([ENROLLED], "auto:clear-optout")]
    assert [r["email"] for r in _detected(records)] == [ENROLLED]
    assert _gate_signals(records) == []
    # Same address: `enrolled` is not even passed, so every existing alert fake still fits.
    assert alerts == [(ENROLLED, "<not passed>")]


def test_an_alias_reply_adds_only_the_sender_and_gates_the_enrolled_address(tmp_path, monkeypatch):
    records, added, alerts, ledgers = _run(
        tmp_path, monkeypatch, [_ours(), _theirs(ALIAS)], switch_on=True
    )
    # The gateless path is untouched: the reply's own sender, and nobody else.
    assert added == [([ALIAS], "auto:clear-optout")]
    assert all(ENROLLED not in addresses for addresses, _ in added)
    # The enrolled address has an OPEN row, so an approved gate draft can reach it ...
    rows = {r["email"]: r for r in _detected(records)}
    assert set(rows) == {ALIAS, ENROLLED}
    assert ENROLLED in dnc_dispatch.open_candidates(ledgers)
    # ... and that row says plainly why it is there, so the human approves knowingly.
    row = rows[ENROLLED]
    assert row["replied_from"] == ALIAS
    assert row["enrolled_differs_from_sender"] is True
    assert row["clear"] is False
    assert ALIAS in row["action_required"] and ENROLLED in row["action_required"]
    # ... and it goes to the optout-suppress gate; the auto-added sender does not.
    assert _gate_signals(records) == [ENROLLED]
    signal = next(r for r in records if r.get("who") == ENROLLED)
    assert signal["meta"]["replied_from"] == ALIAS
    # The one Telegram alert names both addresses (deterministic wording, agent.gate_notify).
    assert alerts == [(ALIAS, ENROLLED)]


def test_an_alias_reply_with_the_switch_off_gates_both(tmp_path, monkeypatch):
    records, added, alerts, ledgers = _run(
        tmp_path, monkeypatch, [_ours(), _theirs(ALIAS)], switch_on=False
    )
    assert added == []
    assert _gate_signals(records) == sorted([ALIAS, ENROLLED])
    assert {ALIAS, ENROLLED} <= dnc_dispatch.open_candidates(ledgers)
    assert alerts == [(ALIAS, ENROLLED)]


def test_a_failed_sender_add_still_gates_both(tmp_path, monkeypatch):
    records, added, _, _ = _run(
        tmp_path,
        monkeypatch,
        [_ours(), _theirs(ALIAS)],
        switch_on=True,
        outcome_status="dnc_add_failed",
    )
    assert added == [([ALIAS], "auto:clear-optout")]
    assert _gate_signals(records) == sorted([ALIAS, ENROLLED])


def test_an_unclear_alias_reply_gates_both(tmp_path, monkeypatch):
    body = "Thanks. We are evaluating vendors next quarter so please take me off for now"
    records, added, _, _ = _run(
        tmp_path, monkeypatch, [_ours(), _theirs(ALIAS, body=body)], switch_on=True
    )
    assert added == []
    assert _gate_signals(records) == sorted([ALIAS, ENROLLED])


def test_an_unresolvable_enrolled_address_is_unchanged_and_logged(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="agent.optout_auto_add")
    records, added, alerts, _ = _run(tmp_path, monkeypatch, [_theirs(ALIAS)], switch_on=True)
    assert added == [([ALIAS], "auto:clear-optout")]
    assert [r["email"] for r in _detected(records)] == [ALIAS]
    assert _gate_signals(records) == []
    assert alerts == [(ALIAS, "<not passed>")]
    assert any("enrolled address" in m and "t1" in m for m in caplog.messages)


def test_the_gate_draft_is_told_to_state_the_enrolled_sender_difference():
    """The `review` node writes the approval text from the ledger rows; it must surface the
    two fields that say the enrolled address is not the one that replied."""
    from gtm_core.packs.loader import load_pack_graph

    repo = Path(__file__).resolve().parents[2]
    graph = load_pack_graph(repo / "packs" / "inbound" / "graphs" / "optout-suppress.toml")
    review = next(n for n in graph.nodes if n.id == "review")
    assert "`replied_from`" in review.prompt and "`action_required`" in review.prompt

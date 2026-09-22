"""Tests for the deterministic DNC mirror refresh + reconcile (agent.dnc_sync).

Every external call is monkeypatched — no Saleshandy HTTP, no Telegram push. What this
exercises is the wiring and, above all, the three refusals:

  - an EMPTY provider payload must keep the old cache and exit 1, because an unparsed
    response and a genuinely empty DNC list are indistinguishable and "0 suppressed, all
    clear" returns every opted-out person to the sendable pool;
  - a provider entry with no ledger row is a hand-added exclusion, NOT an opt-out, and
    must never reach ``suppression.csv``;
  - no key at all is "no connector wired", not "the sync broke" — exit 0, ledger row.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from agent import dnc_sync


@dataclasses.dataclass
class _Cfg:
    content_root: Path
    profiles_root: Path = Path("/nonexistent")
    telegram_bot_token: str = ""


def _fake_ledgers(monkeypatch, records: list):
    class _FakeLedgers:
        def __init__(self, cfg, profile):
            pass

        def append_history(self, record):
            records.append(record)

    monkeypatch.setattr(dnc_sync, "Ledgers", _FakeLedgers)


def _lists(*ids):
    return json.dumps({"message": "ok", "payload": {"items": [{"id": i} for i in ids]}})


def _items(*values):
    return json.dumps(
        {
            "message": "ok",
            "payload": {
                "dncListDetails": [
                    {"value": v, "type": "domain" if v.startswith("@") else "email"} for v in values
                ]
            },
        }
    )


_EMPTY_ITEMS = json.dumps({"message": "ok", "payload": {"dncListDetails": []}})


def _run(tmp_path, monkeypatch, records, *, lists_raw, items_by_page, profile="example"):
    """Drive one sync. ``items_by_page`` is a list of raw responses, consumed in order."""
    calls = {"lists": 0, "items": 0}
    pages = list(items_by_page)

    async def fake_list_dnc_lists(**kw):
        calls["lists"] += 1
        return lists_raw

    async def fake_get_dnc_items(**kw):
        calls["items"] += 1
        return pages.pop(0) if pages else _EMPTY_ITEMS

    monkeypatch.setattr("agent.mcp.saleshandy.server.list_dnc_lists", fake_list_dnc_lists)
    monkeypatch.setattr("agent.mcp.saleshandy.server.get_dnc_items", fake_get_dnc_items)
    rc = asyncio.run(dnc_sync.run(profile, cfg=_Cfg(content_root=tmp_path)))
    return rc, calls


def _cache_path(tmp_path, profile="example"):
    from gtm_core.prospects_consolidate.paths import dnc_cache_path

    return dnc_cache_path(profile, tmp_path)


def _write_ledger(tmp_path, rows, profile="example"):
    """rows: list of (email, reason)."""
    p = tmp_path / profile / "prospects" / ".pool" / "suppression.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["email,reason,source,date"]
    lines += [f"{e},{r},test,2026-09-21" for e, r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# --- the happy path -----------------------------------------------------------------


def test_a_successful_sync_writes_the_cache_and_reconciles(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers(monkeypatch, records)

    rc, _calls = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("dana@acme.example", "@bracken.example")],
    )
    assert rc == 0
    cached = json.loads(_cache_path(tmp_path).read_text())
    assert cached["emails"] == ["dana@acme.example"]
    assert cached["domains"] == ["bracken.example"]
    assert cached["fetched_at"].endswith("Z")
    reconciled = [r for r in records if r["event"] == "dnc_reconciled"]
    assert len(reconciled) == 1
    assert reconciled[0]["findings"] == []


def test_a_ledger_row_absent_from_the_provider_is_a_finding(tmp_path, monkeypatch):
    """The direction that matters: we claim this person is suppressed provider-side, and
    they are not — so a sequence outside this repo's ledger can still reach them."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("ghost@bracken.example", "dnc-optout")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("dana@acme.example")],
    )
    assert rc == 0
    findings = [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"]
    assert len(findings) == 1
    assert "ghost@bracken.example" in findings[0]


# --- the refusals --------------------------------------------------------------------


def test_an_empty_payload_refuses_and_keeps_the_old_cache(tmp_path, monkeypatch):
    """Test plan §4.7: an empty DNC payload must refuse, never report '0 suppressed, all
    clear'. The previous cache — which has real entries — must survive untouched."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    cache = _cache_path(tmp_path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    previous = json.dumps(
        {"emails": ["dana@acme.example"], "domains": [], "fetched_at": "2026-09-20T00:00:00Z"}
    )
    cache.write_text(previous, encoding="utf-8")

    rc, _ = _run(
        tmp_path, monkeypatch, records, lists_raw=_lists("L1"), items_by_page=[_EMPTY_ITEMS]
    )
    assert rc == 1
    assert cache.read_text() == previous, "an empty read overwrote a cache with real entries"
    assert [r for r in records if r["event"] == "dnc_sync_refused"]
    assert not [r for r in records if r["event"] == "dnc_reconciled"]


def test_a_provider_read_error_is_a_hard_failure(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers(monkeypatch, records)
    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw="[saleshandy-error] HTTP 500",
        items_by_page=[],
    )
    assert rc == 1
    assert not _cache_path(tmp_path).exists()


def test_a_missing_key_is_quiet_but_never_silent(tmp_path, monkeypatch):
    """No connector wired is not a broken sync: exit 0 so the unit does not ping daily
    forever, but leave a durable row so the gap is findable."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    from agent.mcp.saleshandy import server

    rc, _ = _run(tmp_path, monkeypatch, records, lists_raw=server.NOT_CONFIGURED, items_by_page=[])
    assert rc == 0
    skipped = [r for r in records if r["event"] == "dnc_sync_skipped"]
    assert len(skipped) == 1
    assert "SALESHANDY_API_KEY" in skipped[0]["action_required"]
    assert not _cache_path(tmp_path).exists()


def test_a_mid_read_key_loss_does_not_write_a_partial_mirror(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers(monkeypatch, records)
    from agent.mcp.saleshandy import server

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[server.NOT_CONFIGURED],
    )
    assert rc == 0
    assert not _cache_path(tmp_path).exists()


# --- the boundary: suppression.csv is never written ------------------------------------


def test_the_sync_never_writes_to_suppression_csv(tmp_path, monkeypatch):
    """A provider entry with no ledger row is a hand-added exclusion, not an opt-out.
    Appending it as `dnc-optout` would relabel it and inflate the opt-out rate the wave
    gate blocks on — so the file must be byte-identical after a sync that sees three."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    ledger = _write_ledger(tmp_path, [("dana@acme.example", "dnc-optout")])
    before = ledger.read_bytes()

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[
            _items(
                "dana@acme.example",
                "handadded1@bracken.example",
                "handadded2@bracken.example",
                "handadded3@renseco.example",
            )
        ],
    )
    assert rc == 0
    assert ledger.read_bytes() == before, "the sync wrote to suppression.csv"
    row = [r for r in records if r["event"] == "dnc_reconciled"][0]
    assert row["provider_only"] == 3  # reported as a count...
    assert row["findings"] == []  # ...and never as a finding


def test_the_module_contains_no_suppression_write_call():
    """Structural, not behavioural: nothing in this module may reach a ledger writer, so a
    future edit cannot quietly add one and pass the behavioural test above by accident."""
    import ast

    tree = ast.parse(Path(dnc_sync.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in ("append", "apply", "migrate"):
        assert forbidden not in called, f"dnc_sync calls suppression.{forbidden}"


# --- identity keys (test plan §4.3) -----------------------------------------------------


def test_a_domain_typed_entry_covers_an_address_at_that_domain(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("dana@bracken.example", "dnc-optout")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("@bracken.example")],
    )
    assert rc == 0
    assert [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"] == []


def test_a_second_address_for_the_same_person_is_still_a_finding(tmp_path, monkeypatch):
    """Identity is per-address here on purpose: the provider suppresses an ADDRESS, so a
    person's second address is genuinely still reachable and must be reported."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("dana.smith@acme.example", "dnc-optout")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("dana@acme.example")],
    )
    assert rc == 0
    findings = [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"]
    assert len(findings) == 1
    assert "dana.smith@acme.example" in findings[0]


def test_casing_does_not_produce_a_phantom_finding(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("Dana@Acme.Example", "dnc-optout")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("dana@acme.example")],
    )
    assert rc == 0
    assert [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"] == []


def test_a_non_dnc_reason_is_not_reconciled(tmp_path, monkeypatch):
    """Only `dnc-optout` rows make a claim about the provider. An out-of-market exclusion
    is a claim about our own files and must not be reported as a provider divergence."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("someone@bracken.example", "out-of-market")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("dana@acme.example")],
    )
    assert rc == 0
    assert [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"] == []


# --- paging --------------------------------------------------------------------------


def test_every_list_is_paged_to_exhaustion(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers(monkeypatch, records)

    rc, calls = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1", "L2"),
        items_by_page=[
            _items("a@acme.example"),
            _items("b@acme.example"),
            _EMPTY_ITEMS,
            _items("c@bracken.example"),
            _EMPTY_ITEMS,
        ],
    )
    assert rc == 0
    cached = json.loads(_cache_path(tmp_path).read_text())
    assert cached["emails"] == ["a@acme.example", "b@acme.example", "c@bracken.example"]
    assert calls["items"] >= 4  # both lists were paged, not just the first


# --- the alert aggregates -------------------------------------------------------------


def test_fifty_divergences_produce_exactly_one_message(tmp_path, monkeypatch):
    """Test plan §3.D: a per-divergence ping on a 400-row list is hundreds of messages,
    which is a cockpit the operator mutes."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [(f"ghost{i}@bracken.example", "dnc-optout") for i in range(50)])
    sent: list = []

    async def fake_push(cfg, profiles_root, profile, findings, *, provider_only=0):
        sent.append((findings, provider_only))

    monkeypatch.setattr("agent.gate_notify.push_dnc_divergence", fake_push)

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("dana@acme.example")],
    )
    assert rc == 0
    assert len(sent) == 1, f"expected one aggregated message, got {len(sent)}"
    assert len(sent[0][0]) == 50


def test_a_clean_reconcile_sends_nothing(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("dana@acme.example", "dnc-optout")])
    sent: list = []

    async def fake_push(*a, **kw):
        sent.append(a)

    monkeypatch.setattr("agent.gate_notify.push_dnc_divergence", fake_push)

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("dana@acme.example")],
    )
    assert rc == 0
    assert not sent


def test_a_push_failure_does_not_fail_the_sync(tmp_path, monkeypatch):
    """The durable ledger row is already written; a notification failure must not turn a
    completed reconcile into a systemd alert."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("ghost@bracken.example", "dnc-optout")])

    async def boom(*a, **kw):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr("agent.gate_notify.push_dnc_divergence", boom)

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("dana@acme.example")],
    )
    assert rc == 0
    assert [r for r in records if r["event"] == "dnc_reconciled"]


# --- CLI -------------------------------------------------------------------------------


def test_main_requires_a_profile_flag():
    with pytest.raises(SystemExit):
        dnc_sync.main([])


# --- test plan §4.3 — is the identity key as wide as the identity? ------------------------
#
# Each of these was a real prior incident shape. A suppression check that matches narrower
# than the identity it is protecting returns a person to the sendable pool.


def test_a_www_prefixed_domain_entry_still_covers_the_bare_domain(tmp_path, monkeypatch):
    """Providers return domains both ways. `www.bracken.example` and `bracken.example` are
    one domain, and treating them as two is how a domain-wide suppression half-applies."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("dana@bracken.example", "dnc-optout")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("@www.bracken.example")],
    )
    assert rc == 0
    findings = [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"]
    assert findings == [], f"a www. prefix split one domain into two: {findings}"


def test_a_leading_dot_domain_entry_also_covers_it(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("dana@bracken.example", "dnc-optout")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("@.bracken.example")],
    )
    assert rc == 0
    assert [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"] == []


def test_whitespace_around_a_provider_entry_does_not_split_the_identity(tmp_path, monkeypatch):
    """A padded value is the same address. Exported CSVs routinely carry one."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("dana@acme.example", "dnc-optout")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("  dana@acme.example  ")],
    )
    assert rc == 0
    assert [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"] == []


def test_a_subdomain_is_not_silently_covered_by_the_parent_domain(tmp_path, monkeypatch):
    """The other direction, and the one that must NOT widen: a DNC entry for
    `bracken.example` says nothing about `mail.bracken.example`. Claiming it did would
    report a person as suppressed when the provider has never heard of their address."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    _write_ledger(tmp_path, [("dana@mail.bracken.example", "dnc-optout")])

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("@bracken.example")],
    )
    assert rc == 0
    findings = [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"]
    assert len(findings) == 1, "a parent-domain entry was read as covering a subdomain"
    assert "dana@mail.bracken.example" in findings[0]


def test_a_ledger_row_with_no_company_domain_still_reconciles_on_its_address(tmp_path, monkeypatch):
    """§4.3: a row with no `company_domain` must fall back to the address key rather than
    matching everyone at a blank domain."""
    records: list = []
    _fake_ledgers(monkeypatch, records)
    path = tmp_path / "example" / "prospects" / ".pool" / "suppression.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "email,reason,source,date,name,company_domain\n"
        "dana@acme.example,dnc-optout,test,2026-09-21,,\n",
        encoding="utf-8",
    )

    rc, _ = _run(
        tmp_path,
        monkeypatch,
        records,
        lists_raw=_lists("L1"),
        items_by_page=[_items("someone-else@other.example")],
    )
    assert rc == 0
    findings = [r for r in records if r["event"] == "dnc_reconciled"][0]["findings"]
    assert len(findings) == 1
    assert "dana@acme.example" in findings[0]

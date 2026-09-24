"""A retire must reach the account it names, and say so when it does not (P6 item 4).

An operator decides `suppress` on a held row. `lanes.decisions.apply` writes the suppression
ledger and then retires the ACCOUNT in `latest.json`, so every future sweep drops every
contact at it. That second half used one key — `lanes.router.account_key`, which returns the
stamped id, else the company domain, else the exact company name, and nothing else — while
`set_status` looks an item up under every key the item carries. When the one key the row
picked was not one the ledger item happened to hold, the update matched nothing, and `apply`
discarded `set_status`'s report of exactly that. The retire looked like it had landed.

Fixtures are `gtm_core.fictionalize` output (§R9). The SHAPE is the real one: one entity
stored with a legal suffix on one side and without it on the other, or with a domain on one
side and none on the other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.lanes.decisions import ApplyPlan, DecisionEntry, apply
from gtm_core.prospects_state import latest_path, load_latest

PROFILE = "acme"


def _ledger(tmp_path: Path, monkeypatch, items: list[dict]) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    path = latest_path(PROFILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items}), encoding="utf-8")


def _entry(**kw) -> DecisionEntry:
    base = {
        "email": "jordan@summitline.example",
        "trigger": "strategic-account",
        "account_key": "",
        "decision": "suppress",
        "company": "Summitline Interactive",
        "company_domain": "",
        "account_id": "",
        "name": "Jordan Renner",
    }
    base.update(kw)
    if not base["account_key"]:
        base["account_key"] = f"c:{base['company'].strip().lower()}"
    return DecisionEntry(**base)


def _status_of(company: str) -> str:
    for item in load_latest(PROFILE).get("items", []):
        if item.get("company") == company:
            return item.get("status", "")
    return "<absent>"


def test_a_retire_reaches_an_account_recorded_with_a_legal_suffix(tmp_path, monkeypatch):
    """The case that cost three round-trips. The ledger holds the registered name, the row
    carries the trading name and no domain, and the two exact `c:` keys never meet."""
    _ledger(tmp_path, monkeypatch, [{"company": "Summitline Interactive Ltd", "status": "new"}])
    result = apply(
        ApplyPlan(suppress=[_entry(company="Summitline Interactive")]), PROFILE, "2026-09-23"
    )
    assert _status_of("Summitline Interactive Ltd") == "disqualified"
    assert result["retire_unmatched"] == []


def test_a_retire_reaches_an_account_whose_domain_was_blanked(tmp_path, monkeypatch):
    """The other evasion the widened join absorbs: a minimal re-discovery dropped the ledger
    item's domain, while the row still carries it, so the row's domain-first key misses."""
    _ledger(tmp_path, monkeypatch, [{"company": "Summitline Interactive", "status": "new"}])
    result = apply(
        ApplyPlan(suppress=[_entry(company_domain="summitline.example")]),
        PROFILE,
        "2026-09-23",
    )
    assert _status_of("Summitline Interactive") == "disqualified"
    assert result["retire_unmatched"] == []


def test_a_retire_reaches_an_account_recorded_in_a_different_case(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch, [{"company": "SUMMITLINE INTERACTIVE", "status": "new"}])
    apply(ApplyPlan(suppress=[_entry()]), PROFILE, "2026-09-23")
    assert _status_of("SUMMITLINE INTERACTIVE") == "disqualified"


def test_a_retire_that_reaches_nothing_is_reported_not_swallowed(tmp_path, monkeypatch):
    """The half that made every miss above invisible. `set_status` refuses to invent an
    account and names the key it could not place; `apply` used to throw that away, so a
    retire that reached nothing and a retire that worked printed the same thing."""
    _ledger(tmp_path, monkeypatch, [{"company": "Marlowe Systems", "status": "new"}])
    result = apply(ApplyPlan(suppress=[_entry()]), PROFILE, "2026-09-23")
    assert _status_of("Marlowe Systems") == "new", "it must not retire a bystander"
    assert result["retire_unmatched"] == ["c:summitline interactive"], (
        "an unmatched retire must be named, under the key the operator will recognise"
    )


def test_the_retire_does_not_widen_into_a_neighbouring_company(tmp_path, monkeypatch):
    """The expensive direction. The widened join is exact on a whole normalised value, so a
    company whose name merely CONTAINS a retired one is untouched."""
    _ledger(
        tmp_path,
        monkeypatch,
        [
            {"company": "Summitline Interactive", "status": "new"},
            {"company": "Summitline Interactive Partners", "status": "new"},
        ],
    )
    apply(ApplyPlan(suppress=[_entry()]), PROFILE, "2026-09-23")
    assert _status_of("Summitline Interactive") == "disqualified"
    assert _status_of("Summitline Interactive Partners") == "new"


def test_no_second_identity_key_was_introduced():
    """The PRD's rejected fix, pinned. Building a helper would have created exactly the
    duplicate identity key this item exists to remove — so the retire path must resolve
    through the shared `account_exclusion_keys` join and `prospects_state`'s own key."""
    import inspect

    from gtm_core.lanes import decisions

    src = inspect.getsource(decisions._retire_updates)
    assert "row_account_keys" in src
    assert "ledger_account_keys" in src
    assert "_identity_key(" in src
    # It must not re-derive a key by hand.
    assert 'f"c:' not in src and 'f"d:' not in src and 'f"a:' not in src


def test_account_key_itself_is_unchanged(tmp_path):
    """`router.account_key` is the identity the decisions ledger has ALREADY RECORDED against
    every prior decision. Re-deriving it would make those rows unfindable and silently
    re-apply decisions an operator already made, so it stays one key with its own
    precedence. One key is the right answer for "what is this decision filed under" and the
    wrong one for "which account does it retire"."""
    from gtm_core.lanes.router import account_key

    assert (
        account_key({"account_id": "A1", "company_domain": "d.example", "company": "X"}) == "a:a1"
    )
    assert account_key({"company_domain": "D.Example", "company": "X"}) == "d:d.example"
    assert account_key({"company": "Summitline Interactive"}) == "c:summitline interactive"


@pytest.mark.parametrize("missing", ["company", "company_domain", "account_id"])
def test_an_entry_missing_a_field_still_resolves_on_the_others(tmp_path, monkeypatch, missing):
    """Every key the row can offer is offered, so losing one field is not losing the join."""
    _ledger(
        tmp_path,
        monkeypatch,
        [{"company": "Summitline Interactive", "domain": "summitline.example", "status": "new"}],
    )
    kw = {"company": "Summitline Interactive", "company_domain": "summitline.example"}
    kw[missing] = ""
    if missing == "company":
        kw["account_key"] = "d:summitline.example"
    apply(ApplyPlan(suppress=[_entry(**kw)]), PROFILE, "2026-09-23")
    assert _status_of("Summitline Interactive") == "disqualified"

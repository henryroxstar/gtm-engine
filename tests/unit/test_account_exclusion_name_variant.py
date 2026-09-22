"""A retired account stays excluded when a later export spells its NAME differently.

Review finding B1 (2026-09-21). The ledger held a do-not-contact account under its registered
name — legal suffix and domain included. A later export named the same account without the
suffix and carried no domain column and no ``account_id``. Both the send-list build and the
enrollment gate joined on exact ``d:``/``i:``/``c:``/``a:`` keys only, so neither saw the match:
the contact was written to ``ready-to-load.csv`` and the gate raised no objection to it.

Every refusal below ships with the positive control that proves the row is otherwise loadable
(§R18), and with the negative controls that prove the wider join is still exact.
Fictional fixtures only (§R9); everything lives under ``tmp_path``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from gtm_core import enrollment_gate
from gtm_core import prospects_consolidate as pc
from gtm_core.account_exclusion_keys import (
    LEGAL_FORM_TOKENS,
    email_domain_key,
    ledger_account_keys,
    legal_form_key,
    row_account_keys,
)
from gtm_core.prospects_consolidate.accounts import _account_keys_of

PROFILE = "qa-tenant"
EXPORT_HEADER = ["First Name", "Last Name", "Email", "Company", "Company Domain Name",
                 "Email Status"]  # fmt: skip

#: The account as the ledger of record holds it.
LEDGER_ACCOUNT = {
    "id": "contoso-freight-inc",
    "company": "Contoso Freight, Inc.",
    "domain": "contosofreight.example",
}
#: The reviewer's repro row: cleaned name, NO domain, NO account_id, address at the account.
INES = ["Ines", "Vale", "ines.vale@contosofreight.example", "Contoso Freight", "", "verified"]


@pytest.fixture(autouse=True)
def _no_real_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))


def _export(root: Path, rows: list[list[str]]) -> None:
    path = root / PROFILE / "prospects" / "prospects-20260921-hubspot.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(EXPORT_HEADER)
        writer.writerows(rows)


def _ledger(root: Path, items: list[dict]) -> None:
    path = root / PROFILE / "prospects" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kind": "prospects", "profile": PROFILE, "items": items}), encoding="utf-8"
    )


def _ready_rows(root: Path) -> list[dict]:
    with pc.ready_to_load_path(PROFILE, root).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _gate_row(export_row: list[str]) -> dict:
    """The row as an enrollment list carries it (ready-to-load.csv's own column names)."""
    return {
        "email": export_row[2],
        "company": export_row[3],
        "company_domain": export_row[4],
        "account_id": "",
    }


# --------------------------------------------------------------------------- the repro, end to end


def test_positive_control_the_variant_row_is_loadable_while_the_account_is_live(
    tmp_path: Path,
) -> None:
    _export(tmp_path, [INES])
    _ledger(tmp_path, [{**LEDGER_ACCOUNT, "status": "new"}])

    res = pc.consolidate(PROFILE, content_root=tmp_path)

    assert res["disqualified_excluded"] == 0
    rows = _ready_rows(tmp_path)
    assert [r["email"] for r in rows] == [INES[2]]
    assert enrollment_gate.check_account_status(rows, PROFILE, tmp_path) is None


@pytest.mark.parametrize("status", ["do-not-contact", "disqualified", "closed-lost"])
def test_a_name_variant_of_a_retired_account_never_reaches_ready_to_load(
    tmp_path: Path, status: str
) -> None:
    _export(tmp_path, [INES])
    _ledger(tmp_path, [{**LEDGER_ACCOUNT, "status": status}])

    res = pc.consolidate(PROFILE, content_root=tmp_path)

    assert res["disqualified_excluded"] == 1
    assert _ready_rows(tmp_path) == []


@pytest.mark.parametrize("status", ["do-not-contact", "disqualified", "closed-lost", "replied"])
def test_the_gate_refuses_a_name_variant_of_a_blocked_account(tmp_path: Path, status: str) -> None:
    """``replied`` is the gate's own (engaged) class — consolidate does not exclude it, the gate
    does, and the name variant must not be the way round it."""
    _ledger(tmp_path, [{**LEDGER_ACCOUNT, "status": status}])

    refusal = enrollment_gate.check_account_status([_gate_row(INES)], PROFILE, tmp_path)

    assert refusal and f"{status}: 1" in refusal


# --------------------------------------------------------------------------- each key on its own


def test_the_email_domain_alone_ties_a_row_to_the_retired_account(tmp_path: Path) -> None:
    """A different company string entirely; only the contact's own address names the account."""
    row = ["Omar", "Reed", "omar.reed@contosofreight.example", "CF Logistics Desk", "", "verified"]
    _export(tmp_path, [row])
    _ledger(tmp_path, [{**LEDGER_ACCOUNT, "status": "do-not-contact"}])

    assert pc.consolidate(PROFILE, content_root=tmp_path)["disqualified_excluded"] == 1
    assert _ready_rows(tmp_path) == []
    assert enrollment_gate.check_account_status([_gate_row(row)], PROFILE, tmp_path)


def test_the_legal_form_key_alone_ties_a_row_to_the_retired_account(tmp_path: Path) -> None:
    """The address is at an unrelated domain and the ledger item has NO domain; only the name,
    minus its legal form, still joins them."""
    row = ["Ines", "Vale", "ines.vale@cf-mailhost.example", "Contoso Freight", "", "verified"]
    _export(tmp_path, [row])
    _ledger(
        tmp_path,
        [{"id": "cf-0001", "company": "CONTOSO FREIGHT PTE. LTD.", "status": "do-not-contact"}],
    )

    assert pc.consolidate(PROFILE, content_root=tmp_path)["disqualified_excluded"] == 1
    assert _ready_rows(tmp_path) == []
    assert enrollment_gate.check_account_status([_gate_row(row)], PROFILE, tmp_path)


# --------------------------------------------------------------------------- still exact


NEIGHBOURS = [
    ["Avery", "Quill", "avery.quill@contosofreightlines.example", "Contoso Freight Lines",
     "contosofreightlines.example", "verified"],
    ["Jules", "Marsh", "jules.marsh@contoso.example", "Contoso", "contoso.example", "verified"],
    ["Rowan", "Pike", "rowan.pike@zephyrinegroup.example", "Zephyrine Group",
     "zephyrinegroup.example", "verified"],
    ["Noor", "Hale", "noor.hale@cfpartners.example", "Contoso Freight Partners", "", "verified"],
]  # fmt: skip


def test_neighbouring_companies_are_not_excluded_by_a_retirement(tmp_path: Path) -> None:
    """Substring neighbours, and the ``group``/``holdings``/``co`` words that are NOT legal forms:
    a retired "Zephyrine Holdings" says nothing about "Zephyrine Group"."""
    _export(tmp_path, NEIGHBOURS)
    _ledger(
        tmp_path,
        [
            {**LEDGER_ACCOUNT, "status": "do-not-contact"},
            {"id": "zephyrine-holdings", "company": "Zephyrine Holdings",
             "domain": "zephyrineholdings.example", "status": "do-not-contact"},
        ],
    )  # fmt: skip

    res = pc.consolidate(PROFILE, content_root=tmp_path)

    assert res["disqualified_excluded"] == 0
    assert sorted(r["email"] for r in _ready_rows(tmp_path)) == sorted(r[2] for r in NEIGHBOURS)
    # "Co" is not a legal form to THIS join. (It is checked at the gate only: ingestion's
    # `merge_hygiene.clean_company` repairs a trailing "Co" off the pooled row's company before
    # any key is derived, which is a merge-field decision made upstream of the exclusion.)
    co = {"email": "noor.hale@contosofreightco.example", "company": "Contoso Freight Co"}
    gate_rows = [*(_gate_row(r) for r in NEIGHBOURS), co]
    assert enrollment_gate.check_account_status(gate_rows, PROFILE, tmp_path) is None


def test_a_free_mail_address_is_never_a_domain_key() -> None:
    # `chris@gmail.com` is the repo's one pinned free-mail RULE-INPUT fixture (pii_allowlist.txt).
    assert email_domain_key("chris@gmail.com") == ""
    assert email_domain_key("Ines.Vale@ContosoFreight.example ") == "d:contosofreight.example"
    assert email_domain_key("not-an-address") == ""
    assert email_domain_key("") == ""
    keys = row_account_keys({"email": "chris@gmail.com", "company": "Tailspin Health"})
    assert not [k for k in keys if k.startswith("d:")]


def test_a_retired_free_mail_domain_in_the_ledger_excludes_nobody_by_address(
    tmp_path: Path,
) -> None:
    """Junk a provider can put in a ledger ``domain``; it must not retire every gmail contact."""
    _ledger(
        tmp_path, [{"company": "Solo Consultant", "domain": "gmail.com", "status": "disqualified"}]
    )
    row = {"email": "chris@gmail.com", "company": "Tailspin Health", "company_domain": ""}
    assert enrollment_gate.check_account_status([row], PROFILE, tmp_path) is None


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Contoso Freight, Inc.", "contoso freight"),
        ("Contoso Freight", "contoso freight"),
        ("CONTOSO  FREIGHT  Pte. Ltd.", "contoso freight"),
        ("Tailspin Health S.A.", "tailspin health"),
        ("Tailspin Health B.V.", "tailspin health"),
        ("Northwind Robotics GmbH", "northwind robotics"),
        ("Zephyrine Holdings", "zephyrine holdings"),
        ("Zephyrine Group", "zephyrine group"),
        ("Contoso Freight Co", "contoso freight co"),
        ("Contoso Freight Company", "contoso freight company"),
        ("Limited", "limited"),  # never stripped to empty
        ("", ""),
    ],
)
def test_only_trailing_legal_form_tokens_are_stripped(name: str, key: str) -> None:
    assert legal_form_key(name) == key


def test_the_legal_form_vocabulary_is_a_closed_list() -> None:
    """Pinned: every word added makes two differently-named accounts exclude each other."""
    assert LEGAL_FORM_TOKENS == {
        "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "plc", "pte",
        "gmbh", "ag", "sa", "bv", "pty",
    }  # fmt: skip
    assert not LEGAL_FORM_TOKENS & {"group", "holdings", "company", "co"}


# --------------------------------------------------------------------------- one join, two callers


ROW_TABLE = [
    {"email": INES[2], "company": "Contoso Freight", "company_domain": "", "account_id": ""},
    {"email": "chris@gmail.com", "company": "Tailspin Health S.A.", "company_domain": "tailspin.example"},
    {"email": "", "company": "", "company_domain": "", "account_id": "acct-7f3a"},
    {"email": "x@northwind.example", "company": "Northwind  Robotics GmbH", "account_id": "ACCT-1"},
    {"contact_email": "y@zephyrine.example", "company": "Zephyrine Group", "domain": "zephyrine.example"},
]  # fmt: skip


@pytest.mark.parametrize("row", ROW_TABLE, ids=[str(i) for i in range(len(ROW_TABLE))])
def test_consolidate_and_the_gate_derive_the_same_keys_for_a_row(row: dict) -> None:
    """Two hand-rolled key lists is how the two checks drifted apart in the first place."""
    assert _account_keys_of(row) == enrollment_gate._row_identity_keys(row) == row_account_keys(row)


def test_a_ledger_item_offers_its_identity_keys_plus_the_legal_form_key() -> None:
    assert ledger_account_keys(LEDGER_ACCOUNT) == [
        "d:contosofreight.example",
        "i:contoso-freight-inc",
        "c:contoso freight, inc.",
        "n:contoso freight",
    ]


# ------------------------------------------------- the same variant, dropped rather than retired


def test_a_name_variant_of_a_dropped_account_never_reaches_ready_to_load(tmp_path: Path) -> None:
    """A `verdict: drop` account is closed to sending on its own axis. Reproduced 2026-09-21:
    the variant row carried a BLANK verdict (no exact key tied it to the account), tier B routed
    it to `generic` — which admits a blank verdict — and the gate printed PASS."""
    _export(tmp_path, [INES])
    _ledger(
        tmp_path,
        [
            {
                **LEDGER_ACCOUNT,
                "status": "new",
                "verdict": "drop",
                "verdict_reason": "direct competitor",
            }
        ],  # fmt: skip
    )

    res = pc.consolidate(PROFILE, content_root=tmp_path)

    assert res["disqualified_excluded"] == 1
    assert _ready_rows(tmp_path) == []


def test_the_gate_refuses_a_name_variant_of_a_dropped_account(tmp_path: Path) -> None:
    _ledger(
        tmp_path,
        [
            {
                **LEDGER_ACCOUNT,
                "status": "new",
                "verdict": "drop",
                "verdict_reason": "direct competitor",
            }
        ],  # fmt: skip
    )
    refusal = enrollment_gate.check_account_status([_gate_row(INES)], PROFILE, tmp_path)
    assert refusal and "verdict drop" in refusal


def test_a_live_accounts_variant_is_still_loadable_when_only_a_ROW_was_dropped(
    tmp_path: Path,
) -> None:
    """Negative control: the drop must come from the LEDGER account, not from any row."""
    _export(tmp_path, [INES])
    _ledger(tmp_path, [{**LEDGER_ACCOUNT, "status": "new", "verdict": "send"}])

    pc.consolidate(PROFILE, content_root=tmp_path)

    assert [r["email"] for r in _ready_rows(tmp_path)] == [INES[2]]

"""The accounts block must agree with the contacts table it is printed above (PSK-029).

Both are built from one routed state; the accounts block joins it to the ledger. These tests
pin the join, the buckets, the cross-check that can actually fire, and what the CLI prints.
Fictional fixtures only (§R9).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gtm_core import prospect_status as ps
from gtm_core import prospect_status_cli as cli
from gtm_core import prospect_status_receipt as receipt_mod
from gtm_core.prospect_status_receipt import compute_attrition_receipt, cross_check

PROFILE = "qa-sandbox"


def _account(company: str, domain: str, email: str = "", **kw) -> dict:
    return {"company": company, "domain": domain, "contact_email": email, "status": "new", **kw}


def _contact(email: str, status: str, **kw) -> dict:
    return {"email": email, "status": status, **kw}


NORTHWIND = _account(
    "Northwind Robotics", "northwindrobotics.example", "rowan.pike@northwindrobotics.example"
)
CONTOSO = _account("Contoso Freight", "contosofreight.example", "ira.bloom@contosofreight.example")
LITWARE = _account("Litware Pay", "litwarepay.example", "oma.reyes@litwarepay.example")
LUCERNE = _account("Lucerne Media", "lucernemedia.example", "kai.moss@lucernemedia.example")


def test_pseudo_values_match_the_status_module() -> None:
    """The receipt module may not import `prospect_status` (importer allowlist), so it carries
    its own copy — this is the tripwire against the two drifting."""
    assert receipt_mod._PSEUDO_VALUES == ps._PSEUDO_VALUES


# ------------------------------------------------------------------ buckets from routed state


def test_ready_and_held_come_from_routed_contacts_and_new_alone_is_never_held() -> None:
    routed = [
        _contact("rowan.pike@northwindrobotics.example", "ready_to_send"),
        _contact("ira.bloom@contosofreight.example", "waiting_on_you"),
        _contact("oma.reyes@litwarepay.example", "being_fixed"),
    ]
    r = compute_attrition_receipt([NORTHWIND, CONTOSO, LITWARE, LUCERNE], routed)
    assert (r.ready, r.held, r.not_routed) == (1, 2, 1)  # Lucerne: status new, nothing routed
    assert r.total_intake == 4 and r.unmatched_contacts == 0
    assert cross_check(r, {"ready_to_send": 1, "waiting_on_you": 1, "being_fixed": 1}, 3) == []


def test_one_ready_contact_makes_the_account_ready_even_beside_a_held_one() -> None:
    routed = [
        _contact("rowan.pike@northwindrobotics.example", "waiting_on_you"),
        _contact(
            "second.person@northwindrobotics.example",
            "in_sending_tool",
            company_domain="northwindrobotics.example",
        ),
    ]
    r = compute_attrition_receipt([NORTHWIND], routed)
    assert (r.ready, r.held, r.routed_accounts) == (1, 0, 1)


def test_an_account_whose_only_contacts_are_closed_is_not_a_fit() -> None:
    routed = [_contact("ira.bloom@contosofreight.example", "not_emailing")]
    r = compute_attrition_receipt([CONTOSO], routed)
    assert (r.failed_fit, r.held, r.ready, r.routed_closed) == (1, 0, 0, 1)


def test_with_routed_state_a_ledger_rows_own_wording_never_says_held_or_ready() -> None:
    worded = [
        {**NORTHWIND, "stage": "held", "lane": "hold"},
        {**CONTOSO, "stage": "ready", "status": "contact-resolved"},
    ]
    r = compute_attrition_receipt(worded, routed=[])
    assert (r.held, r.ready, r.not_routed) == (0, 0, 2)
    # …while a caller with NO routed state still gets the row's own wording (legacy callers).
    legacy = compute_attrition_receipt(worded)
    assert (legacy.held, legacy.ready) == (1, 1) and legacy.from_routed_state is False


def test_status_new_without_routed_state_is_not_held_either() -> None:
    r = compute_attrition_receipt([NORTHWIND, CONTOSO])
    assert (r.held, r.not_routed) == (0, 2)


# ------------------------------------------------------------------ the join


def test_a_contact_joins_by_address_then_domain_then_account_id() -> None:
    accounts = [NORTHWIND, {**CONTOSO, "account_id": "acct-9"}]
    routed = [
        _contact("rowan.pike@northwindrobotics.example", "ready_to_send"),  # by address
        _contact(
            "new.hire@contosofreight.example",
            "ready_to_send",
            company_domain="CONTOSOFREIGHT.example",
        ),  # by domain, case-folded
    ]
    assert compute_attrition_receipt(accounts, routed).ready == 2
    by_id = [_contact("someone@elsewhere.example", "waiting_on_you", account_id="ACCT-9")]
    r = compute_attrition_receipt(accounts, by_id)
    assert (r.held, r.unmatched_contacts) == (1, 0)


def test_a_company_name_never_joins_across_different_domains() -> None:
    twins = [
        _account("Northwind Robotics", "northwindrobotics.example"),
        _account("Northwind Robotics", "northwindrobotics-apac.example"),
    ]
    other_domain = _contact(
        "x@northwindrobotics-eu.example",
        "ready_to_send",
        company="Northwind Robotics",
        company_domain="northwindrobotics-eu.example",
    )
    r = compute_attrition_receipt(twins, [other_domain])
    assert (r.ready, r.unmatched_contacts) == (0, 1)
    # a bare name that fits two accounts is ambiguous — also not guessed
    bare = _contact("y@unknown.example", "ready_to_send", company="Northwind Robotics")
    assert compute_attrition_receipt(twins, [bare]).unmatched_contacts == 1
    # …and the unambiguous name still joins (positive control)
    solo = compute_attrition_receipt(
        [CONTOSO], [_contact("z@x.example", "ready_to_send", company="contoso freight")]
    )
    assert (solo.ready, solo.unmatched_contacts) == (1, 0)


# ------------------------------------------------------------------ the cross-check can fire


def test_cross_check_fires_for_a_routed_contact_whose_account_is_not_in_the_ledger() -> None:
    routed = [
        _contact("rowan.pike@northwindrobotics.example", "ready_to_send"),
        _contact("stranger@fabrikamlabs.example", "waiting_on_you", company="Fabrikam Labs"),
    ]
    r = compute_attrition_receipt([NORTHWIND], routed)
    problems = cross_check(r, {"ready_to_send": 1, "waiting_on_you": 1}, 2)
    assert problems == [
        "Check: 1 routed contact matches no ledger account — the account lines above leave it "
        "out. It can still be sent; only the account count misses it."
    ]


def test_cross_check_fires_when_the_contact_table_does_not_add_up() -> None:
    r = compute_attrition_receipt([NORTHWIND], [])
    (problem,) = cross_check(r, {"ready_to_send": 2}, 3)
    assert "adds up to 2" in problem and "3 contact(s) were routed" in problem


def test_cross_check_fires_from_data_when_the_ledger_excludes_a_listed_contact() -> None:
    """Replaces an "accounts placed" check that could only be made to fire by editing the
    receipt by hand — both of its sides were counted in one loop (§R18). This one fires from
    the inputs alone; `test_prospect_status_excluded_accounts.py` covers it in depth."""
    retired = {**NORTHWIND, "status": "do-not-contact"}
    listed = [_contact("rowan.pike@northwindrobotics.example", "ready_to_send")]
    (problem,) = cross_check(compute_attrition_receipt([retired], listed), {"ready_to_send": 1}, 1)
    assert "1 contact is on the list for an account marked not a fit / excluded" in problem
    assert (
        cross_check(compute_attrition_receipt([NORTHWIND], listed), {"ready_to_send": 1}, 1) == []
    )


def test_the_conservation_assert_covers_the_new_bucket() -> None:
    r = compute_attrition_receipt([NORTHWIND, CONTOSO], [])
    r.not_routed -= 1
    with pytest.raises(ValueError, match="conservation violated"):
        receipt_mod.verify_funnel_conservation(r)


# ------------------------------------------------------------------ what the CLI prints


def _seed(tmp_path: Path, monkeypatch, records: list[dict], items: list[dict]) -> Path:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    evals = tmp_path / PROFILE / "prospects" / "evals"
    evals.mkdir(parents=True)
    (evals / "lanes-state.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    (tmp_path / PROFILE / "prospects" / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": items}), encoding="utf-8"
    )
    return evals


def _record(account: dict, lane: str, reason: str) -> dict:
    return {
        "email": account["contact_email"],
        "lane": lane,
        "reason": reason,
        "company": account["company"],
        "company_domain": account["domain"],
        "account_id": "",
        "stamp": "2026-09-21",
    }


@pytest.mark.parametrize("held", [0, 1, 3])
def test_the_banner_number_is_the_waiting_on_you_number(
    tmp_path, monkeypatch, capsys, held
) -> None:
    accounts = [NORTHWIND, CONTOSO, LITWARE, LUCERNE]
    records = [
        _record(a, "hold", "tier-a-generic") if n < held else _record(a, "generic", "stale-clause")
        for n, a in enumerate(accounts)
    ]
    _seed(tmp_path, monkeypatch, records, accounts)
    assert cli.main(["--profile", PROFILE]) == 0
    out = capsys.readouterr().out
    waiting = int(re.search(r"^Waiting on you\s+(\d+)", out, re.M).group(1))
    # PS15: the lede's "Yours" line IS the Waiting-on-you count (the retired banner's rule).
    yours = re.search(r"^Yours \((\d+)\): decide on", out, re.M)
    assert waiting == held
    assert (int(yours.group(1)) if yours else 0) == held
    if not held:
        assert "Yours: nothing is waiting on you." in out
    assert "ACTION REQUIRED" not in out
    assert int(re.search(r"^  Held\s+(\d+)", out, re.M).group(1)) == held
    assert int(re.search(r"^  Sorted\s+(\d+)", out, re.M).group(1)) == 4 - held
    assert "Check:" not in out


def test_the_yours_line_names_a_review_sheet_that_exists_or_says_when_it_appears(
    tmp_path, monkeypatch, capsys
) -> None:
    evals = _seed(
        tmp_path, monkeypatch, [_record(NORTHWIND, "hold", "tier-a-generic")], [NORTHWIND]
    )
    assert cli.main(["--profile", PROFILE]) == 0
    first = capsys.readouterr().out
    assert "the review sheet is built when the list is sorted" in first

    (evals / "hold-2026-09-20.html").write_text("<html></html>", encoding="utf-8")
    (evals / "hold-2026-09-21.html").write_text("<html></html>", encoding="utf-8")
    (evals / "hold-2026-09-22.csv").write_text("email\n", encoding="utf-8")  # not a sheet
    assert cli.main(["--profile", PROFILE]) == 0
    second = capsys.readouterr().out
    assert f"review sheet: {PROFILE}/prospects/evals/hold-2026-09-21.html" in second
    assert str(tmp_path) not in second  # relative to the content root, not an absolute path


def test_contacts_waiting_on_address_verification_get_their_own_line(
    tmp_path, monkeypatch, capsys
) -> None:
    items = [
        {**NORTHWIND, "email_status": "verified"},
        {**CONTOSO, "email_status": "verifying"},
        {**LITWARE, "email_status": "pending"},
        {**LUCERNE, "email_status": "verifying", "status": "do-not-contact"},  # retired
        {"company": "Fabrikam Labs", "contact_name": "Sol Reed", "email_status": "verifying"},
    ]
    _seed(tmp_path, monkeypatch, [_record(NORTHWIND, "generic", "stale-clause")], items)
    assert cli.main(["--profile", PROFILE]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^Checking the address\s+2\b", out, re.M)
    assert re.search(
        r"^Still finding the right person\s+1\b", out, re.M
    )  # no address at all is the OTHER line


def test_an_unjoinable_contact_is_reported_not_dropped(tmp_path, monkeypatch, capsys) -> None:
    stranger = _account("Fabrikam Labs", "fabrikamlabs.example", "sol.reed@fabrikamlabs.example")
    records = [
        _record(NORTHWIND, "generic", "stale-clause"),
        _record(stranger, "generic", "stale-clause"),
    ]
    _seed(tmp_path, monkeypatch, records, [NORTHWIND])
    assert cli.main(["--profile", PROFILE]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^Sorted — not yet checked\s+2\b", out, re.M)
    assert re.search(r"^  Sorted\s+1\b", out, re.M)
    assert "Check: 1 routed contact matches no ledger account" in out


def test_an_unreadable_ledger_is_one_clear_line_not_a_traceback(
    tmp_path, monkeypatch, capsys
) -> None:
    _seed(tmp_path, monkeypatch, [_record(NORTHWIND, "generic", "stale-clause")], [NORTHWIND])
    (tmp_path / PROFILE / "prospects" / "latest.json").write_text('{"items": [', encoding="utf-8")
    assert cli.main(["--profile", PROFILE]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("The account ledger could not be read")
    assert len(captured.err.strip().splitlines()) == 1


def test_operator_labels_say_what_they_mean() -> None:
    labels = receipt_mod.BUCKET_LABELS
    assert labels["failed_fit"] == "Not a fit / excluded"
    assert labels["failed_intent"] == "Being researched"
    assert labels["failed_enrichment"] == "Finding a contact"
    assert labels["not_routed"] == "Not yet sorted"
    block = receipt_mod.format_attrition_receipt(compute_attrition_receipt([NORTHWIND], []))
    for old in ("Failed Fit", "Failed Intent", "Enrichment Miss", "Attrition"):
        assert old not in block


def test_everything_below_the_banner_is_in_the_operators_own_words(
    tmp_path, monkeypatch, capsys
) -> None:
    """The block is pasted to a person. The repo's vocabulary lint scans `_format_report`; this
    runs the same scanner over the WHOLE block — accounts lines, an Unrecognised row and a
    cross-check line included. Since PS15 nothing is exempt: the ACTION REQUIRED banner that
    named a command is retired, and the lede above the tables is scanned with the rest."""
    import sys

    lint_dir = Path(__file__).resolve().parents[1] / "lint"
    if str(lint_dir) not in sys.path:
        sys.path.insert(0, str(lint_dir))
    import operator_vocabulary as ov

    stranger = _account("Fabrikam Labs", "fabrikamlabs.example", "sol.reed@fabrikamlabs.example")
    records = [
        _record(NORTHWIND, "hold", "tier-a-generic"),
        _record(CONTOSO, "hold", "a-trigger-from-another-build"),
        _record(stranger, "generic", "stale-clause"),
    ]
    _seed(tmp_path, monkeypatch, records, [NORTHWIND, CONTOSO, LITWARE])
    assert cli.main(["--profile", PROFILE]) == 0
    out = capsys.readouterr().out
    assert "Unrecognised" in out and "Check:" in out and "Yours (" in out
    assert "ACTION REQUIRED" not in out
    assert [h for h in ov.findings(text=out) if h[0] == "<text>"] == []


def _pre_ps15_bucket(statuses: set[str]) -> tuple[str, str]:
    """The three set tests `_routed_bucket` used before PS15, kept verbatim as the oracle."""
    if statuses & receipt_mod.READY_CONTACT:
        return "ready", "ready"
    if statuses & receipt_mod.HELD_CONTACT:
        return "held", "held"
    if statuses <= receipt_mod.CLOSED_CONTACT:
        return "failed_fit", "closed"
    return "not_routed", "unrecognised"


def test_the_furthest_stage_rule_matches_the_set_tests_it_replaced_on_every_combination():
    """Exhaustive, not sampled: every subset of every status a routed contact can carry,
    plus an unrecognised one and the ledger-only one — 2**7 cases."""
    from itertools import chain, combinations

    universe = [
        "not_emailing",
        "being_fixed",
        "waiting_on_you",
        "ready_to_send",
        "in_sending_tool",
        "unrecognised",
        "needs_address",
    ]
    subsets = chain.from_iterable(combinations(universe, k) for k in range(len(universe) + 1))
    for combo in subsets:
        got = receipt_mod._routed_bucket(set(combo))
        assert got == _pre_ps15_bucket(set(combo)), combo


def test_the_account_words_say_what_is_happening_not_what_failed():
    labels = receipt_mod.BUCKET_LABELS
    assert labels["ready"] == "Sorted"
    assert "checks decide" in receipt_mod.BUCKET_NOTES["ready"]
    assert "ready to send" not in receipt_mod.BUCKET_NOTES["ready"]

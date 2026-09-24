"""An excluded account is never "Ready" — and a contact still on the list for one is SAID.

Review finding H3 (2026-09-21): once any routed contact reached an account, the accounts
block ignored the ledger row entirely, so a `do-not-contact` company with one routed person
printed under "Ready". A later duplicate ledger row at `status: new` hid the exclusion too.
Fictional fixtures only (§R9).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from gtm_core import prospect_status_cli as cli
from gtm_core.prospect_status_receipt import (
    compute_attrition_receipt,
    cross_check,
    dedupe_accounts,
)

PROFILE = "qa-sandbox"
EMAIL = "rowan.pike@contosofreight.example"


def _account(**kw) -> dict:
    return {
        "company": "Contoso Freight",
        "domain": "contosofreight.example",
        "contact_email": EMAIL,
        "status": "new",
        **kw,
    }


def _contact(status: str, email: str = EMAIL) -> dict:
    return {"email": email, "company_domain": "contosofreight.example", "status": status}


EXCLUDED_ROWS = [
    pytest.param({"status": "do-not-contact"}, id="do-not-contact"),
    pytest.param({"status": "disqualified"}, id="disqualified"),
    pytest.param({"status": "closed-lost"}, id="closed-lost"),
    pytest.param({"verdict": "drop"}, id="verdict-drop"),
    pytest.param({"category_relation": "competitor"}, id="competitor"),
    pytest.param({"category_relation": "regulator"}, id="regulator"),
]


@pytest.mark.parametrize("excluding", EXCLUDED_ROWS)
@pytest.mark.parametrize("contact_status", ["ready_to_send", "in_sending_tool", "waiting_on_you"])
def test_an_excluded_account_is_never_ready_or_held(excluding, contact_status) -> None:
    r = compute_attrition_receipt([_account(**excluding)], [_contact(contact_status)])
    assert (r.failed_fit, r.ready, r.held) == (1, 0, 0)
    assert r.excluded_listed_contacts == 1
    (problem,) = cross_check(r, {contact_status: 1}, 1)
    assert problem.startswith("Check: 1 contact is on the list for an account marked")


def test_the_same_contact_on_a_live_account_is_ready_and_clean() -> None:
    """Positive control: the check above fires on the exclusion, not on every routed row."""
    r = compute_attrition_receipt([_account()], [_contact("ready_to_send")])
    assert (r.failed_fit, r.ready, r.excluded_listed_contacts) == (0, 1, 0)
    assert cross_check(r, {"ready_to_send": 1}, 1) == []


def test_a_closed_contact_on_an_excluded_account_is_not_a_discrepancy() -> None:
    r = compute_attrition_receipt([_account(status="do-not-contact")], [_contact("not_emailing")])
    assert (r.failed_fit, r.excluded_listed_contacts) == (1, 0)
    assert cross_check(r, {"not_emailing": 1}, 1) == []


def test_the_check_counts_contacts_not_accounts() -> None:
    routed = [
        _contact("ready_to_send"),
        _contact("waiting_on_you", "ira.bloom@contosofreight.example"),
        _contact("not_emailing", "oma.reyes@contosofreight.example"),
    ]
    r = compute_attrition_receipt([_account(status="do-not-contact")], routed)
    assert (r.failed_fit, r.excluded_listed_contacts) == (1, 2)
    (problem,) = cross_check(r, {"ready_to_send": 1, "waiting_on_you": 1, "not_emailing": 1}, 3)
    assert problem.startswith("Check: 2 contacts are on the list for an account marked")


@pytest.mark.parametrize("retired_first", [True, False])
def test_a_retired_status_survives_a_duplicate_ledger_row(retired_first) -> None:
    retired = _account(status="do-not-contact")
    fresh = _account(company="Contoso Freight Pte", contact_email="x@contosofreight.example")
    rows = [retired, fresh] if retired_first else [fresh, retired]
    (merged,) = dedupe_accounts(rows)
    assert merged["status"] == "do-not-contact"
    assert compute_attrition_receipt(rows, []).failed_fit == 1
    assert compute_attrition_receipt(rows, [_contact("ready_to_send")]).ready == 0


def test_a_duplicate_row_still_wins_every_other_field() -> None:
    (merged,) = dedupe_accounts([_account(status="new"), _account(status="contact-resolved")])
    assert merged["status"] == "contact-resolved"


def _seed(tmp_path: Path, monkeypatch, records: list[dict], items: list[dict]) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    evals = tmp_path / PROFILE / "prospects" / "evals"
    evals.mkdir(parents=True)
    (evals / "lanes-state.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    (tmp_path / PROFILE / "prospects" / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": items}), encoding="utf-8"
    )


def test_the_block_says_so_in_the_operators_own_words(tmp_path, monkeypatch, capsys) -> None:
    record = {
        "email": EMAIL,
        "lane": "generic",
        "reason": "stale-clause",
        "company": "Contoso Freight",
        "company_domain": "contosofreight.example",
    }
    _seed(tmp_path, monkeypatch, [record], [_account(status="do-not-contact")])
    assert cli.main(["--profile", PROFILE]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^  Ready\s+0\b", out, re.M)
    assert re.search(r"^  Not a fit / excluded\s+1\b", out, re.M)
    assert re.search(
        r"^Routed — not yet checked\s+1\b", out, re.M
    )  # the contact table is left as routed
    (check,) = [ln for ln in out.splitlines() if ln.startswith("Check:")]
    assert "1 contact is on the list for an account marked not a fit / excluded" in check

    lint_dir = Path(__file__).resolve().parents[1] / "lint"
    if str(lint_dir) not in sys.path:
        sys.path.insert(0, str(lint_dir))
    import operator_vocabulary as ov

    assert [h for h in ov.findings(text=out) if h[0] == "<text>"] == []

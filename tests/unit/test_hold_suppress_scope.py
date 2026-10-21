"""A hold sheet's "suppress" does what its copy told the operator — no more.

For a second contact at an account the sheet reads "one person per account — drop this one".
Until 2026-09-24 `lanes hold-apply` retired the whole ACCOUNT for every suppress, so dropping a
duplicate contact silently removed the colleague who was staying on the list: the next list
build excludes a disqualified account's every row, and the gate refuses them. Now only the
questions whose copy promised it retire the account (`ACCOUNT_SCOPED_SUPPRESS`); every other
suppress puts the one person on the suppression ledger and leaves their company alone.
Fictional fixtures only (§R9).
"""

from __future__ import annotations

import json

import pytest

from gtm_core.lanes import decisions as dec
from gtm_core.lanes.model import ACCOUNT_SCOPED_SUPPRESS, HOLD_QUESTION, QUESTION_COPY
from gtm_core.prospects_state import load_latest

PROFILE = "acme"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    ledger = tmp_path / PROFILE / "prospects" / "latest.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "kind": "prospects",
                "items": [
                    {"company": "Brightpath", "domain": "brightpath.example", "status": "new"},
                    {"company": "Copperfield", "domain": "copperfield.example", "status": "new"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _suppress(email: str, domain: str, trigger: str) -> dec.DecisionEntry:
    return dec.DecisionEntry(
        email=email,
        trigger=trigger,
        account_key=f"d:{domain}",
        decision="suppress",
        company=domain.split(".")[0].title(),
        company_domain=domain,
    )


def _status(root, domain: str) -> str:
    items = load_latest(PROFILE, root)["items"]
    return next(i for i in items if i.get("domain") == domain)["status"]


def test_dropping_a_duplicate_contact_leaves_the_account_on_the_list(root):
    plan = dec.plan_apply(
        [
            _suppress("second@brightpath.example", "brightpath.example", "duplicate-contact"),
            _suppress("anyone@copperfield.example", "copperfield.example", "researcher-drop"),
        ],
        {},
    )
    assert "suppress 2 contact(s), retiring 1 account(s)" in plan.render()
    dec.apply(plan, PROFILE, "2026-09-24", root)
    assert _status(root, "brightpath.example") == "new", "the colleague's account must stay"
    assert _status(root, "copperfield.example") == "disqualified"


def test_the_suppressed_person_is_on_the_ledger_either_way(root):
    plan = dec.plan_apply(
        [_suppress("second@brightpath.example", "brightpath.example", "duplicate-contact")], {}
    )
    dec.apply(plan, PROFILE, "2026-09-24", root)
    ledger = (root / PROFILE / "prospects").rglob("*suppress*")
    text = "".join(p.read_text(encoding="utf-8") for p in ledger if p.is_file())
    assert "second@brightpath.example" in text


def test_every_account_scoped_question_says_so_in_its_own_copy():
    """The closed set follows the words the operator reads; a question whose copy speaks of
    one person, or of not losing the account, is never in it."""
    for question in ACCOUNT_SCOPED_SUPPRESS:
        assert question in QUESTION_COPY
    for question, (_title, options) in QUESTION_COPY.items():
        words = options.get("suppress", "")
        if "one person" in words or "do not lose the account" in words:
            assert question not in ACCOUNT_SCOPED_SUPPRESS, question


def test_an_unknown_trigger_drops_only_the_person():
    """Fail safe for the funnel: an unmapped trigger never retires an account."""
    assert "a-trigger-from-another-build" not in HOLD_QUESTION
    entry = _suppress("x@brightpath.example", "brightpath.example", "a-trigger-from-another-build")
    assert dec.retires_account(entry) is False
